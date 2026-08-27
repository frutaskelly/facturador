"""Equivalencias de cliente — cómo se llama este cliente en los otros sistemas.

Es el patrón de `producto_alias` aplicado a clientes: una clave de un sistema
externo resuelve al cliente (y opcionalmente a su sucursal) sin que nadie tenga
que adivinar. Único por (tenant, sistema, clave_normalizada).

`sistema` dice de dónde viene la clave:
  RFC       — el RFC impreso en la orden de compra
  SAE       — '<empresa>:<cliente>' de ASPEL SAE (p. ej. '02:5')
  PROYECTO  — '<perfil>:<PROYECTO>' del bot (p. ej. 'ehmo:HOSPITALES'); si la
              fila trae `proyecto_id`, además etiqueta el documento con él
  NOMBRE    — la razón social tal como aparece dentro del documento
  UBICACION — '<perfil>:<ubicación>' (hospital/plantel) → cliente + SUCURSAL
  WHATSAPP  — el JID del grupo, solo para grupos de un solo cliente

`confianza` separa lo que un humano confirmó (CONFIRMADA) de lo que el bot
propuso solo (SUGERIDA); el resolutor únicamente usa las CONFIRMADAS, las otras
son la bandeja de revisión.
"""
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk

SISTEMAS = ("RFC", "SAE", "PROYECTO", "NOMBRE", "UBICACION", "WHATSAPP")


# Sistemas que dan CONTEXTO en vez de identificar: su clave puede repetirse
# entre clientes (Balles y Jubran comparten grupo y puntos de entrega). El resto
# identifica, y ahí una clave apunta a un solo cliente. La unicidad son dos
# índices parciales en la migración 0045 — no se puede expresar con
# UniqueConstraint, por eso `__table_args__` ya no la declara.
SISTEMAS_CONTEXTO = ("WHATSAPP", "UBICACION")


class ClienteExterno(Base):
    __tablename__ = "cliente_externos"

    id = uuid_pk()
    tenant_id = tenant_fk()
    sistema = Column(String(16), nullable=False)
    clave = Column(String(254), nullable=False)
    clave_normalizada = Column(String(254), nullable=False)
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # En una fila UBICACION: a qué sucursal pertenece ese punto de entrega.
    # En una WHATSAPP: la sucursal POR DEFECTO de ese grupo para ese cliente.
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="SET NULL"))
    # Solo en filas WHATSAPP: la serie que usa ESE grupo para ESE cliente. Vacío
    # = hereda la del cliente. Existe porque un cliente usa varias según la
    # operación por la que entre el pedido (EHMO: ZEHMOHOS y ZEHMOFAC).
    serie_factura_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"))
    serie_remision_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"))
    # Sólo en las filas de sistema PROYECTO: a qué proyecto del catálogo
    # corresponde esa clave. Es lo que hace que una orden que dice
    # "ehmo:HOSPITALES" se etiquete sola y le toquen los precios de esa
    # negociación, sin que nadie capture nada.
    proyecto_id = Column(UUID(as_uuid=True), ForeignKey("proyectos.id", ondelete="SET NULL"))
    origen = Column(String(12), nullable=False, server_default="MANUAL")        # MANUAL | BOT | IMPORT | IA
    confianza = Column(String(10), nullable=False, server_default="CONFIRMADA")  # CONFIRMADA | SUGERIDA
    notas = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
