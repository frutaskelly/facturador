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

TRES PASOS, el mismo recorrido (todas las facturas ESPEJO_SAE del inquilino, por
lotes):

  1. Partidas con producto_id NULL y clave: producto, presentación, unidad SAT
     y clave SAT — exactamente lo que el endpoint les escribiría hoy.
  2. Partidas YA ligadas a las que el endpoint dejaría EL MISMO producto pero
     con otra unidad SAT: la clave es la de una presentación con otra unidad
     (PAPAYAMARADOLKG ligada con H87: 2,948 partidas; MELONCHPZ con KGM: 242…;
     ~3,669 en el paso 2b de la verificación). Solo cambia clave_unidad (y la
     presentación, si no tenía). Mini Conta cuenta kilos/piezas con ella.
  3. Partidas YA ligadas a otro producto que el que da la regla de hoy SIN la
     decisión previa (las ligó el código del cliente: AJOKG en 00000284 AJO
     cuando la clave es de 00010472 AJO KG, ~4.9k; o el catálogo cambió
     después). El endpoint NO las cambia: una partida ligada conserva su
     producto en cada reenvío, salvo que una remisión ligada diga otra cosa.
     Siempre se listan; solo con --recalcular-ligadas se re-apuntan, y solo
     las de decisión fuerte (con producto y no débiles). Es la vía a propósito
     para que una corrección del dueño (dar de alta el gemelo correcto en el
     catálogo del cliente, una fila nueva en la tabla de claves) llegue a lo
     ya reflejado.

EL USO es el de las REMISIONES (como en el endpoint): lo que este script liga no
lo mueve, así que la foto que toma al empezar es la misma que el endpoint verá
al terminar. Las decisiones por uso que tome quedan guardadas en la partida
(la decisión previa): un reenvío no las voltea aunque el uso cambie después.

El dry-run imprime, además de los conteos, QUIÉN GANA cada clave gemela en cada
serie (sku, regla, partidas, si fue débil y los que perdieron) y marca ⚠ cuando
el artículo del SAE se llama como un perdedor y no como el ganador: esa es la
lista que el dueño revisa antes de --aplicar. --csv la guarda completa.

Idempotente: el paso 1 solo escribe donde producto_id sigue NULL, el 2 solo
donde el producto no cambió y la unidad difiere, y el 3 solo donde la partida
sigue con el producto que se leyó; una segunda corrida no hace nada. Cada lote
es su propia transacción corta con lock_timeout de 5 s: si el espejo tiene la
factura tomada, ese lote se salta (se cuenta) y la siguiente corrida lo recoge.

Uso (desde backend/):

    ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave             # --dry-run: solo cuenta y lista
    ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave --csv /tmp/espejo.csv
    ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave --aplicar   # pasos 1 y 2
    ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave --aplicar --recalcular-ligadas   # y el 3

La base sale de DATABASE_URL o, si no está, del ALEMBIC_DB_URL de --env (como
los demás scripts); ESPEJO_SAE_TENANT_ID igual — sin él la tabla de claves
distintas no aplica y el script se detiene. Corre como postgres: el filtro por
inquilino va explícito.

Después: en Mini Conta re-sincronizar las ventas de Kelly Tabasco y Kelly
Chiapas por rangos (su copia `ventas_facturadas` no cambia sola) y mudar las 33
ligas «DESC:…» de Chiapas a su sku.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import unicodedata
from collections import Counter, defaultdict

ENV = "/Users/michelzarate/Documents/Claude/Facturador/.env.prod"
TENANT = "cristian-gerardo-zarate-orozco"
LOTE = 300          # facturas por transacción


def _de_env(env_path: str, llave: str) -> str:
    try:
        lineas = open(env_path, encoding="utf-8").read().splitlines()
    except OSError as e:
        sys.exit(f"sin {llave} en el ambiente y no se pudo leer {env_path}: {e}")
    for linea in lineas:
        if linea.startswith(f"{llave}="):
            return linea.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"sin {llave} en {env_path}")


