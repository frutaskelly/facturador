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
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, ListaPrecios
from ...schemas.cliente import ClienteCreate, ClienteOut, ClienteUpdate
from ...schemas.common import Page
from ...services.cliente_codigo import generate_cliente_codigo
from ...services.facturama import FacturamaClient, FacturamaError
from ...services.rfc import validar_rfc_local
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

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


@router.post("", response_model=ClienteOut, status_code=status.HTTP_201_CREATED)
def create_cliente(
    payload: ClienteCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, ListaPrecios, payload.lista_precios_id, "lista_precios_id")
    data = payload.model_dump()
    _validar_datos_fiscales(data)
    # El código se genera SIEMPRE en el servidor; se ignora cualquier valor enviado.
    data.pop("codigo", None)
    codigo = generate_cliente_codigo(db, ctx.tenant_id)
    obj = Cliente(**data, codigo=codigo, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


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
