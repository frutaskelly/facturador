"""El SKU del cliente, capturado desde la lista de precios.

Dueño (2-oct-2026): Balles y Jubran necesitan SU SKU en el XML de la factura.
Comparten una lista de precios, así que ahí se captura —uno por uno o en el
Excel de la lista— y se activa por lista (`lleva_sku_cliente`).

Dónde vive: en el catálogo de CADA cliente asignado a la lista
(`producto_clientes.codigo_cliente`, fila genérica, con la unidad en
`presentacion`; vacía = la base). Es lo que ya leen los dos caminos:
  * el XML que timbra el Facturador → NoIdentificacion = SKU del cliente o, si
    no tiene, el SKU interno (services/cfdi.py);
  * el masivo de SAE → el artículo con el que SAE factura y que imprime en SU
    XML (services/export_sae.codigo_cliente_de). Por eso a un cliente que se
    factura en SAE su SKU le tiene que existir allá: se avisa, no se bloquea.

Un cliente lleva UN SKU por producto (una fila genérica por cliente y producto):
si ya tiene uno en otra unidad, no se pisa en silencio.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import ClaveSae, Cliente, ClienteExterno, ListaAsignacion, ListaPrecios, Precio, Producto, ProductoCliente
from .inventario import presentacion_declarada


def _norm(sku: Optional[str]) -> Optional[str]:
    s = " ".join((sku or "").split()).upper()[:50]
    return s or None


def _unidad(pc: ProductoCliente, prod: Producto) -> str:
    return (pc.presentacion or prod.unidad_base or "").strip().upper()


def clientes_de_lista(db: Session, tenant_id: UUID, lista_id: UUID) -> list[Cliente]:
    ids = {r[0] for r in db.query(ListaAsignacion.cliente_id).filter(
        ListaAsignacion.tenant_id == tenant_id, ListaAsignacion.lista_id == lista_id,
        ListaAsignacion.cliente_id.isnot(None))}
    if not ids:
        return []
    return (db.query(Cliente).filter(Cliente.id.in_(ids), Cliente.deleted_at.is_(None))
            .order_by(Cliente.legal_name.asc()).all())


def _filas_catalogo(db: Session, tenant_id: UUID, cliente_ids: list, producto_ids) -> dict:
    return {
        (pc.cliente_id, pc.producto_id): pc
        for pc in db.query(ProductoCliente).filter(
            ProductoCliente.tenant_id == tenant_id,
            ProductoCliente.cliente_id.in_(cliente_ids or [None]),
            ProductoCliente.producto_id.in_(list(producto_ids) or [None]),
            ProductoCliente.sucursal_id.is_(None))
    }


def renglon(clientes: list[Cliente], filas: dict, prod: Producto, presentacion: str) -> dict:
    pres = presentacion.strip().upper()
    por_cliente: dict[str, Optional[str]] = {}
    for c in clientes:
        pc = filas.get((c.id, prod.id))
        ok = pc is not None and (pc.codigo_cliente or "").strip() and _unidad(pc, prod) == pres
        por_cliente[str(c.id)] = pc.codigo_cliente.strip().upper() if ok else None
    valores = set(por_cliente.values())
    return {"producto_id": prod.id, "presentacion": pres,
            "sku": next(iter(valores)) if len(valores) == 1 else None,
            "distintos": len(valores) > 1, "por_cliente": por_cliente}


def skus_de_lista(db: Session, tenant_id: UUID, lista: ListaPrecios) -> dict:
    clientes = clientes_de_lista(db, tenant_id, lista.id)
    pares = {(pid, pres) for pid, pres in db.query(Precio.producto_id, Precio.presentacion)
             .filter(Precio.lista_id == lista.id)}
    prods = {p.id: p for p in db.query(Producto).filter(Producto.id.in_({p for p, _ in pares} or [None]))}
    filas = _filas_catalogo(db, tenant_id, [c.id for c in clientes], prods)
    return {
        "clientes": [{"id": c.id, "nombre": c.legal_name} for c in clientes],
        "renglones": [renglon(clientes, filas, prods[pid], pres)
                      for pid, pres in sorted(pares, key=lambda x: (str(x[0]), x[1])) if pid in prods],
    }


def _empresas_sae(db: Session, tenant_id: UUID, cliente_ids: list) -> dict:
    """{cliente_id: {empresas SAE}} de los clientes que se facturan en SAE."""
    out: dict = {}
    for cid, clave in db.query(ClienteExterno.cliente_id, ClienteExterno.clave).filter(
            ClienteExterno.tenant_id == tenant_id, ClienteExterno.sistema == "SAE",
            ClienteExterno.confianza == "CONFIRMADA", ClienteExterno.cliente_id.in_(cliente_ids or [None])):
        emp = (clave or "").split(":")[0].strip()
        if emp:
            out.setdefault(cid, set()).add(emp)
    return out


def guardar_sku(db: Session, tenant_id: UUID, lista: ListaPrecios, producto_id: UUID, presentacion: str,
                sku: Optional[str], user_id: Optional[UUID], *, clientes: Optional[list[Cliente]] = None,
                avisar: bool = True) -> dict:
    """Pone (o quita, con sku vacío) el SKU en el catálogo de cada cliente de la lista."""
    clientes = clientes if clientes is not None else clientes_de_lista(db, tenant_id, lista.id)
    if not clientes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "La lista no tiene clientes asignados: el SKU se guarda en el catálogo de cada uno")
    prod = db.query(Producto).filter(Producto.id == producto_id, Producto.tenant_id == tenant_id,
                                     Producto.deleted_at.is_(None)).one_or_none()
    if prod is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Producto no encontrado")
    pres = presentacion.strip().upper()
    if not presentacion_declarada(prod, pres):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{prod.nombre} no maneja la presentación {pres}")
    sku = _norm(sku)
    base = (prod.unidad_base or "").strip().upper()
    filas = _filas_catalogo(db, tenant_id, [c.id for c in clientes], [prod.id])
    for c in clientes:
        pc = filas.get((c.id, prod.id))
        if pc is not None and (pc.codigo_cliente or "").strip() and _unidad(pc, prod) != pres and sku:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{c.legal_name} ya tiene el SKU {pc.codigo_cliente} de {prod.nombre} en {_unidad(pc, prod)}: "
                "un cliente lleva un solo SKU por producto. Quítalo de ese renglón primero.")
    for c in clientes:
        pc = filas.get((c.id, prod.id))
        if pc is None:
            if not sku:
                continue
            pc = ProductoCliente(tenant_id=tenant_id, cliente_id=c.id, producto_id=prod.id, created_by=user_id)
            db.add(pc)
            filas[(c.id, prod.id)] = pc
        elif not sku and _unidad(pc, prod) != pres:
            continue
        pc.codigo_cliente = sku
        if sku:
            pc.presentacion = None if pres == base else pres
        if hasattr(pc, "updated_by"):
            pc.updated_by = user_id
    db.flush()

    avisos = []
    if sku and avisar:
        emps = _empresas_sae(db, tenant_id, [c.id for c in clientes])
        for c in clientes:
            for emp in sorted(emps.get(c.id, ())):
                existe = db.query(ClaveSae.id).filter(
                    ClaveSae.tenant_id == tenant_id, ClaveSae.empresa == emp, ClaveSae.activa.is_(True),
                    func.upper(func.btrim(ClaveSae.clave)) == sku).first()
                if not existe:
                    avisos.append(f"{c.legal_name} se factura en SAE {emp} y {sku} no existe ahí: "
                                  "su factura no saldrá hasta darlo de alta en SAE")
    return {**renglon(clientes, filas, prod, pres), "avisos": avisos}
