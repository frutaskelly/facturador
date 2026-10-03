"""Conexiones — cómo un sistema externo prueba que puede escribir aquí.

De la clave solo vive su SHA-256 (`clave_hash`) y los últimos caracteres
(`clave_pista`), lo justo para nombrarla en pantalla. El texto completo se
enseña una vez al generarla y después no existe en ningún lado: si se pierde, se
genera otra y la anterior deja de servir.

Alcance fijo y deliberadamente corto, y distinto por tipo (ver
`PERMISOS_POR_TIPO` en core/rbac.py): Smart Supply deja órdenes en la bandeja y
lee catálogos para cruzarlas; Mini Conta solo lee las líneas facturadas. Nada de
CFDI, nada de borrar, nada de usuarios.

Las dos usan el mismo prefijo `fi_ss_`: auth reconoce la clave por él y el tipo
sale de la fila, no del texto.

Smart Supply tiene UNA clave por empresa. Mini Conta tiene una por CUENTA (cada
cuenta de Mini Conta es un cliente aparte), y cada una lleva su `alcance`: qué
series y qué clientes puede leer y si comparte el catálogo. NULL = la clave de
antes de 0092, sin límite, hasta que el dueño le ponga uno.

El panel de Smart Supply (`SMART_SUPPLY_PANEL`, 0098) también va por CUENTA:
cada bodega/plaza de Smart Supply lee con su clave solo su facturado, su
remisionado y sus OC (ver services/smart_supply.py). Su `alcance` tiene otra
forma que el de Mini Conta y, sin él, no lee nada.
"""
import hashlib
import secrets

from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk

# El prefijo hace la clave reconocible de un vistazo (en un chat, en un log) y
# permite distinguirla de un JWT sin intentar verificarla.
CLAVE_PREFIJO = "fi_ss_"

TIPOS = ("SMART_SUPPLY", "MINI_CONTA", "SMART_SUPPLY_PANEL")
# Tipos con una clave por cuenta (varias vivas a la vez, cada una con su
# alcance). El resto: una viva por empresa.
POR_CUENTA = ("MINI_CONTA", "SMART_SUPPLY_PANEL")
ESTADOS = ("PENDIENTE", "ACTIVA", "REVOCADA")


def generar_clave() -> str:
    """Clave nueva en bloques de 4, para poder dictarla o teclearla sin errores."""
    crudo = secrets.token_hex(12).upper()          # 24 hex = 96 bits
    bloques = "-".join(crudo[i:i + 4] for i in range(0, len(crudo), 4))
    return f"{CLAVE_PREFIJO}{bloques}"


def hash_clave(clave: str) -> str:
    return hashlib.sha256(clave.strip().encode("utf-8")).hexdigest()


def pista_de(clave: str) -> str:
    return clave.strip()[-4:]


class Conexion(Base):
    __tablename__ = "conexiones"

    id = uuid_pk()
    tenant_id = tenant_fk()
    tipo = Column(String(30), nullable=False)
    nombre = Column(String(80), nullable=False)
    clave_hash = Column(String(64), nullable=False, unique=True)
    clave_pista = Column(String(8), nullable=False)
    estado = Column(String(12), nullable=False, server_default="PENDIENTE")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    activada_at = Column(DateTime(timezone=True))
    ultimo_uso_at = Column(DateTime(timezone=True))
    revocada_at = Column(DateTime(timezone=True))
    alcance = Column(JSONB)

    # `alcance` es un JSONB cuya forma depende del tipo. La respuesta lo separa
    # en dos campos para que ninguna pantalla lea el de un tipo con la forma
    # del otro.
    @property
    def alcance_mini_conta(self):
        return self.alcance if self.tipo == "MINI_CONTA" else None

    @property
    def alcance_panel(self):
        return self.alcance if self.tipo == "SMART_SUPPLY_PANEL" else None


# Lo que se le hace a una conexión, para la pregunta «¿quién le quitó RRIO a
# Kelly Hidalgo y cuándo?». Desde oct-2026 el alcance se cambia en su lugar
# («Qué comparte», sin clave nueva) y Smart Supply lo aplica en su siguiente
# vuelta: sin bitácora, un cambio así no dejaba rastro.
CAMBIOS = ("CREADA", "EDITADA", "CLAVE_NUEVA", "DESCONECTADA")


class ConexionCambio(Base):
    """Bitácora append-only de una conexión. NUNCA guarda la clave: solo su
    pista (los últimos 4), que ya se enseña en la pantalla."""
    __tablename__ = "conexion_cambios"

    id = uuid_pk()
    tenant_id = tenant_fk(index=False)
    conexion_id = Column(UUID(as_uuid=True), ForeignKey("conexiones.id", ondelete="CASCADE"),
                         nullable=False)
    accion = Column(String(20), nullable=False)
    nombre_antes = Column(String(80))
    nombre_despues = Column(String(80))
    alcance_antes = Column(JSONB)
    alcance_despues = Column(JSONB)
    # CLAVE_NUEVA: la conexión que reemplazó (su historia sigue siendo de esta).
    reemplaza_a = Column(UUID(as_uuid=True), ForeignKey("conexiones.id", ondelete="SET NULL"))
    clave_pista = Column(String(8))
    # NULL si lo hizo una conexión y no una persona.
    hecho_por = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
