"""Clientes / CRM — CRUD.

Reads gated by `menu:clientes` (a TOMADOR/CAJERO can look a customer up at the
POS); writes by `cliente:gestionar`. The optional `lista_precios_id` FK is
re-validated under the tenant scope before persisting.

The running accumulators (saldo_actual, ventas_ytd, ultima_venta_at,
ultimo_pago_at) are maintained by the operations/POS flows in later phases —
they are read-only here, never accepted from the client payload.
"""
from __future__ import annotations

from typing import Optional
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, ClienteExterno, ListaPrecios, Producto, ProductoCliente, Sucursal
from ...schemas.cliente import ClienteCreate, ClienteOut, ClienteUpdate
from ...schemas.cliente_externo import (
    ClienteExternoCreate,
    ClienteExternoOut,
    ResolucionOut,
    ResolverIn,
)
from ...schemas.common import Page
from ...schemas.producto import ProductoClienteOut, ProductoClienteUpsert
from ...services.cliente_codigo import generate_cliente_codigo
from ...services import cliente_match
from ...services.producto_match import aprender_alias
from ...services.facturama import FacturamaClient, FacturamaError
from ...services.rfc import validar_rfc_local
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

log = logging.getLogger(__name__)

router = APIRouter(prefix="/clientes", tags=["clientes"])

_READ = "menu:clientes"
_WRITE = "cliente:gestionar"
_DUP = "Ya existe un cliente con ese código"


@router.get("", response_model=Page[ClienteOut])
def list_clientes(
    q: Optional[str] = Query(default=None, max_length=254),
    tipo: Optional[str] = Query(default=None, max_length=20),
    status_: Optional[str] = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Cliente).filter(Cliente.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            Cliente.legal_name.ilike(like)
            | Cliente.rfc.ilike(like)
            | Cliente.codigo.ilike(like)
        )
    if tipo:
        query = query.filter(Cliente.tipo == tipo)
    if status_:
        query = query.filter(Cliente.status == status_)
    query = query.order_by(Cliente.legal_name.asc())
    return paginate(query, ClienteOut, limit, offset)


