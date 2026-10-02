"""Revisión del catálogo: grupos de productos que son el mismo, en vivo.

Reemplaza la hoja de Excel del 30-sep-2026, que se quedaba vieja en cuanto se
unía algo. Los grupos se calculan en cada consulta (`services/revision_catalogo`)
y aquí sólo se guarda lo que una persona decidió de cada uno. Unir lo aprobado
es un paso aparte.

Prefijo propio (`/revision-catalogo`) y no `/productos/...`: bajo `/productos`
la ruta caería en `/{producto_id}` y respondería 422 por no ser UUID.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import RevisionCatalogo
from ...schemas.revision_catalogo import (
    AjusteIn,
    DecisionIn,
    DecisionOut,
    GrupoRevision,
    MiembroRevision,
    ResumenRevision,
    RevisionOut,
    UnidadMiembro,
)
from ...services.revision_catalogo import Catalogo, ProductoRev, armar_grupos, firma, proponer

router = APIRouter(prefix="/revision-catalogo", tags=["productos"])

_READ = "menu:productos"
_WRITE = "producto:gestionar"


def _cargar(db: Session, tenant_id: UUID) -> tuple[list[ProductoRev], Catalogo]:
    """Productos activos con lo que hace falta para decidir: cuánto venden,
    dónde tienen precio y qué remisiones en borrador los usan."""
    t = {"t": tenant_id}
    filas = db.execute(text("""
        select p.id::text, p.sku, p.nombre, p.unidad_base, p.presentaciones, p.clave_sae,
               coalesce(p.clave_sat, ''), coalesce(e.nombre, ''), coalesce(c.nombre, ''), p.created_at::date
          from productos p
          left join esquemas_impuesto e on e.id = p.esquema_impuesto_id
          left join categorias_producto c on c.id = p.categoria_id
         where p.tenant_id = :t and p.activo and p.deleted_at is null"""), t).all()

    def conteo(sql: str) -> dict[str, int]:
        return {str(pid): n for pid, n in db.execute(text(sql), t).all()}

    ventas = conteo("""
        select l.producto_id, count(*) from lineas_remision l join remisiones r on r.id = l.remision_id
         where r.tenant_id = :t and r.deleted_at is null and r.estado <> 'CANCELADA'
           and r.fecha_remision >= current_date - 365 group by 1""")
    precios = conteo("""
        select pr.producto_id, count(*) from precios pr join listas_precios l on l.id = pr.lista_id
         where l.tenant_id = :t and l.deleted_at is null group by 1""")
    alias = conteo("select producto_id, count(*) from producto_alias where tenant_id = :t group by 1")
    catalogo = conteo("select producto_id, count(*) from producto_clientes where tenant_id = :t group by 1")
    borradores: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for pid, unidad, folio in db.execute(text("""
        select l.producto_id::text, coalesce(l.presentacion, p.unidad_base), r.folio_interno
          from lineas_remision l join remisiones r on r.id = l.remision_id join productos p on p.id = l.producto_id
         where r.tenant_id = :t and r.estado = 'BORRADOR' and r.deleted_at is null"""), t).all():
        borradores[pid][unidad].add(folio)

    prods = [
        ProductoRev(id=f[0], sku=f[1], nombre=f[2], unidad_base=f[3], presentaciones=f[4] or {},
                    clave_sae=f[5], clave_sat=f[6], esquema=f[7], categoria=f[8], alta=f[9],
                    ventas=ventas.get(f[0], 0), precios=precios.get(f[0], 0), alias=alias.get(f[0], 0),
                    catalogo=catalogo.get(f[0], 0),
                    borradores={u: sorted(fs) for u, fs in borradores.get(f[0], {}).items()})
        for f in filas
    ]
    en_sae = {c: list(emps) for c, emps in db.execute(text("""
        select upper(btrim(clave)), array_agg(empresa order by empresa) from claves_sae
         where tenant_id = :t and activa group by 1"""), t).all()}
    facturas = {c: n for c, n in db.execute(text("""
        select upper(btrim(clave_sae)), count(*) from lineas_factura
         where tenant_id = :t and coalesce(btrim(clave_sae), '') <> '' group by 1"""), t).all()}
    return prods, Catalogo(en_sae=en_sae, facturas=facturas)


def _ajustes(row: Optional[RevisionCatalogo]) -> dict:
    return dict((row.propuesta or {}).get("ajustes") or {}) if row else {}


def _armar(g: dict, cat: Catalogo, row: Optional[RevisionCatalogo], ajustes: Optional[dict] = None) -> GrupoRevision:
    miembros = g["miembros"]
    f = firma(miembros)
    vigente = row is not None and row.firma == f
    aj = ajustes if ajustes is not None else (_ajustes(row) if vigente else {})
    prop = proponer(miembros, cat, tipo=g["tipo"], raiz=g["raiz"], **aj)
    decision = None
    estado = "PENDIENTE"
    if row is not None:
        decision = DecisionOut(estado=row.estado, nota=row.nota, por=row.decidido_por_email,
                               at=row.decidido_at, desactualizada=not vigente)
        if vigente:
            estado = row.estado
    return GrupoRevision(
        clave=g["clave"], tipo=g["tipo"], raiz=g["raiz"], estado=estado, decision=decision,
        propuesta=prop, ventas=sum(p.ventas for p in miembros),
        miembros=[
            MiembroRevision(
                id=p.id, sku=p.sku, nombre=p.nombre, unidad_base=p.unidad_base,
                unidades=[UnidadMiembro(unidad=u, clave=c, en_sae=cat.existe(c),
                                        borradores=len(p.borradores.get(u, [])))
                          for u, c in p.unidades().items()],
                categoria=p.categoria, esquema=p.esquema, clave_sat=p.clave_sat, alta=p.alta,
                ventas=p.ventas, precios=p.precios, alias=p.alias, catalogo=p.catalogo,
            )
            for p in sorted(miembros, key=lambda p: (p.sku != prop["queda_sku"], -p.ventas, p.sku))
        ],
    )


def _decisiones(db: Session, tenant_id: UUID) -> dict[str, RevisionCatalogo]:
    return {r.grupo: r for r in db.query(RevisionCatalogo).filter(RevisionCatalogo.tenant_id == tenant_id)}


def _grupo(db: Session, ctx: AuthContext, clave: str):
    prods, cat = _cargar(db, ctx.tenant_id)
    g = next((x for x in armar_grupos(prods, cat) if x["clave"] == clave), None)
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Ese grupo ya no existe: el catálogo cambió. Recarga la pantalla.")
    return g, cat


def _validar_ajustes(g: dict, cat: Catalogo, body: AjusteIn) -> dict:
    skus = {p.sku for p in g["miembros"]}
    if body.queda_sku and body.queda_sku not in skus:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{body.queda_sku} no es parte de este grupo")
    claves = {u.strip().upper(): (c or "").strip().upper() for u, c in (body.claves or {}).items()}
    for u, c in claves.items():
        if c and not cat.existe(c):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"La clave {c} ({u}) no existe en SAE")
    aj = {"queda_sku": body.queda_sku, "nombre_final": (body.nombre_final or "").strip() or None,
          "claves": claves or None,
          "quitar": [u.strip().upper() for u in body.quitar] if body.quitar is not None else None}
    return {k: v for k, v in aj.items() if v is not None}


@router.get("", response_model=RevisionOut)
def listar(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    prods, cat = _cargar(db, ctx.tenant_id)
    filas = _decisiones(db, ctx.tenant_id)
    grupos = [_armar(g, cat, filas.get(g["clave"])) for g in armar_grupos(prods, cat)]
    grupos.sort(key=lambda g: (g.estado != "PENDIENTE", -g.ventas, g.raiz))
    por_estado: dict[str, int] = defaultdict(int)
    por_tipo: dict[str, int] = defaultdict(int)
    for g in grupos:
        por_estado[g.estado] += 1
        por_tipo[g.tipo] += 1
    return RevisionOut(grupos=grupos, resumen=ResumenRevision(
        grupos=len(grupos), productos=sum(len(g.miembros) for g in grupos),
        por_estado=dict(por_estado), por_tipo=dict(por_tipo),
        aplicados=sum(1 for r in filas.values() if r.estado == "APLICADO"),
    ))


@router.post("/propuesta", response_model=GrupoRevision)
def recalcular(
    body: AjusteIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cómo quedaría el grupo con lo que la persona cambió. No guarda nada."""
    g, cat = _grupo(db, ctx, body.grupo)
    row = _decisiones(db, ctx.tenant_id).get(body.grupo)
    return _armar(g, cat, row, _validar_ajustes(g, cat, body))


