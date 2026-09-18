"""El producto VIEJO se queda la clave; su gemelo del 15-sep se desactiva.

De dónde sale: el backfill del 18-sep dejó 92 claves de SAE sin dueño porque dos
productos del Facturador reclamaban el mismo artículo. Mirando las fechas, 87 de
los 188 en disputa se crearon el MISMO día —15-sep-2026, SKU consecutivo
00010559-00010854— y con el nombre exacto de la descripción de SAE (ACELGAS,
AGUACATE HASS, CILANTRO, ESPINACAS…). Los originales son de agosto, tienen
nombres largos y se llevan casi toda la venta: fue una importación masiva
alimentada con el catálogo de SAE que duplicó lo que ya existía.

Regla del dueño: gana el viejo. Aquí eso significa tres escrituras por caso:

  1. el VIEJO recibe la clave (`productos.clave_sae`);
  2. el GEMELO se desactiva (`activo = false`) — no se borra ni se toca nada en
     SAE, sólo deja de ofrecerse al cruzar;
  3. con `--repuntar-borradores`, las líneas de remisiones en BORRADOR que
     apuntaban al gemelo pasan al viejo. Sin esto, las remisiones que hoy están
     atoradas siguen igual: su línea apunta al gemelo, que quedó sin clave.

NO se toca (se reporta y se salta):
  · los casos donde el NUEVO vende más que el viejo — contradice la premisa;
  · los de tres o más productos peleando la misma clave;
  · los que tienen nombres distintos y AMBOS venden: pueden ser dos artículos de
    verdad (BROCOLIPZA es pieza y BROCOLIKG es kilo), y el perdedor necesitaría
    su propia clave en SAE, no que se la quiten;
  · cualquier línea de una remisión que no esté en BORRADOR.

Sin banderas SÓLO REPORTA.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from collections import defaultdict
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values

ENV = "/Users/michelzarate/Documents/Claude/Facturador/.env.prod"
TENANT = "cristian-gerardo-zarate-orozco"
# El día de la importación que creó los gemelos. Es el criterio: un par sólo se
# resuelve solo si el nuevo nació ESE día y el viejo antes.
TANDA = date(2026, 9, 15)


def url_de(env_path: str) -> str:
    for linea in open(env_path, encoding="utf-8"):
        if linea.startswith("ALEMBIC_DB_URL="):
            return linea.split("=", 1)[1].strip().strip('"').strip("'").replace(
                "postgresql+psycopg2://", "postgresql://")
    sys.exit(f"sin ALEMBIC_DB_URL en {env_path}")


SQL = """
with t as (select tt.id tid from tenants tt where tt.slug = %(tenant)s),
k as (select pc.producto_id, upper(btrim(pc.codigo_cliente)) cod
      from producto_clientes pc, t
      where pc.tenant_id = t.tid and pc.codigo_cliente is not null group by 1,2),
disputadas as (select cod from k group by cod having count(distinct producto_id) > 1),
ventas as (
  select lr.producto_id, sum(lr.importe) imp
    from remisiones r join lineas_remision lr on lr.remision_id = r.id, t
   where r.tenant_id = t.tid and r.deleted_at is null and r.estado <> 'CANCELADA'
     and r.fecha_remision >= current_date - 365
   group by 1)
select k.cod, p.id, p.nombre, p.created_at::date, coalesce(v.imp, 0), p.activo,
       p.clave_sae, p.presentaciones, p.unidad_base
  from k join disputadas d on d.cod = k.cod
  join productos p on p.id = k.producto_id
  left join ventas v on v.producto_id = p.id
 where p.deleted_at is null
