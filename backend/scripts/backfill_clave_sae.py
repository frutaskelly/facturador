"""Escribe la clave base de SAE en cada producto — la decisión del 18-sep-2026.

Gana la convención de EHMO/MAFAN (`AJOPRIMERAKG`, `ACELGASKG`) sobre la de
Balles/Jubran (`AJO-FRUT-017`): mueve $4.45M contra $0.61M en 180 días y es la
que ya está replicada en la empresa 03. Cuando un producto tiene varias claves
de la convención ganadora, gana la que MÁS vendió en 180 días.

Un producto cuya única clave es de la otra convención se queda con ella: es el
único artículo que SAE le conoce, y inventarle otro sería darlo de alta por la
puerta de atrás.

Tres pasos, cada uno con su bandera. Sin banderas SOLO REPORTA:

  --aplicar            escribe productos.clave_sae      (aditivo: hoy nadie la lee
                       salvo como respaldo, así que no cambia nada que ya resuelva)
  --podar-iguales      limpia el codigo_cliente de las filas que REPITEN la base
                       (duplicación pura; la fila se conserva si aún dice cómo
                       llama el cliente al producto)
  --aplicar-decision-b limpia el codigo_cliente de las filas que apuntan a la
                       convención PERDEDORA. ESTE es el que cambia lo que sale a
                       SAE: de aquí en adelante Balles y Jubran facturan contra
                       el artículo ganador.

Nada se borra en SAE y nada toca lo ya facturado: sólo cambia lo que se exporte
de hoy en adelante (regla del dueño, 1-sep-2026).
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values

RAIZ_ENV = "/Users/michelzarate/Documents/Claude/Facturador/.env.prod"
TENANT = "cristian-gerardo-zarate-orozco"
# La convención perdedora: PREFIJO-FAMILIA-NUMERO (AJO-FRUT-017).
PERDEDORA = re.compile(r"^[A-Z]+-[A-Z]+-[0-9]+$")


def url_de(env_path: str) -> str:
    for linea in open(env_path, encoding="utf-8"):
        if linea.startswith("ALEMBIC_DB_URL="):
            return linea.split("=", 1)[1].strip().strip('"').strip("'").replace(
                "postgresql+psycopg2://", "postgresql://"
            )
    sys.exit(f"sin ALEMBIC_DB_URL en {env_path}")


SQL_CLAVES = """
with t as (select tt.id tid from tenants tt where tt.slug = %(tenant)s),
k as (select pc.producto_id, upper(btrim(pc.codigo_cliente)) cod
      from producto_clientes pc, t
      where pc.tenant_id = t.tid and pc.codigo_cliente is not null group by 1,2),
lin as (
  select lr.producto_id, r.cliente_facturacion_id cli, r.sucursal_id suc, lr.importe
  from remisiones r join lineas_remision lr on lr.remision_id = r.id, t
  where r.tenant_id = t.tid and r.deleted_at is null and r.estado <> 'CANCELADA'
    and r.fecha_remision >= current_date - 180 and lr.cantidad_solicitada > 0),
res as (
  select lin.producto_id, lin.importe, coalesce(
    (select upper(btrim(pc.codigo_cliente)) from producto_clientes pc
      where pc.cliente_id = lin.cli and pc.producto_id = lin.producto_id
        and pc.sucursal_id = lin.suc and pc.codigo_cliente is not null),
    (select upper(btrim(pc.codigo_cliente)) from producto_clientes pc
      where pc.cliente_id = lin.cli and pc.producto_id = lin.producto_id
        and pc.sucursal_id is null and pc.codigo_cliente is not null)) cod
  from lin),
