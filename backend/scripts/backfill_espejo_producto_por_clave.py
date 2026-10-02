"""Pone producto a las partidas del espejo SAE que llegaron sin él.

Con la MISMA regla que `POST /facturas/espejo` (services/espejo_productos.py):
no hay una copia en SQL que se pueda separar del endpoint. Medido el 2-oct-2026
(solo lectura): 81,427 partidas espejo sin producto, el 61%; ZEHMOVH 22,758 de
38,281. La previsualización verificada esperaba ~79.9k ligadas y ~1.5k que se
quedan (huérfanas sin producto con esa clave + las 560 de CALABAZACASTILKG en la
03, que ahora sí van a CALABAZA CRIOLLA 00010233 por la tabla de claves
distintas por empresa).

CORRERLO DESPUÉS DEL DEPLOY del endpoint. El espejo corre dentro del backend
cada 30 s y cada reenvío (nuevas, cancelaciones, abonos, cuadre) borra y recrea
las partidas de la factura: con el endpoint viejo, lo que esto ligue se
volvería a perder en el siguiente reenvío.

DOS PASOS, el mismo recorrido (todas las facturas ESPEJO_SAE del inquilino, por
lotes):

  1. Partidas con producto_id NULL y clave: producto, presentación, unidad SAT
     y clave SAT — exactamente lo que el endpoint les escribiría hoy.
  2. Partidas YA ligadas a las que el endpoint dejaría EL MISMO producto pero
     con otra unidad SAT: la clave es la de una presentación con otra unidad
     (PAPAYAMARADOLKG ligada con H87: 2,948 partidas; MELONCHPZ con KGM: 242…;
     ~3,669 en el paso 2b de la verificación). Solo cambia clave_unidad (y la
     presentación, si no tenía). Mini Conta cuenta kilos/piezas con ella.

Y un INFORMATIVO que no toca nada: partidas ya ligadas a un producto distinto
del que da la clave (las ligó el código del cliente: AJOKG en 00000284 AJO
cuando la clave es de 00010472 AJO KG…; ~4.9k). El endpoint las cambia cuando
la factura se vuelva a mandar; aquí solo se cuentan para que el dueño decida.

Idempotente: el paso 1 solo escribe donde producto_id sigue NULL y el 2 solo
donde el producto no cambió y la unidad difiere; una segunda corrida no hace
nada. Cada lote es su propia transacción corta con lock_timeout de 5 s: si el
espejo tiene la factura tomada, ese lote se salta (se cuenta) y la siguiente
corrida lo recoge.

Uso (desde backend/):

    ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave             # --dry-run: solo cuenta
    ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave --aplicar

La base sale de DATABASE_URL o, si no está, del ALEMBIC_DB_URL de --env (como
los demás scripts). Corre como postgres: el filtro por inquilino va explícito.

Después: en Mini Conta re-sincronizar las ventas de Kelly Tabasco y Kelly
Chiapas por rangos (su copia `ventas_facturadas` no cambia sola) y mudar las 33
ligas «DESC:…» de Chiapas a su sku.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

ENV = "/Users/michelzarate/Documents/Claude/Facturador/.env.prod"
TENANT = "cristian-gerardo-zarate-orozco"
LOTE = 300          # facturas por transacción


def _url_de(env_path: str) -> str:
    try:
        lineas = open(env_path, encoding="utf-8").read().splitlines()
    except OSError as e:
        sys.exit(f"sin DATABASE_URL y no se pudo leer {env_path}: {e}")
    for linea in lineas:
        if linea.startswith("ALEMBIC_DB_URL="):
            return linea.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"sin ALEMBIC_DB_URL en {env_path}")


def _trozos(xs: list, n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


_SQL_PASO_1 = """
update lineas_factura lf
   set producto_id = v.pid::uuid,
       presentacion = v.pres::varchar,
       clave_unidad = v.uni::varchar,
       clave_prod_serv = v.cps::varchar
  from (values %s) as v(id, tid, pid, pres, uni, cps)
 where lf.id = v.id::uuid
   and lf.tenant_id = v.tid::uuid
   and lf.producto_id is null
"""

_SQL_PASO_2 = """
update lineas_factura lf
   set clave_unidad = v.uni::varchar,
       presentacion = coalesce(lf.presentacion, v.pres::varchar)
  from (values %s) as v(id, tid, pid, pres, uni)
 where lf.id = v.id::uuid
   and lf.tenant_id = v.tid::uuid
   and lf.producto_id = v.pid::uuid
   and lf.clave_unidad is distinct from v.uni::varchar
