"""Espejo del catálogo de artículos de SAE (INVE02 / INVE03…).

Solo entra lo que SAE dice; aquí nadie inventa claves. Desde el 26-sep-2026 el
Facturador lee INVE él mismo (el reloj del espejo, `claves_sae.
sincronizar_catalogo`) y, cuando ACABA de escribir un alta o un cambio en SAE,
refleja aquí lo que SAE confirmó (`reflejar_escritura`). Antes lo depositaba el
bot por HTTP; esa ruta sigue abierta y pasa por el mismo depósito.

Nació para una cosa — que el masivo avise ANTES de generarse cuando una partida
lleva una clave que SAE no conoce, en vez de que el operador lo descubra tras
importar, con la factura ya emitida a medias (caso FRESADOMOPZ, 14-sep-2026) —
y hoy también es el candado «esa clave ya existe en SAE» de las altas y la
búsqueda de claves del bot (`GET /productos/claves-sae`).

`activa` refleja el STATUS de SAE: una clave dada de baja EXISTE pero no
factura, así que se reporta distinto — el operador necesita saber cuál de los
dos problemas tiene enfrente.
"""
from sqlalchemy import Boolean, Column, DateTime, String, UniqueConstraint, text

from ..core.db import Base
from .base import tenant_fk, uuid_pk


def norm_clave(valor: str | None) -> str:
    """Trim + mayúsculas. SAE guarda CVE_ART con relleno y el cruce falla por
    un espacio — misma normalización al depositar y al consultar."""
    return (valor or "").strip().upper()


class ClaveSae(Base):
    __tablename__ = "claves_sae"
    __table_args__ = (
        UniqueConstraint("tenant_id", "empresa", "clave", name="uq_clave_sae_tenant_empresa"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    empresa = Column(String(4), nullable=False)
    clave = Column(String(50), nullable=False)
    descripcion = Column(String(254))
    activa = Column(Boolean, nullable=False, server_default=text("true"))
    sincronizado_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