"""

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=ENV)
    ap.add_argument("--tenant", default=TENANT)
    ap.add_argument("--aplicar", action="store_true",
                    help="el viejo recibe la clave y el gemelo se desactiva")
    ap.add_argument("--repuntar-borradores", action="store_true",
                    help="además, las líneas en BORRADOR pasan del gemelo al viejo")
    args = ap.parse_args()

    con = psycopg2.connect(url_de(args.env))
    con.autocommit = False
    cur = con.cursor()
    cur.execute(SQL, {"tenant": args.tenant})
    por_clave: dict = defaultdict(list)
    for cod, pid, nombre, creado, imp, activo, clave_sae, pres, ubase in cur.fetchall():
        por_clave[cod].append({
            "id": pid, "nombre": nombre, "creado": creado, "imp": Decimal(imp or 0),
            "activo": activo, "clave_sae": clave_sae, "pres": pres or {},
            "ubase": ubase,
            # ¿Tiene alguna presentación por pieza? (para el guardia de abajo)
            "upres": any(re.search(r"PZ|PIEZA", k.upper()) for k in (pres or {})),
        })

    decididos: list = []           # (clave, viejo, gemelo)
    saltados: dict = defaultdict(list)
    for cod, grupo in sorted(por_clave.items()):
        if len(grupo) > 2:
            saltados["tres o más productos"].append((cod, [g["nombre"] for g in grupo]))
            continue
        viejo, nuevo = sorted(grupo, key=lambda g: (g["creado"], g["nombre"]))
        # El criterio que de verdad identifica al gemelo es la FECHA, no el
        # parecido de los nombres: comparar textos saltaba AGUACATE contra
        # AGUACATE HASS, que es el mismo producto escrito de dos formas.
        if not (nuevo["creado"] == TANDA and viejo["creado"] < TANDA):
            saltados["no son la tanda del 15-sep"].append(
                (cod, [f"{g['nombre']} ({g['creado']})" for g in (viejo, nuevo)]))
            continue
        if nuevo["imp"] > viejo["imp"]:
            saltados["el nuevo vende más que el viejo"].append(
                (cod, [f"{g['nombre']} ({g['creado']}, ${g['imp']:,.0f})" for g in (viejo, nuevo)]))
            continue
        # Pieza contra kilo NO es un duplicado: BROCOLIPZA y BROCOLIKG son dos
        # artículos de SAE y cada uno necesita el suyo. Se detecta por la unidad
        # y por la marca de pieza en el nombre o en la clave.
        def pieza(x: str) -> bool:
            return bool(re.search(r"\b(PZ|PZA|PIEZA)S?\b|PZA?$", (x or "").upper()))
        if (pieza(nuevo["nombre"]) != pieza(viejo["nombre"])
                or (pieza(cod) and not (pieza(viejo["nombre"]) or viejo["upres"]))):
            saltados["¿pieza contra kilo?"].append(
                (cod, [f"{g['nombre']} [{g['ubase']}]" for g in (viejo, nuevo)]))
            continue
        if viejo["ubase"] != nuevo["ubase"]:
            saltados["unidad base distinta"].append(
                (cod, [f"{g['nombre']} [{g['ubase']}]" for g in (viejo, nuevo)]))
            continue
        decididos.append((cod, viejo, nuevo))

    # Una clave que YA tiene dueño no se le quita a nadie. Pasa cuando alguien
    # la asignó a mano desde la pantalla —y a veces al revés de esta regla, al
    # gemelo en vez de al viejo—: esa es una decisión de una persona mirando el
    # caso, y un script no la pisa. Se reporta para que quien la tomó confirme.
    cur.execute(
        "select p.id, upper(btrim(p.clave_sae)), p.nombre from productos p"
        " join tenants t on t.id = p.tenant_id"
        " where t.slug = %(tenant)s and p.deleted_at is null and p.clave_sae is not null",
        {"tenant": args.tenant},
    )
    dueno_de = {clave: (pid, nombre) for pid, clave, nombre in cur.fetchall()}
    reasignadas = []
    for cod, v, g in list(decididos):
        dueno = dueno_de.get(cod)
        if dueno is None or dueno[0] == v["id"]:
            continue                       # libre, o ya es del viejo: adelante
        if dueno[0] == g["id"]:
            # La tiene el gemelo (alguien la capturó a mano el 18-sep, al revés
            # de la regla). Decisión del dueño: manda la regla — se le quita al
            # apagarlo y pasa al viejo.
            reasignadas.append((cod, dueno[1], v["nombre"]))
            continue
        decididos.remove((cod, v, g))
        saltados["la clave es de un TERCER producto"].append(
            (cod, [f"{dueno[1]} la tiene · el viejo sería {v['nombre']}"]))

    for cod, v, g in list(decididos):
        suya = (v["clave_sae"] or "").strip().upper()
        if suya and suya != cod:
            decididos.remove((cod, v, g))
            saltados["el viejo ya tiene OTRA clave"].append(
                (cod, [f"{v['nombre']} tiene {suya}"]))

    # Un producto no puede quedarse DOS claves: el índice único sólo deja una y
    # el UPDATE aplicaría cualquiera de las dos en silencio. Pasa cuando SAE
    # mismo tiene el artículo duplicado (CALABAZACASTIKG y CALABAZACASTILKG son
    # el mismo chayote con dos claves). Cuál vale lo decide una persona.
    veces = defaultdict(list)
    for cod, v, _ in decididos:
        veces[v["id"]].append(cod)
    repetidos = {pid for pid, cods in veces.items() if len(cods) > 1}
    # Y tampoco puede ganar en un caso y apagarse en otro.
    apagados = {g["id"] for _, _, g in decididos}
    conflictivos = repetidos | {v["id"] for _, v, _ in decididos if v["id"] in apagados}
    if conflictivos:
        for cod, v, g in list(decididos):
            if v["id"] in conflictivos:
                decididos.remove((cod, v, g))
                saltados["el mismo producto gana dos claves (o gana y se apaga)"].append(
                    (cod, [f"{v['nombre']} ← {', '.join(veces[v['id']])}"]))

    print(f"claves en disputa            : {len(por_clave)}")
    print(f"  · resueltas (gana el viejo): {len(decididos)}")
    if reasignadas:
        print(f"  · de ésas, {len(reasignadas)} le QUITAN la clave al gemelo"
              f" (se capturó a mano al revés de la regla):")
        for cod, tenia, pasa_a in reasignadas:
            print(f"      {cod:18} {tenia[:28]:28} → {pasa_a[:34]}")
    for cod, v, g in decididos[:12]:
        print(f"      {cod:18} {v['nombre'][:34]:34} ${v['imp']:>9,.0f}"
              f"   ⟵ se apaga: {g['nombre'][:28]} (${g['imp']:,.0f})")
    if len(decididos) > 12:
        print(f"      … y {len(decididos) - 12} más")
    for motivo, casos in saltados.items():
        print(f"  · SALTADAS, {motivo}: {len(casos)}")
        for cod, nombres in casos[:8]:
            print(f"      {cod}: " + " | ".join(nombres))

    # Las líneas en BORRADOR que apuntan al gemelo: sin moverlas, la remisión
    # atorada sigue atorada (su línea apunta al que se quedó sin clave).
    gemelos = {g["id"]: v["id"] for _, v, g in decididos}
    cur.execute(
        """
        select lr.id, lr.producto_id, lr.presentacion, r.folio_interno, r.estado
          from lineas_remision lr join remisiones r on r.id = lr.remision_id
          join tenants t on t.id = r.tenant_id
         where t.slug = %(tenant)s and r.deleted_at is null
           and lr.producto_id = any(%(ids)s::uuid[])
        """,
        {"tenant": args.tenant, "ids": [str(x) for x in gemelos]},
    )
    lineas = cur.fetchall()
    borradores = [l for l in lineas if l[4] == "BORRADOR"]
    otras = [l for l in lineas if l[4] != "BORRADOR"]
    print(f"líneas que apuntan a un gemelo: {len(lineas)}"
          f"  (BORRADOR {len(borradores)} · intocables {len(otras)})")
    if otras:
        estados = defaultdict(int)
        for l in otras:
            estados[l[4]] += 1
        print("      se quedan como están: " + ", ".join(f"{k} {v}" for k, v in estados.items()))

    if args.aplicar:
        # PRIMERO se apaga al gemelo y se le quita la clave: si no, el índice
        # único rechaza dárse la al viejo (la del gemelo sigue ocupándola).
        cur.execute(
            "update productos set activo = false, clave_sae = null, updated_at = now()"
            " where id = any(%s::uuid[])",
            ([str(g["id"]) for _, _, g in decididos],),
        )
        execute_values(
            cur,
            "update productos p set clave_sae = v.cod, updated_at = now()"
            " from (values %s) as v(pid, cod)"
            " where p.id = v.pid::uuid and p.clave_sae is null",
            [(str(viejo["id"]), cod) for cod, viejo, _ in decididos],
        )
        print(f"→ clave puesta a {len(decididos)} productos viejos; gemelos desactivados")

    if args.repuntar_borradores and borradores:
        movidas = 0
        for lid, pid, pres, folio, _ in borradores:
            destino = gemelos[pid]
            # La presentación se conserva sólo si el viejo la tiene: heredar una
            # que no existe deja el factor en 1 y descuadra el inventario.
            viejo = next(v for _, v, g in decididos if g["id"] == pid)
            nueva_pres = pres if pres in (viejo["pres"] or {}) else None
            if nueva_pres is None:
                print(f"      ! {folio}: {pres} no existe en «{viejo['nombre']}», línea sin mover")
                continue
            cur.execute(
                "update lineas_remision set producto_id = %s where id = %s",
                (str(destino), lid),
            )
            movidas += 1
        print(f"→ {movidas} líneas de BORRADOR repuntadas al producto viejo")

    if args.aplicar or args.repuntar_borradores:
        con.commit()
        print("COMMIT")
    else:
        con.rollback()
        print("(sólo reporte — nada se escribió)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
