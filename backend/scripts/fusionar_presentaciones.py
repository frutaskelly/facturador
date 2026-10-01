"""Un producto, varias presentaciones, una clave de SAE por presentación.

Regla del dueño (30-sep-2026): el NOMBRE del producto es único y lo que cambia
es la unidad. SANDIA es un solo producto con KILO → SANDIAKG y PIEZA →
SANDIAPZ; «SANDIA PZ», «SANDIA PZA», «SANDIA (11 - 12 KG)» y el segundo
«SANDIA» eran el mismo producto dado de alta cuatro veces más.

Qué hace, por fusión (ver FUSIONES abajo):

  1. el SOBREVIVIENTE queda con exactamente las presentaciones de la receta,
     cada una con su clave de SAE (la base sigue usando `productos.clave_sae`);
  2. los ABSORBIDOS se desactivan (`activo = false`). Conservan su clave: sus
     remisiones ya capturadas se siguen exportando igual que hoy. Decisión del
     dueño: «únicamente impacta a nuevas remisiones»;
  3. sus PRECIOS se COPIAN al sobreviviente, llevando cada uno a la presentación
     que le toca — sólo donde el sobreviviente no tenga ya precio para esa
     lista + presentación (el suyo manda y no se pisa);
  4. sus ALIAS pasan al sobreviviente y su NOMBRE queda como alias global, para
     que las OC y el bot sigan cruzando a él (el cruce sólo mira activos);
  5. `--kilos-como-pieza`: las partidas en BORRADOR del sobreviviente que dicen
     PIEZA a precio de kilo pasan a KILO. Era una captura mal hecha (9.3 «piezas»
     a $20.50) que hasta hoy salía bien de chiripa como SANDIAKG; con SANDIAPZ
     en la PIEZA saldría mal. Cantidad, precio e importe no se mueven.

NO toca: partidas de remisiones (salvo el punto 5), el catálogo de cada
cliente, lo facturado, SAE.

Sin --aplicar SÓLO REPORTA. Con --aplicar deja respaldo CSV de lo que cambia.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

import psycopg2

ENV = "/Users/michelzarate/Documents/Claude/Facturador/.env.prod"
TENANT = "cristian-gerardo-zarate-orozco"
RESPALDOS = Path.home() / "Documents/Claude/scripts"

# sobreviviente: SKU que se queda. presentaciones: la receta final COMPLETA (lo
# que no esté aquí se quita). absorbidos: SKU → presentación a la que van sus
# precios.
FUSIONES = [
    {
        "nombre": "SANDIA",
        "sobreviviente": "00000391",
        "presentaciones": {
            "KILO": 1,
            "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "SANDIAPZ"},
        },
        "absorbidos": {
            "00011135": "KILO",    # SANDIA — SAND-FRUT-413, el mismo artículo duplicado en SAE
            "00010546": "PIEZA",   # SANDIA PZA — SANDIAPZ
            "00010545": "PIEZA",   # SANDIA PZ — «SANDIA PZA», clave SAT de salsas
            "00010395": "PIEZA",   # SANDIA (11 - 12 KG) — sin clave, sin ventas
        },
        # Precio unitario por debajo del cual una «PIEZA» en BORRADOR es un kilo.
        "tope_kilo": Decimal("30"),
    },
]


def url_de(env_path: str) -> str:
    for linea in open(env_path, encoding="utf-8"):
        if linea.startswith("ALEMBIC_DB_URL="):
            return linea.split("=", 1)[1].strip().strip('"').strip("'").replace(
                "postgresql+psycopg2://", "postgresql://")
    sys.exit(f"sin ALEMBIC_DB_URL en {env_path}")


def normalizar(texto: str) -> str:
    """El mismo que app/services/producto_match.normalizar (alias_normalizado)."""
    s = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s.lower())
    return " ".join(s.split())


def fusionar(cur, tid, f: dict, respaldo: list, kilos_como_pieza: bool) -> None:
    skus = [f["sobreviviente"], *f["absorbidos"]]
    cur.execute(
        "select sku, id, nombre, activo, clave_sae, presentaciones from productos"
        " where tenant_id = %s and sku = any(%s) and deleted_at is null",
        (tid, skus),
    )
    prods = {r[0]: {"id": r[1], "nombre": r[2], "activo": r[3], "clave": r[4], "pres": r[5]}
             for r in cur.fetchall()}
    faltan = set(skus) - set(prods)
    if faltan:
        sys.exit(f"{f['nombre']}: no existen {sorted(faltan)}")
    viv = prods[f["sobreviviente"]]
    print(f"\n══ {f['nombre']}: se queda {f['sobreviviente']} «{viv['nombre']}» ({viv['clave']})")

    # 1. presentaciones del sobreviviente
    nuevas = f["presentaciones"]
    print(f"  presentaciones: {json.dumps(viv['pres'], ensure_ascii=False)}")
    print(f"             →    {json.dumps(nuevas, ensure_ascii=False)}")
    respaldo.append(["producto.presentaciones", f["sobreviviente"], json.dumps(viv["pres"]), json.dumps(nuevas)])
    cur.execute("update productos set presentaciones = %s::jsonb, updated_at = now() where id = %s",
                (json.dumps(nuevas), viv["id"]))

    for sku, destino in f["absorbidos"].items():
        a = prods[sku]
        print(f"  ─ absorbe {sku} «{a['nombre']}» ({a['clave'] or 'sin clave'}) → {destino}")

        # 2. desactivar (conserva su clave: sus remisiones ya capturadas no cambian)
        if a["activo"]:
            respaldo.append(["producto.activo", sku, "true", "false"])
            cur.execute("update productos set activo = false, updated_at = now() where id = %s", (a["id"],))

        # 3. precios de lista: copia sólo si el sobreviviente no tiene ya esa lista+presentación
        cur.execute(
            """
            select pr.lista_id, l.nombre, pr.precio_unitario, pr.cantidad_minima,
                   pr.vigencia_desde, pr.vigencia_hasta, x.precio_unitario
              from precios pr join listas_precios l on l.id = pr.lista_id
              left join precios x on x.lista_id = pr.lista_id and x.producto_id = %(viv)s
                   and x.presentacion = %(dest)s and x.cantidad_minima = pr.cantidad_minima
             where pr.producto_id = %(abs)s
            """,
            {"viv": viv["id"], "abs": a["id"], "dest": destino},
        )
        for lista_id, lista, precio, qmin, desde, hasta, ya in cur.fetchall():
            if ya is not None:
                marca = "igual" if Decimal(ya) == Decimal(precio) else f"DISTINTO, se queda ${ya:,.2f}"
                print(f"      precio {lista}: ${precio:,.2f} {destino} — ya existe ({marca})")
                continue
            cur.execute(
                "insert into precios (id, tenant_id, lista_id, producto_id, presentacion,"
                " precio_unitario, cantidad_minima, vigencia_desde, vigencia_hasta)"
                " values (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s)",
                (tid, lista_id, viv["id"], destino, precio, qmin, desde, hasta),
            )
            respaldo.append(["precio+", f["sobreviviente"], "", f"{lista} {destino} {precio}"])
            print(f"      precio {lista}: ${precio:,.2f} {destino} — copiado")

        # 3b. overrides de cliente/sucursal: NO se copian (dueño, 1-oct). Los
        # precios especiales se borraron y no deben renacer como efecto de una
        # fusión; solo se avisa para que alguien decida a mano.
        cur.execute(
            "select count(*) from precio_overrides where producto_id = %s", (a["id"],)
        )
        (n_ovr,) = cur.fetchone()
        if n_ovr:
            print(f"      {n_ovr} precio(s) especial(es) del gemelo — NO se copian")

        # 4. alias: los suyos pasan; su nombre queda como alias global
        cur.execute("select id, alias from producto_alias where producto_id = %s", (a["id"],))
        for aid, alias in cur.fetchall():
            cur.execute("update producto_alias set producto_id = %s where id = %s", (viv["id"], aid))
            respaldo.append(["alias→", sku, alias, f["sobreviviente"]])
            print(f"      alias «{alias}» → pasa")
        norm = normalizar(a["nombre"])
        if norm != normalizar(viv["nombre"]):
            cur.execute(
                "insert into producto_alias (id, tenant_id, producto_id, alias, alias_normalizado, origen)"
                " select gen_random_uuid(), %s, %s, %s, %s, 'MANUAL'"
                " where not exists (select 1 from producto_alias where tenant_id = %s"
                "   and cliente_id is null and sucursal_id is null and alias_normalizado = %s)",
                (tid, viv["id"], a["nombre"], norm, tid, norm),
            )
            if cur.rowcount:
                respaldo.append(["alias+", f["sobreviviente"], "", a["nombre"]])
                print(f"      alias «{a['nombre']}» → nuevo")

    # 5. PIEZA a precio de kilo en BORRADOR → KILO
    if kilos_como_pieza:
        cur.execute(
            """
            select lr.id, r.folio_interno, lr.cantidad_solicitada, lr.precio_unitario
              from lineas_remision lr join remisiones r on r.id = lr.remision_id
             where lr.producto_id = %s and lr.presentacion = 'PIEZA'
               and r.estado = 'BORRADOR' and r.deleted_at is null
               and lr.precio_unitario > 0 and lr.precio_unitario < %s
             order by r.folio_interno
            """,
            (viv["id"], f["tope_kilo"]),
        )
        for lid, folio, cant, precio in cur.fetchall():
            cur.execute("update lineas_remision set presentacion = 'KILO' where id = %s", (lid,))
            respaldo.append(["linea.presentacion", folio, "PIEZA", f"KILO ({cant} @ {precio})"])
            print(f"  partida {folio}: {cant:g} PIEZA @ ${precio:,.2f} → KILO")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=ENV)
    ap.add_argument("--tenant", default=TENANT)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--kilos-como-pieza", action="store_true",
                    help="las PIEZA a precio de kilo en BORRADOR pasan a KILO")
    args = ap.parse_args()

    con = psycopg2.connect(url_de(args.env))
    con.autocommit = False
    cur = con.cursor()
    cur.execute("select id from tenants where slug = %s", (args.tenant,))
    tid = cur.fetchone()[0]

    respaldo: list = []
    for f in FUSIONES:
        fusionar(cur, tid, f, respaldo, args.kilos_como_pieza)

    if args.aplicar:
        ruta = RESPALDOS / f"respaldo-fusion-presentaciones-{datetime.date.today()}.csv"
        with open(ruta, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([["que", "donde", "antes", "despues"], *respaldo])
        con.commit()
        print(f"\nCOMMIT — respaldo en {ruta}")
    else:
        con.rollback()
        print("\n(sólo reporte — nada se escribió)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
