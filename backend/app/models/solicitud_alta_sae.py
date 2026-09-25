"""Altas de producto en SAE — la cola entre el Facturador y el conector.

Meta del dueño: «Alta de productos: WhatsApp → Facturador → SAE (las 4
empresas)». Hoy el bot inserta directo en INVE02 (sheets_push.cmd_producto_sae)
y solo en la empresa 02: el producto nace en SAE y el Facturador se enatera
después, por el espejo. Aquí se invierte, igual que se invirtió la ingesta de
órdenes: el producto nace en el CATÁLOGO DEL FACTURADOR y la alta en SAE queda
pedida en esta cola.

Por qué una cola y no una llamada: el backend NO ve SAE (quien lo consulta es
el conector, con sqlcmd desde la Mac), exactamente como el espejo de facturas
—de ahí que este modelo copie `espejo_syncs`.

Y por qué el resultado se guarda POR EMPRESA: **nunca se reintenta una
escritura a SAE**. Un INSERT repetido duplica el producto, así que cada empresa
se cierra por separado y una que falló se queda ERROR para que la vea una
persona; el reintento a ciegas no existe en este camino.
"""
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk

# PARCIAL = alguna empresa quedó creada y alguna falló. No es OK (falta trabajo)
# ni ERROR (algo sí se creó, y eso no se puede volver a intentar).
ALTA_SAE_ESTADOS = ("PENDIENTE", "EN_CURSO", "OK", "PARCIAL", "ERROR")
ALTA_SAE_TIPOS = ("ALTA", "CAMBIO")


class SolicitudAltaSae(Base):
    __tablename__ = "solicitudes_alta_sae"

    id = uuid_pk()
    tenant_id = tenant_fk()
    estado = Column(String(10), nullable=False, server_default="PENDIENTE")
    # ALTA = crear el artículo · CAMBIO = modificar uno que ya existe (0089).
    # En un CAMBIO, `datos` lleva sólo los campos a cambiar.
    tipo = Column(String(8), nullable=False, server_default="ALTA")
    # WHATSAPP = lo pidió el bot · UI = alguien desde la pantalla de catálogo
    origen = Column(String(12), nullable=False, server_default="UI")
    # El producto del catálogo que se está dando de alta allá. Puede venir vacío
    # (una alta pedida por clave, sin producto aún) — por eso es nullable.
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id", ondelete="SET NULL"),
                         index=True)
    # La clave con la que nacerá en SAE (CVE_ART). Es la identidad de la alta:
    # el candado de duplicados se hace contra ella, no contra producto_id.
    clave = Column(String(20), nullable=False, index=True)
    # Todo lo que SAE necesita para el INSERT, tal como lo resolvió quien pidió:
    # descripcion, unidad, linea, esquema, sat, sat_unidad. Se guarda completo a
    # propósito: si el catálogo cambia mañana, la alta que se aplicó es ESTA.
    datos = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Las empresas donde hay que crearlo: ["02","03","04","05"].
    empresas = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    solicitada_por = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    solicitada_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    iniciada_at = Column(DateTime(timezone=True))
    terminada_at = Column(DateTime(timezone=True))
    # Lo que contestó SAE por empresa: {"02": {"ok": true, "clave": "..."},
    # "03": {"ok": false, "error": "..."}}. Es la única fuente de qué se creó.
    resultado = Column(JSONB)
    # Por qué falló, en una línea, para el acuse de WhatsApp y el chip de la UI.
    motivo = Column(Text)