def _trozos(xs: list, n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# Palabras de unidad que el SAE pega al nombre del artículo («CALABAZA
# CRIOLLA KG»): no cuentan para comparar nombres.
_UNIDADES = {"KG", "KGS", "KILO", "KILOS", "PZ", "PZA", "PZAS", "PIEZA", "PIEZAS",
             "MJ", "MANOJO", "MAZO", "LT", "LTS", "LITRO", "GR", "GRS", "G"}


def _nombre(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode("ascii")
    s = "".join(ch if ch.isalnum() else " " for ch in s.upper())
    return " ".join(t for t in s.split() if t not in _UNIDADES)


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

_SQL_PASO_3 = """
update lineas_factura lf
   set producto_id = v.pid::uuid,
       presentacion = v.pres::varchar,
       clave_unidad = v.uni::varchar,
       clave_prod_serv = v.cps::varchar
  from (values %s) as v(id, tid, viejo, pid, pres, uni, cps)
 where lf.id = v.id::uuid
   and lf.tenant_id = v.tid::uuid
   and lf.producto_id = v.viejo::uuid
"""


def correr(db, tenant_id, *, aplicar: bool = False, recalcular: bool = False,
           lote: int = LOTE, unidades: bool = True, espera_candado: str = "5s",
           salida=print, csv_path: str | None = None) -> dict:
    """Recorre las facturas espejo del inquilino y devuelve los conteos.

    `aplicar=False` no escribe nada; `recalcular` (con `aplicar`) también
    escribe el paso 3. Las importaciones van aquí adentro para que `main`
    pueda fijar el ambiente antes de que la app lea su config."""
    import psycopg2
    from psycopg2.extras import execute_values
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.core.rbac import es_duenio_de_sae
    from app.models import Factura, LineaFactura, Producto
    from app.services.espejo_productos import (
        SIN_PRODUCTO,
        FuentesEnLote,
        campos_de_linea,
        norm_clave_sae,
        resolver_claves,
    )

    fuentes = FuentesEnLote(db, tenant_id)
    fuentes.fijar_uso()
    indice = fuentes.indice()

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
        "duenio_de_sae": es_duenio_de_sae(tenant_id),
        "paso1_por_regla": Counter(), "paso1_por_serie_regla": Counter(),
        "paso1_debiles": Counter(), "paso1_unidad": Counter(),
        "paso1_sin_producto_claves": Counter(),
        "paso1_escritas": 0,
        # (serie, clave, ganador, regla) → partidas, de las claves que se disputaron
        "gemelos": Counter(), "gemelos_debil": set(), "gemelos_rivales": defaultdict(set),
        "paso2_por_serie": Counter(), "paso2_unidad": Counter(), "paso2_claves": Counter(),
        "paso2_escritas": 0,
        # (serie, clave, viejo, nuevo, regla) → partidas
        "paso3": Counter(), "paso3_debil": set(), "paso3_solo": set(), "paso3_aplicables": 0,
        "paso3_sin_producto": 0, "paso3_escritas": 0,
        "lotes_saltados": 0,
    }
    desc_sae: dict = {}                     # (serie, clave) → descripción del SAE

    for trozo in _trozos(factura_ids, max(1, lote)):
        facturas = (
            db.query(Factura.id, Factura.cliente_id, Factura.serie, Factura.espejo_empresa)
            .filter(Factura.id.in_(trozo))
            .all()
        )
        lineas = (
            db.query(LineaFactura.id, LineaFactura.factura_id, LineaFactura.clave_sae,
                     LineaFactura.producto_id, LineaFactura.clave_unidad,
                     LineaFactura.presentacion, LineaFactura.descripcion)
            .filter(LineaFactura.tenant_id == tenant_id, LineaFactura.factura_id.in_(trozo))
            .all()
        )
        fuentes.precargar(trozo, [(ln.factura_id, ln.clave_sae, ln.producto_id) for ln in lineas])
        por_factura: dict = defaultdict(list)
        for ln in lineas:
            if (ln.clave_sae or "").strip():
                por_factura[ln.factura_id].append(ln)

        decididas = []          # (linea, con previa, sin previa, serie)
        for f in facturas:
            suyas = por_factura.get(f.id)
            if not suyas:
                continue
            kw = dict(factura_id=f.id, cliente_id=f.cliente_id, empresa=f.espejo_empresa,
                      serie=f.serie, claves=[ln.clave_sae for ln in suyas])
            res = resolver_claves(fuentes, **kw)
            # Lo que la regla dice HOY sin lo que la factura ya tenía: solo hace
            # falta si alguna partida ya está ligada.
            res0 = resolver_claves(fuentes, con_previa=False, **kw) \
                if any(ln.producto_id for ln in suyas) else res
            for ln in suyas:
                k = norm_clave_sae(ln.clave_sae)
                decididas.append((ln, res.get(k), res0.get(k), f.serie))

        pids = set()
        for _, r, r0, _ in decididas:
            pids |= {x.producto_id for x in (r, r0) if x and x.producto_id}
        prods = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(pids))} if pids else {}

        def _campos(r):
            return campos_de_linea(r, prods.get(r.producto_id) if r and r.producto_id else None)

        paso1, paso2, paso3 = [], [], []
        for ln, r, r0, serie in decididas:
            clave = norm_clave_sae(ln.clave_sae)
            desc_sae.setdefault((serie, clave), ln.descripcion)
            if ln.producto_id is None:
                campos = _campos(r)
                regla = r.regla if r else SIN_PRODUCTO
                c["paso1_por_regla"][regla] += 1
                c["paso1_por_serie_regla"][(serie, regla)] += 1
                if r and r.debil:
                    c["paso1_debiles"][regla] += 1
                if campos["producto_id"] is None:
                    c["paso1_sin_producto_claves"][(serie, clave)] += 1
                    continue
                if len(r.competidores) > 1:
                    llave = (serie, clave, r.producto_id, regla)
                    c["gemelos"][llave] += 1
                    c["gemelos_rivales"][llave].update(r.competidores)
                    if r.debil:
                        c["gemelos_debil"].add(llave)
                c["paso1_unidad"][campos["clave_unidad"]] += 1
                paso1.append((str(ln.id), str(tenant_id), str(campos["producto_id"]),
                              campos["presentacion"], campos["clave_unidad"],
                              campos["clave_prod_serv"]))
                continue

            campos = _campos(r)
            if campos["producto_id"] == ln.producto_id and unidades \
                    and campos["clave_unidad"] != ln.clave_unidad:
                c["paso2_por_serie"][serie] += 1
                c["paso2_unidad"][f"{ln.clave_unidad}→{campos['clave_unidad']}"] += 1
                c["paso2_claves"][clave] += 1
                paso2.append((str(ln.id), str(tenant_id), str(ln.producto_id),
                              campos["presentacion"], campos["clave_unidad"]))

            nuevo = _campos(r0)
            if nuevo["producto_id"] == ln.producto_id:
                continue
            regla0 = r0.regla if r0 else SIN_PRODUCTO
            # «solo»: el endpoint también la mueve en el siguiente reenvío (la
            # remisión ligada dice otra cosa, o la factura traía dos productos
            # en esa clave); las demás se quedan como están hasta --recalcular.
            solo = campos["producto_id"] != ln.producto_id
            llave = (serie, clave, ln.producto_id, nuevo["producto_id"], regla0)
            c["paso3"][llave] += 1
            if solo:
                c["paso3_solo"].add(llave)
            if nuevo["producto_id"] is None:
                c["paso3_sin_producto"] += 1      # nunca se desliga: se queda como está
                continue
            if r0.debil:
                c["paso3_debil"].add(llave)
                continue
            c["paso3_aplicables"] += 1
            paso3.append((str(ln.id), str(tenant_id), str(ln.producto_id),
                          str(nuevo["producto_id"]), nuevo["presentacion"],
                          nuevo["clave_unidad"], nuevo["clave_prod_serv"]))

        if not recalcular:
            paso3 = []
        if aplicar and (paso1 or paso2 or paso3):
            escritas = [0, 0, 0]
            try:
                db.execute(text("SELECT set_config('lock_timeout', :t, true)"),
                           {"t": espera_candado})
                db.execute(text("SET LOCAL statement_timeout = '120s'"))
                # Un UPDATE … FROM (VALUES …) por paso y por lote: uno por
                # partida serían decenas de miles de viajes al pooler. El 2 va
                # antes que el 3: los dos leen el producto viejo.
                with db.connection().connection.cursor() as cur:
                    for i, (sql, filas) in enumerate(
                            ((_SQL_PASO_1, paso1), (_SQL_PASO_2, paso2), (_SQL_PASO_3, paso3))):
                        if filas:
                            execute_values(cur, sql, filas, page_size=len(filas))
                            escritas[i] = max(cur.rowcount, 0)
                db.commit()
                c["paso1_escritas"] += escritas[0]
                c["paso2_escritas"] += escritas[1]
                c["paso3_escritas"] += escritas[2]
            except (OperationalError, psycopg2.OperationalError) as e:
                # lock_timeout / statement_timeout: el espejo tiene tomada alguna
                # de esas facturas. Se suelta el lote entero; es idempotente.
                db.rollback()
                c["lotes_saltados"] += 1
                causa = type(getattr(e, "orig", None) or e).__name__
                salida(f"  ! lote saltado ({causa}): la siguiente corrida lo recoge")
        else:
            db.rollback()     # sin transacciones largas abiertas entre lotes

    filas_gemelos = _tabla_gemelos(c, indice, desc_sae)
    filas_paso3 = _tabla_paso3(c, indice, desc_sae)
    c["tabla_gemelos"], c["tabla_paso3"] = filas_gemelos, filas_paso3
    _reporte(c, aplicar=aplicar, recalcular=recalcular, unidades=unidades, salida=salida)
    if csv_path:
        _csv(csv_path, filas_gemelos, filas_paso3)
        salida(f"\nlista completa en {csv_path}")
    return c