@router.post("/decision", response_model=GrupoRevision)
def decidir(
    body: DecisionIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    g, cat = _grupo(db, ctx, body.grupo)
    row = db.query(RevisionCatalogo).filter(RevisionCatalogo.tenant_id == ctx.tenant_id,
                                            RevisionCatalogo.grupo == body.grupo).one_or_none()
    if row is not None and row.estado == "APLICADO":
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese grupo ya se aplicó: no se puede cambiar la decisión")
    if body.estado == "PENDIENTE":
        if row is not None:
            db.delete(row)
            db.commit()
        return _armar(g, cat, None)

    aj = _validar_ajustes(g, cat, body)
    prop = proponer(g["miembros"], cat, tipo=g["tipo"], raiz=g["raiz"], **aj)
    if body.estado == "APROBADO":
        problemas = list(prop["bloqueos"])
        problemas += [f"{u['unidad']}: falta una clave que exista en SAE"
                      for u in prop["unidades"] if not u["clave"] or not u["en_sae"]]
        if not prop["unidades"]:
            problemas.append("No puede quedar sin ninguna unidad")
        if problemas:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, " · ".join(problemas))
    if row is None:
        row = RevisionCatalogo(tenant_id=ctx.tenant_id, grupo=body.grupo)
        db.add(row)
    row.estado = body.estado
    row.firma = firma(g["miembros"])
    row.propuesta = {**prop, "ajustes": aj}
    row.nota = (body.nota or "").strip() or None
    row.decidido_por = ctx.user_id
    row.decidido_por_email = ctx.email
    row.decidido_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _armar(g, cat, row)
