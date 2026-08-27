"""Conexiones — cómo un sistema externo prueba que puede escribir aquí.

De la clave solo vive su SHA-256 (`clave_hash`) y los últimos caracteres
(`clave_pista`), lo justo para nombrarla en pantalla. El texto completo se
enseña una vez al generarla y después no existe en ningún lado: si se pierde, se
genera otra y la anterior deja de servir.

Alcance fijo y deliberadamente corto (ver `PERMISOS_CONEXION` en core/rbac.py):
dejar órdenes en la bandeja y leer catálogos para cruzarlas. Nada de CFDI, nada
de borrar, nada de usuarios.
"""
import hashlib
import secrets

from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk

# El prefijo hace la clave reconocible de un vistazo (en un chat, en un log) y
# permite distinguirla de un JWT sin intentar verificarla.
CLAVE_PREFIJO = "fi_ss_"

TIPOS = ("SMART_SUPPLY",)
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