v as (select producto_id, cod, sum(importe) imp from res where cod is not null group by 1,2)
select k.producto_id, k.cod, coalesce(v.imp, 0), p.nombre
  from k join productos p on p.id = k.producto_id
  left join v on v.producto_id = k.producto_id and v.cod = k.cod
 where p.deleted_at is null
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=RAIZ_ENV)
    ap.add_argument("--tenant", default=TENANT)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--podar-iguales", action="store_true")
    ap.add_argument("--aplicar-decision-b", action="store_true")
    args = ap.parse_args()

    con = psycopg2.connect(url_de(args.env))
    con.autocommit = False
    cur = con.cursor()
    cur.execute(SQL_CLAVES, {"tenant": args.tenant})
    por_prod: dict = defaultdict(list)
    nombres: dict = {}
    for pid, cod, imp, nombre in cur.fetchall():
        por_prod[pid].append((cod, Decimal(imp or 0)))
        nombres[pid] = nombre

    # ── Elegir la ganadora de cada producto ──
    ganadora: dict = {}
    solo_perdedora: list = []
    for pid, claves in por_prod.items():
        buenas = [(c, i) for c, i in claves if not PERDEDORA.match(c)]
        if not buenas:
            # Su única clave es de la convención perdedora: se respeta, es el
            # único artículo que SAE le conoce.
            solo_perdedora.append(pid)
            buenas = claves
        ganadora[pid] = max(buenas, key=lambda x: (x[1], x[0]))[0]

    # ── Choques: dos productos que quieren la misma clave ──
    por_clave: dict = defaultdict(list)
    for pid, cod in ganadora.items():
        por_clave[cod].append(pid)
    choques = {c: pids for c, pids in por_clave.items() if len(pids) > 1}
    for pids in choques.values():
        for pid in pids:
            ganadora.pop(pid, None)

    print(f"productos con clave           : {len(por_prod)}")
    print(f"  · base elegida              : {len(ganadora)}")
    print(f"  · sólo tenían la perdedora  : {len(solo_perdedora)} (se respeta su clave)")
    print(f"  · en choque, SIN tocar      : {sum(len(v) for v in choques.values())}"
          f" en {len(choques)} clave(s)")
    for cod, pids in list(choques.items())[:10]:
        print(f"      {cod}: " + " | ".join(nombres[p] for p in pids))

    escritos = 0
    if args.aplicar:
        # Un UPDATE por producto eran 1,019 viajes al pooler (más de dos
        # minutos). Un solo statement con VALUES hace lo mismo de un jalón.
        execute_values(
            cur,
            "update productos p set clave_sae = v.cod, updated_at = now()"
            " from (values %s) as v(pid, cod)"
            " where p.id = v.pid::uuid and coalesce(p.clave_sae,'') <> v.cod",
            [(str(pid), cod) for pid, cod in ganadora.items()],
        )
        escritos = cur.rowcount
        print(f"→ clave_sae escrita en {escritos} productos")

    # ── Poda 1: filas que repiten la base (duplicación pura) ──
    cur.execute(
        """
        select pc.id, upper(btrim(pc.codigo_cliente)), pc.producto_id,
               pc.nombre_cliente, pc.presentacion
          from producto_clientes pc
          join tenants t on t.id = pc.tenant_id
         where t.slug = %(tenant)s and pc.codigo_cliente is not null
        """,
        {"tenant": args.tenant},
    )
    filas = cur.fetchall()
    iguales = [f for f in filas if ganadora.get(f[2]) == f[1]]
    # OJO con la diferencia: una fila que apunta a la convención PERDEDORA es
    # la decisión que el dueño ya tomó (gana B). Una que apunta a OTRA clave de
    # la MISMA convención es uno de los duplicados que él dijo que iban uno por
    # uno —AJOKG contra AJOPRIMERAKG, y a veces ni siquiera son duplicados sino
    # la presentación (BROCOLIPZA contra BROCOLIKG)—: ésas NO se tocan aquí.
    perdedora = [f for f in filas
                 if f[2] in ganadora and ganadora[f[2]] != f[1] and PERDEDORA.match(f[1])]
    sin_decidir = [f for f in filas
                   if f[2] in ganadora and ganadora[f[2]] != f[1] and not PERDEDORA.match(f[1])]
    print(f"filas de catálogo con código  : {len(filas)}")
    print(f"  · repiten la base           : {len(iguales)}")
    print(f"  · convención perdedora      : {len(perdedora)}")
    print(f"  · otra clave de la MISMA convención, sin decidir: {len(sin_decidir)}"
          f" en {len({f[2] for f in sin_decidir})} productos (no se tocan)")

    # Antes de limpiar nada: si a Balles y Jubran se les quita su clave, van a
    # exportar con la base. Si la empresa 02 no la conoce, esa partida la
    # descarta al importar y la factura sale incompleta (el caso FRESADOMOPZ).
    # Mismo criterio que el candado del masivo; sin espejo no se opina.
    if perdedora:
        cur.execute(
            "select upper(btrim(clave)), activa from claves_sae"
            " join tenants t on t.id = claves_sae.tenant_id"
            " where t.slug = %(tenant)s and empresa = '02'",
            {"tenant": args.tenant},
        )
        espejo = dict(cur.fetchall())
        if not espejo:
            print("  ! sin espejo de la empresa 02: no se puede verificar la base")
        else:
            desconocidas = sorted({
                (ganadora[f[2]], nombres.get(f[2], "?")) for f in perdedora
                if espejo.get(ganadora[f[2]]) is None
            })
            de_baja = sorted({
                (ganadora[f[2]], nombres.get(f[2], "?")) for f in perdedora
                if espejo.get(ganadora[f[2]]) is False
            })
            print(f"  · de esas, su base NO está en la empresa 02 : {len(desconocidas)}")
            for cod, nom in desconocidas[:15]:
                print(f"      {cod}  ({nom})")
            print(f"  · de esas, su base está de BAJA en la 02    : {len(de_baja)}")
            for cod, nom in de_baja[:15]:
                print(f"      {cod}  ({nom})")
            # No se aborta todo por 13: se aplica la decisión donde es segura y
            # esas filas se quedan EXACTAMENTE como están hoy (su clave vieja,
            # que sí factura). Cuando esas claves existan en SAE, se vuelve a
            # correr y se limpian solas.
            malas = {c for c, _ in desconocidas} | {c for c, _ in de_baja}
            if malas:
                antes = len(perdedora)
                perdedora = [f for f in perdedora if ganadora[f[2]] not in malas]
                print(f"  → se SALTAN {antes - len(perdedora)} filas: se quedan con su clave"
                      f" actual hasta que la 02 tenga la base")

    def limpiar(lote, etiqueta):
        vacias = [f[0] for f in lote if not (f[3] or "").strip() and not (f[4] or "").strip()]
        resto = [f[0] for f in lote if f[0] not in set(vacias)]
        if vacias:
            cur.execute("delete from producto_clientes where id = any(%s::uuid[])",
                        ([str(x) for x in vacias],))
        if resto:
            cur.execute(
                "update producto_clientes set codigo_cliente = null, updated_at = now()"
                " where id = any(%s::uuid[])", ([str(x) for x in resto],),
            )
        print(f"→ {etiqueta}: {len(vacias)} filas borradas (no decían nada más),"
              f" {len(resto)} conservadas sin código")

    if args.podar_iguales:
        limpiar(iguales, "repetidas")
    if args.aplicar_decision_b:
        limpiar(perdedora, "convención perdedora")

    if args.aplicar or args.podar_iguales or args.aplicar_decision_b:
        con.commit()
        print("COMMIT")
    else:
        con.rollback()
        print("(sólo reporte — nada se escribió)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
