"""Unir un grupo aprobado en la revisión del catálogo.

Es la receta de las fusiones a mano del 30-sep … 2-oct-2026 (SANDIA, TORTILLA
BURRERA, TÉ DE LIMÓN, HIERBABUENA), ahora en el backend:

  * El que se queda toma el nombre, la unidad base y una clave SAE por unidad
    tal como se aprobaron.
  * De cada uno que se une:
      - sus precios se COPIAN donde el que se queda no tenga esa lista y unidad;
      - su catálogo de cliente se muda donde el que se queda no tenga fila para
        ese cliente y sucursal;
      - sus sinónimos se mudan y su nombre queda como sinónimo;
      - se desactiva CON su clave: sus remisiones en borrador siguen exportando
        bien. Sólo cambian las remisiones nuevas (regla del dueño).
  * Unidad que se quita: si el producto CAMBIA de unidad (un empaque de KILO
    pasa a PIEZA con la misma clave) lo que estaba en la unidad vieja se muda a
    la nueva; si sólo deja de venderse en ella, sus precios se borran y quedan
    en el resumen. Los precios especiales no se tocan: se retiraron el 1-oct.

Todo lo que cambia queda en el resumen (lo de antes del que se queda, los
precios borrados) para poder deshacerlo a mano si hiciera falta.
"""
from __future__ import annotations

import json
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from .producto_match import normalizar
from .revision_catalogo import ProductoRev
from .sat_catalogo import UNIDAD_A_SAT


def _entrada_presentacion(unidad: str, base: str, queda: ProductoRev, otros: list[ProductoRev]) -> dict:
    """La definición de una unidad que no es la base. Si el que se queda (u otro con
    la misma base) ya la tenía, se respeta su factor; si venía de la base de otro
    producto, la conversión no se conoce y va con factor 1, como en las fusiones a mano."""
    for p in [queda, *otros]:
        raw = (p.presentaciones or {}).get(unidad)
        if unidad != p.unidad_base and p.unidad_base == base and raw is not None:
            entrada = dict(raw) if isinstance(raw, dict) else {"factor": raw}
            entrada.setdefault("sat", UNIDAD_A_SAT.get(unidad, "H87"))
            return entrada
    return {"sat": UNIDAD_A_SAT.get(unidad, "H87"), "factor": 1}


