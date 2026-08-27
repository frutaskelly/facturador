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

La UBICACIÓN queda FUERA de esa cascada a propósito (27-ago-2026). Un punto de
descarga —un hospital, un plantel— dice DÓNDE se entrega, no A QUIÉN se le
factura: Balles y Jubran son dos razones sociales que comparten los mismos
puntos de entrega. Si la ubicación votara por un cliente, toda orden de Jubran
saldría «ambigua» contra Balles. Se usa solo para el destino, ya sabiendo el
cliente (`resolver_destino`), y el texto viaja siempre a las observaciones del
documento — que es donde el negocio lo lee.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Cliente, ClienteExterno, Sucursal
from .producto_match import normalizar

# Mayor a menor especificidad. El resolutor recorre esta lista en orden.
# UBICACION NO está: no identifica al cliente (ver el docstring del módulo).
PRIORIDAD = ("RFC", "SAE", "PROYECTO", "NOMBRE", "WHATSAPP")

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


def buscar_equivalencia(
    db: Session, tenant_id: UUID, sistema: str, clave: str, *, solo_confirmadas: bool = False
) -> Optional[ClienteExterno]:
    """La equivalencia registrada para esa clave, o None.

    El filtro de `tenant_id` es explícito y NO se delega a la RLS: los scripts de
    mantenimiento abren la sesión como owner (la RLS está ENABLE, no FORCE) y sin
    él verían —y reapuntarían— las filas de otro inquilino.
    """
    norm = normalizar_clave(sistema, clave)
    if not norm:
        return None
    q = db.query(ClienteExterno).filter(
        ClienteExterno.tenant_id == tenant_id,
        ClienteExterno.sistema == sistema,
        ClienteExterno.clave_normalizada == norm,
    )
    if solo_confirmadas:
        q = q.filter(ClienteExterno.confianza == "CONFIRMADA")
    return q.one_or_none()


def _buscar(db: Session, tenant_id: UUID, sistema: str, clave: str) -> Optional[ClienteExterno]:
    """Equivalencia utilizable para resolver: confirmada y con cliente vivo.

    Un cliente borrado deja su equivalencia huérfana; si se devolviera, la orden
    quedaría marcada «lista» y reventaría al crear la remisión. Que se comporte
    como inexistente la manda a PENDIENTE, que es lo correcto.
    """
    hit = buscar_equivalencia(db, tenant_id, sistema, clave, solo_confirmadas=True)
    if hit is None:
        return None
    vivo = (
        db.query(Cliente.id)
        .filter(Cliente.id == hit.cliente_id, Cliente.deleted_at.is_(None))
        .first()
    )
    return hit if vivo is not None else None


def resolver(db: Session, tenant_id: UUID, pistas: list[Pista]) -> Resolucion:
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
            hit = _buscar(db, tenant_id, sistema, p.clave)
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
    return res


def _sucursal_viva(db: Session, sucursal_id, cliente_id: UUID):
    """La sucursal, solo si vive Y es de ese cliente.

    Lo segundo importa: los puntos de entrega se comparten entre razones
    sociales (Balles/Jubran), así que una equivalencia puede apuntar a la
    sucursal de otro cliente si alguien la reapuntó. Mandar la remisión ahí
    resolvería la serie y los precios con datos mezclados.
    """
    if sucursal_id is None:
        return None
    hit = (
        db.query(Sucursal.id)
        .filter(
            Sucursal.id == sucursal_id,
            Sucursal.cliente_id == cliente_id,
            Sucursal.deleted_at.is_(None),
        )
        .first()
    )
    return hit[0] if hit else None


def resolver_destino(
    db: Session, tenant_id: UUID, cliente_id: UUID, texto: str, *, perfil: str = ""
) -> Optional[UUID]:
    """A qué SUCURSAL va una entrega, con el cliente ya decidido.

    Dos caminos, en orden: la equivalencia UBICACION registrada para ese punto
    de descarga, y el cruce directo contra el catálogo de sucursales del cliente
    (por código de 3 letras o por nombre) para cuando el documento nombra la
    sucursal misma («Tabasco») en vez de un punto dentro de ella.

    Sin hit devuelve None: la remisión sale igual, y el punto de descarga viaja
    en las observaciones — no es un error, es lo normal para un hospital nuevo.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    clave = f"{perfil.strip().lower()}:{texto}" if perfil else texto
    hit = _buscar(db, tenant_id, "UBICACION", clave)
    if hit is not None and hit.cliente_id == cliente_id:
        viva = _sucursal_viva(db, hit.sucursal_id, cliente_id)
        if viva is not None:
            return viva
    return resolver_sucursal_por_texto(db, cliente_id, texto)


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
                ClienteExterno.tenant_id == tenant_id,
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
