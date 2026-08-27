"""Cruce de clientes — resuelve las pistas de un documento al cliente real.

El problema que resuelve: una orden de compra que llega por WhatsApp o correo no
trae el id del cliente, trae PISTAS — el RFC impreso, la razón social, el
proyecto, la ubicación de entrega, el grupo del que llegó. Cada pista es una
clave de un sistema externo y vive en `cliente_externos`.

Dos reglas que valen más que la cascada:

1. **Solo cuentan las equivalencias CONFIRMADAS.** Lo que el bot propuso solo
   (SUGERIDA) se muestra en la bandeja para que un humano lo apruebe; nunca
   decide por sí mismo. Si no, un error se propaga solo.

2. **Ante contradicción no se elige.** Si el RFC dice un cliente y el proyecto
   dice otro, `ambiguo=True` y `cliente_id=None`. Adivinar aquí significa
   facturarle a la empresa equivocada — es el único error de esta cascada que no
   se puede deshacer con un PATCH.

El orden de prioridad es por especificidad: el RFC identifica a una persona
moral sin lugar a dudas; el grupo de WhatsApp es el más débil (un mismo grupo
puede recibir órdenes de varias razones sociales, que es exactamente lo que pasa
hoy con EHMO/MAFAN y con Balles/Jubran).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ClienteExterno, Sucursal
from .producto_match import normalizar

# Mayor a menor especificidad. El resolutor recorre esta lista en orden.
PRIORIDAD = ("RFC", "SAE", "PROYECTO", "NOMBRE", "UBICACION", "WHATSAPP")

# El RFC no se normaliza como texto libre (los espacios y guiones sí estorban,
# pero las mayúsculas son significativas para leerlo). Se sube a mayúsculas y se
# quita todo lo que no sea alfanumérico.
_SOLO_ALNUM = {"RFC"}


def normalizar_clave(sistema: str, clave: str) -> str:
    """Normaliza una clave según su sistema. Truncada a 254 (ancho de columna)."""
    s = (clave or "").strip()
    if not s:
        return ""
    if sistema.upper() in _SOLO_ALNUM:
        return "".join(ch for ch in s.upper() if ch.isalnum())[:254]
    return normalizar(s)[:254]


@dataclass
class Pista:
    """Una clave candidata leída del documento."""
    sistema: str
    clave: str


@dataclass
class Resolucion:
    cliente_id: Optional[UUID] = None
    sucursal_id: Optional[UUID] = None
    via: Optional[str] = None            # el sistema que decidió
    ambiguo: bool = False
    motivo: Optional[str] = None         # por qué NO se pudo resolver
    # Todo lo que cruzó, para que la UI muestre el desacuerdo tal cual.
    coincidencias: list[dict] = field(default_factory=list)


def _buscar(db: Session, sistema: str, clave: str) -> Optional[ClienteExterno]:
    norm = normalizar_clave(sistema, clave)
    if not norm:
        return None
    return (
        db.query(ClienteExterno)
        .filter(
            ClienteExterno.sistema == sistema,
            ClienteExterno.clave_normalizada == norm,
            ClienteExterno.confianza == "CONFIRMADA",
        )
        .one_or_none()
    )


def resolver(db: Session, pistas: list[Pista]) -> Resolucion:
    """Cruza las pistas de un documento contra `cliente_externos`.

    Devuelve el cliente solo si todas las pistas que cruzaron apuntan al MISMO
    cliente. Si hay desacuerdo, `ambiguo=True` y no se elige ninguno.
    """
    res = Resolucion()
    encontrados: list[tuple[str, ClienteExterno]] = []

    # Por si llegan varias pistas del mismo sistema (dos ubicaciones, p. ej.).
    for sistema in PRIORIDAD:
        for p in pistas:
            if p.sistema.upper() != sistema:
                continue
            hit = _buscar(db, sistema, p.clave)
            if hit is not None:
                encontrados.append((sistema, hit))
                res.coincidencias.append({
                    "sistema": sistema,
                    "clave": p.clave,
                    "cliente_id": str(hit.cliente_id),
                    "sucursal_id": str(hit.sucursal_id) if hit.sucursal_id else None,
                })

    if not encontrados:
        res.motivo = "Ninguna pista del documento cruza con un cliente conocido"
        return res

    clientes = {e.cliente_id for _, e in encontrados}
    if len(clientes) > 1:
        res.ambiguo = True
        sistemas = ", ".join(sorted({s for s, _ in encontrados}))
        res.motivo = (
            f"Las pistas del documento apuntan a {len(clientes)} clientes distintos "
            f"({sistemas}); requiere revisión humana"
        )
        return res

    sistema_ganador, ganador = encontrados[0]
    res.cliente_id = ganador.cliente_id
    res.via = sistema_ganador

    # La sucursal la aporta la primera coincidencia que traiga una — típicamente
    # la de sistema UBICACION, que es la única que sabe a qué hospital va.
    for _, e in encontrados:
        if e.sucursal_id is not None:
            res.sucursal_id = e.sucursal_id
            break
    return res


def resolver_sucursal_por_texto(
    db: Session, cliente_id: UUID, texto: str
) -> Optional[UUID]:
    """Cruza el texto de la ubicación contra las sucursales del cliente.

    Dos pasos, más específico primero: código exacto (las 3 letras que el bot ya
    calcula: JUA, PAL, NIN) y luego nombre normalizado. Sin hit devuelve None —
    la remisión igual puede salir sin sucursal, no es un error.
    """
    norm = normalizar(texto or "")
    if not norm:
        return None
    sucs = (
        db.query(Sucursal)
        .filter(Sucursal.cliente_id == cliente_id, Sucursal.deleted_at.is_(None))
        .all()
    )
    codigo = norm.replace(" ", "").upper()
    for s in sucs:
        if (s.codigo or "").upper() == codigo:
            return s.id
    for s in sucs:
        if normalizar(s.nombre or "") == norm:
            return s.id
    return None


def aprender(
    db: Session,
    tenant_id: UUID,
    sistema: str,
    clave: str,
    cliente_id: UUID,
    *,
    sucursal_id: Optional[UUID] = None,
    origen: str = "MANUAL",
    confianza: str = "CONFIRMADA",
    user_id=None,
) -> Optional[ClienteExterno]:
    """Guarda (o reapunta) una equivalencia. Idempotente por clave normalizada.

    Reapuntar es deliberado: cuando un humano corrige el cruce en la bandeja, lo
    que quiere es que a partir de ahí resuelva al cliente correcto — no que
    conviva con el anterior.
    """
    norm = normalizar_clave(sistema, clave)
    if not norm:
        return None

    def _existente() -> Optional[ClienteExterno]:
        return (
            db.query(ClienteExterno)
            .filter(
                ClienteExterno.sistema == sistema,
                ClienteExterno.clave_normalizada == norm,
            )
            .one_or_none()
        )

    existing = _existente()
    if existing is not None:
        # Una SUGERIDA del bot nunca degrada una CONFIRMADA que ya puso una
        # persona: al revés sí (confirmar sube la confianza).
        if confianza == "SUGERIDA" and existing.confianza == "CONFIRMADA":
            return existing
        existing.cliente_id = cliente_id
        existing.sucursal_id = sucursal_id
        existing.origen = origen
        existing.confianza = confianza
        db.flush()
        return existing

    obj = ClienteExterno(
        tenant_id=tenant_id,
        sistema=sistema,
        clave=(clave or "").strip()[:254],
        clave_normalizada=norm,
        cliente_id=cliente_id,
        sucursal_id=sucursal_id,
        origen=origen,
        confianza=confianza,
        created_by=user_id,
    )
    try:
        # Savepoint: si otro request insertó la misma clave en paralelo, el
        # UNIQUE truena aquí sin tirar la transacción entera.
        with db.begin_nested():
            db.add(obj)
            db.flush()
        return obj
    except IntegrityError:
        existing = _existente()
        if existing is not None:
            existing.cliente_id = cliente_id
            existing.sucursal_id = sucursal_id
            existing.origen = origen
            existing.confianza = confianza
            db.flush()
        return existing
