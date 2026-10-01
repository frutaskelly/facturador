"""La lista de precios se escoge donde vive la negociación (1-oct-2026).

Antes había una pantalla aparte, «Asignación de precios», para decir qué lista
cobra cada cliente/plaza/proyecto. Se retiró del menú: la lista se escoge en
la ficha del PROYECTO, en el vínculo CLIENTE×PLAZA (Sucursales y precios) o en
la ficha del CLIENTE (en cualquier plaza). Todas escriben el mismo renglón de
`lista_asignaciones` —el resolutor de precios no cambia— y este módulo es lo
único que sabe cuál renglón es el de cada lugar.

Cambiar la lista cambia lo que se cobra, así que pide el mismo permiso que la
pantalla que sustituye (`lista_precios:gestionar`).
"""
from __future__ import annotations

from fastapi import HTTPException

from ..models import ListaAsignacion, ListaPrecios

PERMISO = "lista_precios:gestionar"


def _renglones(db, tenant_id, *, cliente_id=None, sucursal_id=None, proyecto_id=None):
    """Los renglones de ESE lugar: sin serie y con exactamente estas dimensiones.

    El del proyecto se busca sólo por proyecto: los renglones viejos traen
    además el cliente (y a veces la plaza) del proyecto, y siguen siendo suyos.
    """
    q = db.query(ListaAsignacion).filter(
        ListaAsignacion.tenant_id == tenant_id,
        ListaAsignacion.serie_id.is_(None),
    )
    if proyecto_id is not None:
        return q.filter(ListaAsignacion.proyecto_id == proyecto_id)
    q = q.filter(ListaAsignacion.proyecto_id.is_(None), ListaAsignacion.cliente_id == cliente_id)
    if sucursal_id is None:
        return q.filter(ListaAsignacion.sucursal_id.is_(None))
    return q.filter(ListaAsignacion.sucursal_id == sucursal_id)


def listas_de_clientes(db, tenant_id, cliente_ids) -> dict:
    """{cliente_id: (lista_id, lista_nombre)} de la lista «en cualquier plaza»."""
    if not cliente_ids:
        return {}
    filas = (
        db.query(ListaAsignacion.cliente_id, ListaPrecios.id, ListaPrecios.nombre)
        .join(ListaPrecios, ListaPrecios.id == ListaAsignacion.lista_id)
        .filter(
            ListaAsignacion.tenant_id == tenant_id,
            ListaAsignacion.cliente_id.in_(list(cliente_ids)),
            ListaAsignacion.sucursal_id.is_(None),
            ListaAsignacion.serie_id.is_(None),
            ListaAsignacion.proyecto_id.is_(None),
        )
        .all()
    )
    return {c: (lid, nombre) for c, lid, nombre in filas}


def listas_de_vinculos(db, tenant_id, pares) -> dict:
    """{(cliente_id, sucursal_id): (lista_id, lista_nombre)} del vínculo."""
    if not pares:
        return {}
    clientes = {c for c, _ in pares}
    filas = (
        db.query(ListaAsignacion.cliente_id, ListaAsignacion.sucursal_id,
                 ListaPrecios.id, ListaPrecios.nombre)
        .join(ListaPrecios, ListaPrecios.id == ListaAsignacion.lista_id)
        .filter(
            ListaAsignacion.tenant_id == tenant_id,
            ListaAsignacion.cliente_id.in_(list(clientes)),
            ListaAsignacion.sucursal_id.isnot(None),
            ListaAsignacion.serie_id.is_(None),
            ListaAsignacion.proyecto_id.is_(None),
        )
        .all()
    )
    return {(c, s): (lid, nombre) for c, s, lid, nombre in filas if (c, s) in pares}


def fijar_lista(db, ctx, lista_id, *, cliente_id=None, sucursal_id=None, proyecto_id=None) -> None:
    """Pone (o quita, con None) la lista de ese lugar. Cambia el renglón que ya
    existe en lugar de agregar otro: dos renglones del mismo lugar discreparían."""
    actuales = _renglones(
        db, ctx.tenant_id, cliente_id=cliente_id, sucursal_id=sucursal_id, proyecto_id=proyecto_id,
    ).all()
    # Las fichas mandan la lista con todo lo demás: si no cambió, no hay nada
    # que hacer ni permiso que pedir (editar el teléfono no exige el de listas).
    if lista_id is None and not actuales:
        return
    if actuales and all(a.lista_id == lista_id for a in actuales):
        return
    if not ctx.has(PERMISO):
        raise HTTPException(
            status_code=403,
            detail="Tu usuario no puede cambiar qué lista de precios se cobra",
        )
    if lista_id is None:
        for a in actuales:
            db.delete(a)
        return
    existe = (
        db.query(ListaPrecios.id)
        .filter(ListaPrecios.id == lista_id, ListaPrecios.tenant_id == ctx.tenant_id,
                ListaPrecios.deleted_at.is_(None))
        .first()
    )
    if existe is None:
        raise HTTPException(status_code=422, detail="Esa lista de precios no existe")
    if actuales:
        for a in actuales:
            a.lista_id = lista_id
        return
    db.add(ListaAsignacion(
        tenant_id=ctx.tenant_id, lista_id=lista_id,
        cliente_id=cliente_id, sucursal_id=sucursal_id, proyecto_id=proyecto_id,
    ))