"""


def correr(db, tenant_id, *, aplicar: bool = False, lote: int = LOTE,
           unidades: bool = True, espera_candado: str = "5s", salida=print) -> dict:
    """Recorre las facturas espejo del inquilino y devuelve los conteos.

    `aplicar=False` no escribe nada. Las importaciones van aquí adentro para
    que `main` pueda fijar DATABASE_URL antes de que la app lea su config."""
    import psycopg2
    from psycopg2.extras import execute_values
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.models import Factura, LineaFactura, Producto
    from app.services.espejo_productos import (
        FuentesEnLote,
        campos_de_linea,
        norm_clave_sae,
        resolver_claves,
    )

    fuentes = FuentesEnLote(db, tenant_id)
    fuentes.fijar_uso()

    factura_ids = [fid for (fid,) in (
        db.query(Factura.id)
        .filter(Factura.tenant_id == tenant_id,
                Factura.origen == "ESPEJO_SAE",          # nunca partidas nativas
                Factura.deleted_at.is_(None))
        .order_by(Factura.id)
        .all()
    )]

    c = {
        "facturas": len(factura_ids),
        "paso1_por_regla": Counter(), "paso1_por_serie_regla": Counter(),
        "paso1_debiles": Counter(), "paso1_unidad": Counter(),
        "paso1_sin_producto_claves": Counter(),
        "paso1_escritas": 0,
        "paso2_por_serie": Counter(), "paso2_unidad": Counter(), "paso2_claves": Counter(),
        "paso2_escritas": 0,
        "info_cambiaria": Counter(), "info_cambiaria_claves": Counter(),
        "info_sin_producto": 0,
        "lotes_saltados": 0,
    }

    for trozo in _trozos(factura_ids, max(1, lote)):
        facturas = (
            db.query(Factura.id, Factura.cliente_id, Factura.serie, Factura.espejo_empresa)
            .filter(Factura.id.in_(trozo))
            .all()
        )
        lineas = (
            db.query(LineaFactura.id, LineaFactura.factura_id, LineaFactura.clave_sae,
                     LineaFactura.producto_id, LineaFactura.clave_unidad,
                     LineaFactura.presentacion)
            .filter(LineaFactura.tenant_id == tenant_id, LineaFactura.factura_id.in_(trozo))
            .all()
        )
        fuentes.precargar(trozo, [(ln.factura_id, ln.clave_sae, ln.producto_id) for ln in lineas])
        por_factura: dict = defaultdict(list)
        for ln in lineas:
            if (ln.clave_sae or "").strip():
                por_factura[ln.factura_id].append(ln)

        decididas = []          # (linea, resolucion, serie)
        for f in facturas:
            suyas = por_factura.get(f.id)
            if not suyas:
                continue
            res = resolver_claves(
                fuentes, factura_id=f.id, cliente_id=f.cliente_id,
                empresa=f.espejo_empresa, serie=f.serie,
                claves=[ln.clave_sae for ln in suyas],
            )
            for ln in suyas:
                decididas.append((ln, res.get(norm_clave_sae(ln.clave_sae)), f.serie))

        pids = {r.producto_id for _, r, _ in decididas if r and r.producto_id}
        prods = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(pids))} if pids else {}

        paso1, paso2 = [], []
        for ln, r, serie in decididas:
            clave = norm_clave_sae(ln.clave_sae)
            campos = campos_de_linea(r, prods.get(r.producto_id) if r and r.producto_id else None)
            if ln.producto_id is None:
                regla = r.regla if r else "X_sin_producto"
                c["paso1_por_regla"][regla] += 1
                c["paso1_por_serie_regla"][(serie, regla)] += 1
                if r and r.debil:
                    c["paso1_debiles"][regla] += 1
                if campos["producto_id"] is None:
                    c["paso1_sin_producto_claves"][(serie, clave)] += 1
                    continue
                c["paso1_unidad"][campos["clave_unidad"]] += 1
                paso1.append((str(ln.id), str(tenant_id), str(campos["producto_id"]),
                              campos["presentacion"], campos["clave_unidad"],
                              campos["clave_prod_serv"]))
            elif campos["producto_id"] == ln.producto_id:
                if unidades and campos["clave_unidad"] != ln.clave_unidad:
                    c["paso2_por_serie"][serie] += 1
                    c["paso2_unidad"][f"{ln.clave_unidad}→{campos['clave_unidad']}"] += 1
                    c["paso2_claves"][clave] += 1
                    paso2.append((str(ln.id), str(tenant_id), str(ln.producto_id),
                                  campos["presentacion"], campos["clave_unidad"]))
            else:
                # Ya ligada a OTRO producto: el endpoint la cambiará en el
                # siguiente reenvío de esa factura. Aquí no se toca.
                c["info_cambiaria"][serie] += 1
                c["info_cambiaria_claves"][clave] += 1
                if campos["producto_id"] is None:
                    c["info_sin_producto"] += 1

        if aplicar and (paso1 or paso2):
            escritas1 = escritas2 = 0
            try:
                db.execute(text("SELECT set_config('lock_timeout', :t, true)"),
                           {"t": espera_candado})
                db.execute(text("SET LOCAL statement_timeout = '120s'"))
                # Un UPDATE … FROM (VALUES …) por paso y por lote: uno por
                # partida serían decenas de miles de viajes al pooler.
                with db.connection().connection.cursor() as cur:
                    if paso1:
                        execute_values(cur, _SQL_PASO_1, paso1, page_size=len(paso1))
                        escritas1 = max(cur.rowcount, 0)
                    if paso2:
                        execute_values(cur, _SQL_PASO_2, paso2, page_size=len(paso2))
                        escritas2 = max(cur.rowcount, 0)
                db.commit()
                c["paso1_escritas"] += escritas1
                c["paso2_escritas"] += escritas2
            except (OperationalError, psycopg2.OperationalError) as e:
                # lock_timeout / statement_timeout: el espejo tiene tomada alguna
                # de esas facturas. Se suelta el lote entero; es idempotente.
                db.rollback()
                c["lotes_saltados"] += 1
                causa = type(getattr(e, "orig", None) or e).__name__
                salida(f"  ! lote saltado ({causa}): la siguiente corrida lo recoge")
        else:
            db.rollback()     # sin transacciones largas abiertas entre lotes

    _reporte(c, aplicar=aplicar, unidades=unidades, salida=salida)
    return c


def _reporte(c: dict, *, aplicar: bool, unidades: bool, salida) -> None:
    modo = "APLICADO" if aplicar else "DRY-RUN (no se escribió nada)"
    salida(f"\n══ Backfill espejo → producto por clave SAE — {modo}")
    salida(f"facturas espejo recorridas: {c['facturas']:,}")

    total1 = sum(c["paso1_por_regla"].values())
    salida(f"\n── Paso 1: partidas sin producto: {total1:,}")
    for regla, n in sorted(c["paso1_por_regla"].items()):
        deb = c["paso1_debiles"].get(regla, 0)
        salida(f"  {regla:<34} {n:>8,}" + (f"   ({deb:,} débiles)" if deb else ""))
    salida("  por serie:")
    series = sorted({s for s, _ in c["paso1_por_serie_regla"]})
    for s in series:
        partes = [f"{r}={n:,}" for (ss, r), n in sorted(c["paso1_por_serie_regla"].items()) if ss == s]
        salida(f"    {s:<10} " + "  ".join(partes))
    salida("  unidad que queda: " + ", ".join(f"{u}={n:,}" for u, n in c["paso1_unidad"].most_common()))
    if c["paso1_sin_producto_claves"]:
        salida("  claves que se quedan sin producto (top 15):")
        for (s, k), n in c["paso1_sin_producto_claves"].most_common(15):
            salida(f"    {s:<10} {k:<22} {n:>6,}")
    if aplicar:
        salida(f"  → escritas: {c['paso1_escritas']:,}")

    if unidades:
        total2 = sum(c["paso2_por_serie"].values())
        salida(f"\n── Paso 2: ya ligadas con otra unidad SAT: {total2:,}")
        salida("  " + ", ".join(f"{s}={n:,}" for s, n in c["paso2_por_serie"].most_common()))
        salida("  " + ", ".join(f"{u}={n:,}" for u, n in c["paso2_unidad"].most_common()))
        for k, n in c["paso2_claves"].most_common(10):
            salida(f"    {k:<22} {n:>6,}")
        if aplicar:
            salida(f"  → escritas: {c['paso2_escritas']:,}")

    total3 = sum(c["info_cambiaria"].values())
    salida(f"\n── Informativo (NO se toca): ligadas a otro producto que el de su clave: {total3:,}")
    salida("  el endpoint las cambia en el siguiente reenvío de su factura"
           f" ({c['info_sin_producto']:,} se quedarían sin producto)")
    salida("  " + ", ".join(f"{s}={n:,}" for s, n in c["info_cambiaria"].most_common()))
    for k, n in c["info_cambiaria_claves"].most_common(10):
        salida(f"    {k:<22} {n:>6,}")
    if c["lotes_saltados"]:
        salida(f"\n!! {c['lotes_saltados']} lote(s) saltados por candado: vuelve a correrlo")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Producto por clave SAE en las partidas del espejo (misma regla que el endpoint).")
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", dest="aplicar", action="store_false",
                      help="solo cuenta (por defecto)")
    modo.add_argument("--aplicar", dest="aplicar", action="store_true",
                      help="escribe, en lotes cortos")
    ap.set_defaults(aplicar=False)
    ap.add_argument("--env", default=ENV, help="archivo con ALEMBIC_DB_URL si no hay DATABASE_URL")
    ap.add_argument("--tenant", default=TENANT, help="slug del inquilino")
    ap.add_argument("--lote", type=int, default=LOTE, help="facturas por transacción")
    ap.add_argument("--sin-unidades", action="store_true",
                    help="no corre el paso 2 (unidad de las ya ligadas)")
    args = ap.parse_args(argv)

    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = _url_de(args.env)

    from app.core.db import SessionLocal
    from app.models import Tenant

    db = SessionLocal()
    try:
        tid = db.query(Tenant.id).filter(Tenant.slug == args.tenant).scalar()
        if tid is None:
            sys.exit(f"no existe el inquilino {args.tenant!r}")
        correr(db, tid, aplicar=args.aplicar, lote=args.lote, unidades=not args.sin_unidades)
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