def _marca_nombre(indice, desc, ganador, rivales) -> str:
    """⚠ cuando el artículo del SAE se llama como un perdedor y no como el
    ganador (TOMATEVERDELIMKG → 00010048 «TOMATE VERDE GRANDE Y LIMPIO»
    habiendo un 00010049 «TOMATE VERDE LIMPIO»)."""
    d = _nombre(desc)
    if not d or _nombre(indice.nombre.get(ganador)) == d:
        return ""
    igual = [indice.sku.get(p, "?") for p in rivales
             if p != ganador and _nombre(indice.nombre.get(p)) == d]
    return f"⚠ el SAE la llama como {', '.join(sorted(igual))}" if igual else ""


def _tabla_gemelos(c, indice, desc_sae) -> list[dict]:
    filas = []
    for (serie, clave, pid, regla), n in c["gemelos"].items():
        rivales = c["gemelos_rivales"][(serie, clave, pid, regla)] - {pid}
        desc = desc_sae.get((serie, clave))
        filas.append({
            "serie": serie, "clave": clave, "sae": desc or "",
            "sku": indice.sku.get(pid, "?"), "nombre": indice.nombre.get(pid, ""),
            "regla": regla, "partidas": n,
            "debil": (serie, clave, pid, regla) in c["gemelos_debil"],
            "perdedores": " | ".join(sorted(f"{indice.sku.get(p, '?')} {indice.nombre.get(p, '')}"
                                            for p in rivales)),
            "marca": _marca_nombre(indice, desc, pid, rivales),
        })
    filas.sort(key=lambda f: (-f["partidas"], f["serie"], f["clave"]))
    return filas