@router.get("/validar-rfc")
def validar_rfc(
    rfc: str = Query(..., min_length=10, max_length=15),
    nombre: Optional[str] = Query(default=None, max_length=254),
    cp: Optional[str] = Query(default=None, max_length=5),
    regimen: Optional[str] = Query(default=None, max_length=4),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Valida un RFC contra el SAT vía Facturama.

    Sin `nombre`/`cp`/`regimen`: solo formato + activo + localizado
    (GET /customers/status) — {Rfc, FormatoCorrecto, Activo, Localizado}.

    Con los tres presentes: valida ADEMÁS que la Razón social, el Código
    Postal y el Régimen Fiscal coincidan con lo que el SAT tiene registrado
    para ese RFC (POST /customers/validate) — {ExistRfc, MatchName,
    MatchZipCode, MatchFiscalRegime}. Atrapa un dato mal capturado antes de
    que el timbrado real lo rechace, en vez de descubrirlo hasta entonces.

    Consume 1 folio de Facturama por llamada (botón manual en el formulario
    de clientes).
    """
    rfc_u = rfc.strip().upper()
    # Filtro local: formato + dígito verificador. El sandbox de Facturama aprueba
    # cualquier RFC bien formado, así que esto atrapa typos (p. ej. ...V1 vs ...VA)
    # sin consultar al SAT ni gastar un folio.
    local = validar_rfc_local(rfc_u)
    if not (local["formato_ok"] and local["digito_ok"]):
        return {"Rfc": rfc_u, "FormatoCorrecto": False, "Activo": False, "Localizado": False}

    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Facturama no está configurado")
    try:
        if nombre and cp and regimen:
            return client.validar_completo(rfc_u, nombre.strip(), cp.strip(), regimen.strip())
        return client.validar_rfc(rfc_u)
    except FacturamaError as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo validar el RFC: {exc}")


# RFC genéricos del SAT: SÍ son válidos como RECEPTOR (a diferencia del emisor).
_RFC_GENERICOS = {"XAXX010101000", "XEXX010101000"}
_CP_CLIENTE_RE = re.compile(r"^\d{5}$")


def _validar_datos_fiscales(data: dict) -> None:
    """RFC y CP del receptor: el SAT rechaza el timbrado si están mal, así que
    se atrapan AQUÍ y no cuando ya hay una factura que no se puede emitir."""
    if "rfc" in data and data["rfc"] is not None:
        rfc = str(data["rfc"]).strip().upper()
        data["rfc"] = rfc
        if not rfc:
            raise HTTPException(status_code=422, detail="El RFC del cliente es obligatorio")
        if rfc not in _RFC_GENERICOS:
            v = validar_rfc_local(rfc)
            if not v["formato_ok"]:
                raise HTTPException(
                    status_code=422,
                    detail="El RFC no tiene un formato válido del SAT (3-4 letras + AAMMDD + homoclave).",
                )
            if not v["digito_ok"]:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"El RFC {rfc} no pasa el dígito verificador del SAT: hay un error de "
                        "captura (dígitos transpuestos o el último carácter). Verifícalo con "
                        "la constancia de situación fiscal del cliente."
                    ),
                )
    dom = data.get("domicilio_fiscal")
    if isinstance(dom, dict) and dom.get("cp") is not None:
        cp = str(dom.get("cp") or "").strip()
        if cp and not _CP_CLIENTE_RE.match(cp):
            raise HTTPException(
                status_code=422, detail="El código postal debe tener exactamente 5 dígitos."
            )


def _validar_contra_sat(rfc: str, nombre: str, cp: str, regimen: str) -> None:
    """Confirma con el SAT (vía Facturama) que RFC + razón social + CP + régimen
    corresponden entre sí. Un CP o un régimen que no casan con el RFC hacen que
    el SAT rechace el CFDI al timbrar: se atrapa aquí, al dar de alta.

    Si el PAC no responde, NO se bloquea el guardado (no dejar al usuario sin
    poder trabajar por una caída externa): solo se registra.
    """
    if not (rfc and nombre and cp and regimen):
        return  # datos incompletos: la validación completa no aplica
    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        return
    try:
        r = client.validar_completo(rfc, nombre, cp, regimen)
    except (FacturamaError, Exception) as exc:  # noqa: BLE001 — degradar, no bloquear
        log.warning("No se pudo validar el cliente contra el SAT: %s", exc)
        return

    problemas = []
    if r.get("ExistRfc") is False:
        problemas.append("el RFC no existe ante el SAT")
    if r.get("MatchName") is False:
        problemas.append("la razón social no coincide con la registrada")
    if r.get("MatchZipCode") is False:
        problemas.append(f"el código postal {cp} no corresponde a este RFC")
    if r.get("MatchFiscalRegime") is False:
        problemas.append("el régimen fiscal no coincide")
    if problemas:
        raise HTTPException(
            status_code=422,
            detail=(
                "El SAT no valida estos datos: "
                + "; ".join(problemas)
                + ". Corrígelos con la constancia de situación fiscal del cliente "
                "(si no, el timbrado será rechazado)."
            ),
        )


@router.post("", response_model=ClienteOut, status_code=status.HTTP_201_CREATED)
def create_cliente(
    payload: ClienteCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, ListaPrecios, payload.lista_precios_id, "lista_precios_id")
    data = payload.model_dump()
    _validar_datos_fiscales(data)
    _validar_contra_sat(
        str(data.get("rfc") or ""),
        str(data.get("legal_name") or ""),
        str((data.get("domicilio_fiscal") or {}).get("cp") or ""),
        str(data.get("regimen_fiscal") or ""),
    )
    # El código se genera SIEMPRE en el servidor; se ignora cualquier valor enviado.
    data.pop("codigo", None)
    codigo = generate_cliente_codigo(db, ctx.tenant_id)
    obj = Cliente(**data, codigo=codigo, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


# ─── Equivalencias: cómo se llama este cliente en los otros sistemas ──────────
# Declaradas ANTES de /{cliente_id} para que las rutas estáticas ganen: si no,
# FastAPI intentaría parsear "externos" como UUID y devolvería 422.


@router.post("/resolver", response_model=ResolucionOut)
def resolver_cliente(
    payload: ResolverIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cruza las pistas de un documento (RFC, proyecto, nombre, ubicación, grupo)
    contra las equivalencias registradas. No escribe nada.

    Si las pistas se contradicen devuelve `ambiguo=true` y NO elige cliente:
    adivinar aquí significaría facturarle a la empresa equivocada.
    """
    res = cliente_match.resolver(
        db, ctx.tenant_id,
        [cliente_match.Pista(sistema=p.sistema, clave=p.clave) for p in payload.pistas],
    )
    # Sin sucursal por equivalencia, se intenta contra el catálogo del cliente
    # (código de 3 letras y nombre) — es lo que resuelve una ubicación escrita
    # de otra forma sin tener que registrarla antes.
    if res.cliente_id and not res.sucursal_id and payload.ubicacion_texto:
        res.sucursal_id = cliente_match.resolver_sucursal_por_texto(
            db, res.cliente_id, payload.ubicacion_texto
        )

    out = ResolucionOut(
        cliente_id=res.cliente_id,
        sucursal_id=res.sucursal_id,
        via=res.via,
        ambiguo=res.ambiguo,
        motivo=res.motivo,
        coincidencias=res.coincidencias,
    )
    if res.cliente_id:
        cli = db.query(Cliente).filter(Cliente.id == res.cliente_id).one_or_none()
        out.cliente_nombre = cli.legal_name if cli else None
    if res.sucursal_id:
        suc = db.query(Sucursal).filter(Sucursal.id == res.sucursal_id).one_or_none()
        out.sucursal_nombre = suc.nombre if suc else None
    return out


@router.get("/externos", response_model=list[ClienteExternoOut])
def list_externos(
    cliente_id: Optional[UUID] = Query(default=None),
    sistema: Optional[str] = Query(default=None, max_length=16),
    confianza: Optional[str] = Query(default=None, max_length=10),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    q = db.query(ClienteExterno)
    if cliente_id:
        q = q.filter(ClienteExterno.cliente_id == cliente_id)
    if sistema:
        q = q.filter(ClienteExterno.sistema == sistema.upper())
    if confianza:
        q = q.filter(ClienteExterno.confianza == confianza.upper())
    return q.order_by(ClienteExterno.sistema, ClienteExterno.clave).all()


@router.post("/externos", response_model=ClienteExternoOut, status_code=status.HTTP_201_CREATED)
def crear_externo(
    payload: ClienteExternoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Registra (o reapunta) una equivalencia. Idempotente por clave normalizada."""
    ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
    if payload.sucursal_id is not None:
        suc = get_or_404(db, Sucursal, payload.sucursal_id)
        if suc.cliente_id != payload.cliente_id:
            raise HTTPException(
                status_code=422, detail="La sucursal no pertenece al cliente de la equivalencia"
            )
    # Una SUGERIDA no puede tocar una CONFIRMADA que ya puso una persona. Si se
    # intenta, la respuesta tiene que decirlo: devolver 201 con el cliente ANTERIOR
    # haría creer que quedó registrada una equivalencia que no se registró.
    previa = cliente_match.buscar_equivalencia(db, ctx.tenant_id, payload.sistema, payload.clave)
    if (
        previa is not None
        and payload.confianza == "SUGERIDA"
        and previa.confianza == "CONFIRMADA"
        and previa.cliente_id != payload.cliente_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Esa clave ya está confirmada para otro cliente; cámbiala como confirmada para reapuntarla",
        )
    obj = cliente_match.aprender(
        db,
        ctx.tenant_id,
        payload.sistema,
        payload.clave,
        payload.cliente_id,
        sucursal_id=payload.sucursal_id,
        serie_factura_id=payload.serie_factura_id,
        serie_remision_id=payload.serie_remision_id,
        origen=payload.origen,
        confianza=payload.confianza,
        user_id=ctx.user_id,
    )
    if obj is None:
        raise HTTPException(status_code=422, detail="La clave queda vacía al normalizarla")
    if payload.notas is not None:
        obj.notas = payload.notas
    db.flush()
    db.refresh(obj)
    return obj


@router.delete("/externos/{externo_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_externo(
    externo_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, ClienteExterno, externo_id)
    db.delete(obj)
    db.flush()


@router.get("/{cliente_id}", response_model=ClienteOut)
def get_cliente(
    cliente_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Cliente, cliente_id)


@router.patch("/{cliente_id}", response_model=ClienteOut)
def update_cliente(
    cliente_id: UUID,
    payload: ClienteUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Cliente, cliente_id)
    data = payload.model_dump(exclude_unset=True)
    _validar_datos_fiscales(data)
    # Solo si el cambio toca datos fiscales: no gastar un folio del PAC al
    # editar el teléfono. Se valida el resultado FINAL (lo guardado + el cambio).
    if {"rfc", "legal_name", "domicilio_fiscal", "regimen_fiscal"} & set(data):
        dom_final = data.get("domicilio_fiscal", obj.domicilio_fiscal) or {}
        _validar_contra_sat(
            str(data.get("rfc", obj.rfc) or ""),
            str(data.get("legal_name", obj.legal_name) or ""),
            str(dom_final.get("cp") or ""),
            str(data.get("regimen_fiscal", obj.regimen_fiscal) or ""),
        )
    # El código no se regenera ni se acepta en update: queda fijo desde la creación.
    data.pop("codigo", None)
    if "lista_precios_id" in data:
        ensure_fk(db, ListaPrecios, data["lista_precios_id"], "lista_precios_id")
    for key, value in data.items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.delete("/{cliente_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cliente(
    cliente_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Cliente, cliente_id)
    obj.deleted_at = func.now()
    db.flush()
    return None


# ─── Catálogo del cliente (cómo llama ESTE cliente a cada producto) ──────────
# codigo_cliente → NoIdentificacion y nombre_cliente → Descripcion del CFDI al
# timbrar (services/cfdi.py). Un producto interno, muchos nombres de cara al
# cliente — sin duplicar productos.


@router.get("/{cliente_id}/catalogo", response_model=list[ProductoClienteOut])
def catalogo_cliente(
    cliente_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    get_or_404(db, Cliente, cliente_id)
    rows = (
        db.query(ProductoCliente, Producto)
        .join(Producto, Producto.id == ProductoCliente.producto_id)
        .filter(
            ProductoCliente.cliente_id == cliente_id,
            Producto.deleted_at.is_(None),
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    return [
        ProductoClienteOut(
            producto_id=pc.producto_id,
            producto_sku=p.sku,
            producto_nombre=p.nombre,
            codigo_cliente=pc.codigo_cliente,
            nombre_cliente=pc.nombre_cliente,
            presentacion=pc.presentacion,
        )
        for pc, p in rows
    ]


@router.put("/{cliente_id}/catalogo/{producto_id}", response_model=ProductoClienteOut)
def upsert_catalogo_cliente(
    cliente_id: UUID,
    producto_id: UUID,
    payload: ProductoClienteUpsert,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    get_or_404(db, Cliente, cliente_id)
    prod = get_or_404(db, Producto, producto_id)
    codigo = (payload.codigo_cliente or "").strip() or None
    nombre = (payload.nombre_cliente or "").strip() or None
    if not codigo and not nombre:
        raise HTTPException(
            status_code=422,
            detail="Captura el código y/o el nombre que usa el cliente",
        )
    pc = (
        db.query(ProductoCliente)
        .filter(
            ProductoCliente.cliente_id == cliente_id,
            ProductoCliente.producto_id == producto_id,
        )
        .one_or_none()
    )
    if pc is None:
        pc = ProductoCliente(
            tenant_id=ctx.tenant_id, cliente_id=cliente_id, producto_id=producto_id
        )
        db.add(pc)
    pc.codigo_cliente = codigo
    pc.nombre_cliente = nombre
    if payload.presentacion is not None:
        pc.presentacion = payload.presentacion.strip().upper() or None
    db.flush()
    # El cruce de productos también aprende el nombre del cliente.
    if nombre:
        aprender_alias(db, ctx.tenant_id, nombre, producto_id, origen="MANUAL", user_id=ctx.user_id)
    return ProductoClienteOut(
        producto_id=producto_id,
        producto_sku=prod.sku,
        producto_nombre=prod.nombre,
        codigo_cliente=pc.codigo_cliente,
        nombre_cliente=pc.nombre_cliente,
        presentacion=pc.presentacion,
    )


@router.delete("/{cliente_id}/catalogo/{producto_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_catalogo_cliente(
    cliente_id: UUID,
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    get_or_404(db, Cliente, cliente_id)
    (
        db.query(ProductoCliente)
        .filter(
            ProductoCliente.cliente_id == cliente_id,
            ProductoCliente.producto_id == producto_id,
        )
        .delete()
    )
    db.flush()
    return None
