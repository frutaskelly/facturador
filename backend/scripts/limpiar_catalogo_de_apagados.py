"""Las filas del catálogo de cliente que apuntan a un producto APAGADO.

Cabo suelto de `resolver_duplicados_clave.py`: al apagar los 60 gemelos del
15-sep, las líneas de remisión se repuntaron al producto viejo, pero las filas
del catálogo de cada cliente —cómo lo llama y con qué clave— se quedaron
colgando de un producto que ya nadie puede vender.

No estorban al export (nada apunta ya a esos productos), pero son catálogo
muerto: al abrir Clientes → Catálogo salen productos apagados, y cualquier
lectura del catálogo del cliente los arrastra.

El sobreviviente se identifica por la CLAVE, no por adivinar parejas: es el
producto activo cuyo `clave_sae` es la clave que trae la fila muerta.

  · sin choque  → la fila se MUDA al sobreviviente (conserva código, nombre y
    presentación: cómo llama el cliente al producto no se pierde);
  · con choque (el cliente ya tiene fila para el sobreviviente) → si la que vive
    no tiene `nombre_cliente` y la muerta sí, se le copia; y la muerta se borra.

Sin banderas SÓLO REPORTA.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import sys
from pathlib import Path

import psycopg2

ENV = "/Users/michelzarate/Documents/Claude/Facturador/.env.prod"
TENANT = "cristian-gerardo-zarate-orozco"

SQL = """
with t as (select tt.id tid from tenants tt where tt.slug = %(tenant)s),
muertas as (
  select pc.id, pc.cliente_id, pc.sucursal_id, pc.codigo_cliente, pc.nombre_cliente,
         pc.presentacion, p.nombre as apagado
    from producto_clientes pc join productos p on p.id = pc.producto_id, t
   where pc.tenant_id = t.tid and p.deleted_at is null and not p.activo)
select m.id, m.cliente_id, m.sucursal_id, m.codigo_cliente, m.nombre_cliente,
       m.apagado, c.legal_name, s.id, s.nombre, x.id, x.codigo_cliente, x.nombre_cliente
  from muertas m
  join clientes c on c.id = m.cliente_id
  left join productos s on s.tenant_id = (select tid from t) and s.activo
       and s.deleted_at is null
       and upper(btrim(s.clave_sae)) = upper(btrim(m.codigo_cliente))
  left join producto_clientes x on x.cliente_id = m.cliente_id and x.producto_id = s.id
       and x.sucursal_id is not distinct from m.sucursal_id
 order by c.legal_name, m.apagado
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=ENV)
    ap.add_argument("--tenant", default=TENANT)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    url = None
    for linea in open(args.env, encoding="utf-8"):
        if linea.startswith("ALEMBIC_DB_URL="):
            url = linea.split("=", 1)[1].strip().strip('"').strip("'").replace(
                "postgresql+psycopg2://", "postgresql://")
    if not url:
        sys.exit("sin ALEMBIC_DB_URL")

    con = psycopg2.connect(url)
    con.autocommit = False
    cur = con.cursor()
    cur.execute(SQL, {"tenant": args.tenant})
    filas = cur.fetchall()

    mudar, borrar, copiar_nombre, huerfanas, clave_distinta = [], [], [], [], []
    for (mid, _cli, _suc, cod, nombre, apagado, cliente, sob, sob_nombre,
         xid, xcod, xnombre) in filas:
        if sob is None:
            huerfanas.append((cliente, apagado, cod))
            continue
        if xid is None:
            mudar.append((mid, sob))
            continue
        borrar.append(mid)
        if not (xnombre or "").strip() and (nombre or "").strip():
            copiar_nombre.append((xid, nombre))
        if (xcod or "").strip().upper() != (cod or "").strip().upper():
            clave_distinta.append((cliente, apagado, cod, sob_nombre, xcod))

    print(f"filas que apuntan a un producto apagado : {len(filas)}")
    print(f"  · se mudan al sobreviviente           : {len(mudar)}")
    print(f"  · se borran (el cliente ya tiene fila): {len(borrar)}"
          f"  (y a {len(copiar_nombre)} se les rescata el nombre del cliente)")
    print(f"  · sin sobreviviente identificable     : {len(huerfanas)}")
    for cliente, apagado, cod in huerfanas[:10]:
        print(f"      {cliente[:28]:28} {apagado[:30]:30} {cod or '(sin código)'}")
    if clave_distinta:
        print(f"\n  OJO: {len(clave_distinta)} de las que se borran traían OTRA clave de SAE."
              f" Ese cliente deja de tener esa clave registrada:")
        for cliente, apagado, cod, sob_nombre, xcod in clave_distinta:
            print(f"      {cliente[:26]:26} {apagado[:26]:26} {cod:18} → se queda con"
                  f" {sob_nombre[:22]:22} {xcod}")

    if args.aplicar:
        hoy = datetime.date.today().isoformat()
        destino = Path.home() / "Documents/Claude/scripts" / f"respaldo-catalogo-muerto-{hoy}.csv"
        with destino.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["producto_cliente_id", "cliente", "producto_apagado",
                        "codigo", "nombre_cliente", "accion"])
            ids_mudan = {m[0] for m in mudar}
            for (mid, _c, _s, cod, nombre, apagado, cliente, *_r) in filas:
                w.writerow([mid, cliente, apagado, cod, nombre,
                            "mudada" if mid in ids_mudan else "borrada"])
        print(f"\nrespaldo: {destino}")

        for xid, nombre in copiar_nombre:
            cur.execute(
                "update producto_clientes set nombre_cliente = %s, updated_at = now()"
                " where id = %s", (nombre, xid))
        for mid, sob in mudar:
            cur.execute(
                "update producto_clientes set producto_id = %s, updated_at = now()"
                " where id = %s", (str(sob), mid))
        if borrar:
            cur.execute("delete from producto_clientes where id = any(%s::uuid[])",
                        ([str(x) for x in borrar],))
        con.commit()
        print(f"→ {len(mudar)} mudadas, {len(borrar)} borradas, "
              f"{len(copiar_nombre)} nombres rescatados\nCOMMIT")
    else:
        con.rollback()
        print("\n(sólo reporte — nada se escribió)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