def _tabla_paso3(c, indice, desc_sae) -> list[dict]:
    filas = []
    for (serie, clave, viejo, nuevo, regla), n in c["paso3"].items():
        desc = desc_sae.get((serie, clave))
        filas.append({
            "serie": serie, "clave": clave, "sae": desc or "",
            "de_sku": indice.sku.get(viejo, "?"), "de_nombre": indice.nombre.get(viejo, ""),
            "a_sku": indice.sku.get(nuevo, "—") if nuevo else "—",
            "a_nombre": indice.nombre.get(nuevo, "") if nuevo else "(sin producto)",
            "regla": regla, "partidas": n,
            "debil": (serie, clave, viejo, nuevo, regla) in c["paso3_debil"],
            "solo": (serie, clave, viejo, nuevo, regla) in c["paso3_solo"],
            "marca": _marca_nombre(indice, desc, nuevo, {viejo}) if nuevo else "",
        })
    filas.sort(key=lambda f: (-f["partidas"], f["serie"], f["clave"]))
    return filas


def _csv(ruta: str, gemelos: list[dict], paso3: list[dict]) -> None:
    campos = ["tabla", "serie", "clave", "sae", "sku", "nombre", "de_sku", "de_nombre",
              "a_sku", "a_nombre", "regla", "partidas", "debil", "perdedores", "solo", "marca"]
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for f in gemelos:
            w.writerow({"tabla": "gemelos_paso1", **f})
        for f in paso3:
            w.writerow({"tabla": "ligadas_paso3", **f})