def aplicar_grupo(db: Session, tenant_id: UUID, miembros: list[ProductoRev], prop: dict,
                  user_id: Optional[UUID]) -> dict:
    por_sku = {p.sku: p for p in miembros}
    queda = por_sku[prop["queda_sku"]]
    otros = [por_sku[s] for s in prop["se_unen"]]
    base = prop["unidad_base"]
    claves = {u["unidad"]: u["clave"] for u in prop["unidades"]}
    quitar = set(prop["quitar"])
    t = {"t": tenant_id}

    ids = [p.id for p in miembros]
    vivos = db.execute(text("""select id::text from productos where tenant_id = :t and id = any(cast(:ids as uuid[]))
                                 and activo and deleted_at is null for update"""), {**t, "ids": ids}).scalars().all()
    if len(vivos) != len(ids):
        raise ValueError("Alguno de los productos ya no está activo")

    antes = db.execute(text("""select nombre, unidad_base, presentaciones, clave_sae, unidad_sat, presentacion_default,
                                      unidad_entrada, unidad_salida from productos where id = :id"""),
                       {"id": queda.id}).mappings().one()
    resumen: dict = {"queda": queda.sku, "antes": {k: v for k, v in antes.items()}, "se_unen": [p.sku for p in otros],
                     "precios_copiados": 0, "precios_movidos": 0, "precios_borrados": [],
                     "catalogo_movido": 0, "sinonimos_movidos": 0, "sinonimos_nuevos": 0}

    # ── el que se queda ──
    presentaciones: dict = {base: 1}
    for u, clave in claves.items():
        if u != base:
            presentaciones[u] = {**_entrada_presentacion(u, base, queda, otros), "clave_sae": clave}
    # La unidad base nueva es otra y ningún producto la vendía: es el mismo artículo
    # que cambia de unidad (KILO → PIEZA), lo de la unidad vieja se muda a la nueva.
    cambia_de_unidad = queda.unidad_base != base and not any(base in p.unidades() for p in miembros)
    mudar = {queda.unidad_base: base} if cambia_de_unidad else {}

    def _ajusta(campo: Optional[str]) -> str:
        return campo if campo in claves else base

    db.execute(text("""
        update productos set nombre = :nombre, unidad_base = :base, presentaciones = cast(:pres as jsonb),
               clave_sae = :clave, unidad_sat = :usat, presentacion_default = :pdef,
               unidad_entrada = :uent, unidad_salida = :usal, updated_at = now(), updated_by = :u
         where id = :id"""), {
        "id": queda.id, "nombre": prop["nombre_final"][:254], "base": base, "pres": json.dumps(presentaciones),
        "clave": claves[base], "usat": UNIDAD_A_SAT.get(base, antes["unidad_sat"]),
        "pdef": _ajusta(antes["presentacion_default"]), "uent": _ajusta(antes["unidad_entrada"]),
        "usal": _ajusta(antes["unidad_salida"]), "u": user_id})

    # ── lo que estaba en las unidades que ya no se venden ──
    for u in quitar:
        nueva = mudar.get(u)
        filas = db.execute(text("""
            select pr.id::text, l.nombre, pr.precio_unitario, pr.cantidad_minima, pr.producto_id::text,
                   exists (select 1 from precios x where x.lista_id = pr.lista_id and x.producto_id = pr.producto_id
                             and x.presentacion = :nueva and x.cantidad_minima = pr.cantidad_minima) choca
              from precios pr join listas_precios l on l.id = pr.lista_id
             where pr.producto_id = :id and pr.presentacion = :u"""), {"id": queda.id, "u": u, "nueva": nueva}).all()
        for pid, lista, precio, cmin, _, choca in filas:
            if nueva and not choca:
                db.execute(text("update precios set presentacion = :n where id = :pid"), {"n": nueva, "pid": pid})
                resumen["precios_movidos"] += 1
            else:
                db.execute(text("delete from precios where id = :pid"), {"pid": pid})
                resumen["precios_borrados"].append({"sku": queda.sku, "lista": lista, "unidad": u,
                                                    "precio": str(precio), "cantidad_minima": cmin})
        db.execute(text("update producto_clientes set presentacion = :n where producto_id = :id and presentacion = :u"),
                   {"n": nueva, "id": queda.id, "u": u})
        if nueva:
            db.execute(text("update producto_alias set presentacion = :n where producto_id = :id and presentacion = :u"),
                       {"n": nueva, "id": queda.id, "u": u})
        else:
            # «KG → KILO» ya no tiene a dónde ir: la regla de unidad se borra; el
            # sinónimo sin unidad de la orden se queda, sin presentación fija.
            db.execute(text("""delete from producto_alias where producto_id = :id and presentacion = :u
                                 and unidad_oc is not null"""), {"id": queda.id, "u": u})
            db.execute(text("update producto_alias set presentacion = null where producto_id = :id and presentacion = :u"),
                       {"id": queda.id, "u": u})

    # ── los que se unen ──
    unidades_finales = list(claves)
    for a in otros:
        r = db.execute(text("""
            insert into precios (id, tenant_id, lista_id, producto_id, presentacion, precio_unitario, cantidad_minima,
                                 vigencia_desde, vigencia_hasta)
            select gen_random_uuid(), pr.tenant_id, pr.lista_id, :v, coalesce(pr.presentacion, :abase), pr.precio_unitario,
                   pr.cantidad_minima, pr.vigencia_desde, pr.vigencia_hasta
              from precios pr
             where pr.producto_id = :a and coalesce(pr.presentacion, :abase) = any(:unidades)
               and not exists (select 1 from precios x where x.lista_id = pr.lista_id and x.producto_id = :v
                                 and x.presentacion = coalesce(pr.presentacion, :abase)
                                 and x.cantidad_minima = pr.cantidad_minima)"""),
                       {"v": queda.id, "a": a.id, "abase": a.unidad_base, "unidades": unidades_finales})
        resumen["precios_copiados"] += r.rowcount
        r = db.execute(text("""
            update producto_clientes pc set producto_id = :v,
                   presentacion = case when pc.presentacion = any(:unidades) then pc.presentacion end
             where pc.producto_id = :a and not exists (
                   select 1 from producto_clientes x where x.producto_id = :v and x.cliente_id = pc.cliente_id
                      and x.sucursal_id is not distinct from pc.sucursal_id)"""),
                       {"v": queda.id, "a": a.id, "unidades": unidades_finales})
        resumen["catalogo_movido"] += r.rowcount
        # La llave única de los sinónimos no lleva el producto: mudarlos nunca choca.
        db.execute(text("""delete from producto_alias where producto_id = :a and unidad_oc is not null
                             and presentacion is not null and not (presentacion = any(:unidades))"""),
                   {"a": a.id, "unidades": unidades_finales})
        r = db.execute(text("""
            update producto_alias set producto_id = :v,
                   presentacion = case when presentacion = any(:unidades) then presentacion end
             where producto_id = :a"""), {"v": queda.id, "a": a.id, "unidades": unidades_finales})
        resumen["sinonimos_movidos"] += r.rowcount
        db.execute(text("update productos set activo = false, updated_at = now(), updated_by = :u where id = :a"),
                   {"a": a.id, "u": user_id})

    # Los nombres viejos se quedan como sinónimos: una orden que todavía diga
    # «ESPINACA PZA» cae en ESPINACA, en PIEZA.
    nombres = [(a.nombre, a.unidad_base) for a in otros]
    if antes["nombre"] != prop["nombre_final"]:
        nombres.append((antes["nombre"], antes["unidad_base"]))
    for nombre, unidad in nombres:
        norm = normalizar(nombre)[:254]
        if not norm or norm == normalizar(prop["nombre_final"]):
            continue
        r = db.execute(text("""
            insert into producto_alias (id, tenant_id, producto_id, alias, alias_normalizado, origen, presentacion, created_by)
            values (gen_random_uuid(), :t, :v, :alias, :norm, 'MANUAL', :pres, :u)
            on conflict do nothing"""), {**t, "v": queda.id, "alias": " ".join(nombre.split())[:254], "norm": norm,
                                         "pres": unidad if unidad in claves and unidad != base else None,
                                         "u": user_id})
        resumen["sinonimos_nuevos"] += r.rowcount
    return resumen