def _reporte(c: dict, *, aplicar: bool, recalcular: bool, unidades: bool, salida) -> None:
    modo = "APLICADO" if aplicar else "DRY-RUN (no se escribió nada)"
    salida(f"\n══ Backfill espejo → producto por clave SAE — {modo}")
    salida(f"facturas espejo recorridas: {c['facturas']:,}")
    if not c["duenio_de_sae"]:
        salida("  (este inquilino no es ESPEJO_SAE_TENANT_ID: la tabla de claves distintas no aplica)")

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
    if c["tabla_gemelos"]:
        salida(f"\n  Claves disputadas — quién gana en cada serie ({len(c['tabla_gemelos']):,} filas;"
               " «débil» = el uso decidió por poco o decidió el sku):")
        for f in c["tabla_gemelos"]:
            salida(f"    {f['serie']:<10} {f['clave']:<20} → {f['sku']} {f['nombre'][:32]:<32}"
                   f" {f['regla']:<20} {f['partidas']:>6,}{'  débil' if f['debil'] else ''}"
                   f"   pierden: {f['perdedores'][:70]}"
                   + (f"   {f['marca']}" if f["marca"] else ""))
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

    total3 = sum(c["paso3"].values())
    salida(f"\n── Paso 3: ligadas a otro producto que el que da la regla de hoy: {total3:,}")
    salida("  el endpoint NO las cambia (salvo las «solo»: la remisión ligada las mueve en el"
           " siguiente reenvío);")
    salida(f"  con --recalcular-ligadas se re-apuntan {c['paso3_aplicables']:,}"
           f" (las débiles y las {c['paso3_sin_producto']:,} que quedarían sin producto, no)")
    for f in c["tabla_paso3"]:
        salida(f"    {f['serie']:<10} {f['clave']:<20} {f['de_sku']} {f['de_nombre'][:24]:<24}"
               f" → {f['a_sku']} {f['a_nombre'][:30]:<30} {f['regla']:<20} {f['partidas']:>6,}"
               f"{'  débil' if f['debil'] else ''}{'  solo' if f['solo'] else ''}"
               + (f"   {f['marca']}" if f["marca"] else ""))
    if aplicar and recalcular:
        salida(f"  → re-apuntadas: {c['paso3_escritas']:,}")
    if c["lotes_saltados"]:
        salida(f"\n!! {c['lotes_saltados']} lote(s) saltados por candado: vuelve a correrlo")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Producto por clave SAE en las partidas del espejo (misma regla que el endpoint).")
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", dest="aplicar", action="store_false",
                      help="solo cuenta y lista (por defecto)")
    modo.add_argument("--aplicar", dest="aplicar", action="store_true",
                      help="escribe los pasos 1 y 2, en lotes cortos")
    ap.set_defaults(aplicar=False)
    ap.add_argument("--recalcular-ligadas", action="store_true",
                    help="con --aplicar: también re-apunta las ya ligadas del paso 3 (decisión fuerte)")
    ap.add_argument("--csv", default=None, help="guarda las tablas de gemelos y del paso 3")
    ap.add_argument("--env", default=ENV,
                    help="archivo con ALEMBIC_DB_URL y ESPEJO_SAE_TENANT_ID si no están en el ambiente")
    ap.add_argument("--tenant", default=TENANT, help="slug del inquilino")
    ap.add_argument("--lote", type=int, default=LOTE, help="facturas por transacción")
    ap.add_argument("--sin-unidades", action="store_true",
                    help="no corre el paso 2 (unidad de las ya ligadas)")
    args = ap.parse_args(argv)
    if args.recalcular_ligadas and not args.aplicar:
        ap.error("--recalcular-ligadas va con --aplicar (sin él, el dry-run ya lista el paso 3)")

    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = _de_env(args.env, "ALEMBIC_DB_URL")
    # La tabla de claves distintas solo aplica en el inquilino dueño del SAE,
    # igual que en el endpoint: sin la variable, el script decidiría distinto.
    if not os.environ.get("ESPEJO_SAE_TENANT_ID"):
        os.environ["ESPEJO_SAE_TENANT_ID"] = _de_env(args.env, "ESPEJO_SAE_TENANT_ID")

    from app.core.db import SessionLocal
    from app.models import Tenant

    db = SessionLocal()
    try:
        tid = db.query(Tenant.id).filter(Tenant.slug == args.tenant).scalar()
        if tid is None:
            sys.exit(f"no existe el inquilino {args.tenant!r}")
        correr(db, tid, aplicar=args.aplicar, recalcular=args.recalcular_ligadas,
               lote=args.lote, unidades=not args.sin_unidades, csv_path=args.csv)
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
