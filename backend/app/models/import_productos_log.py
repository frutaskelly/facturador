"""Bitácora de la importación masiva de productos — de qué lote salió cada cosa.

`productos.created_by` contesta "quién creó ESTA fila"; esto contesta la otra
mitad: de qué pasada salió, con qué archivo, para qué clientes y cuánto tocó.
Nació del 16-sep-2026, cuando un producto con cinco filas de catálogo apuntando
a clientes equivocados no se le pudo atribuir a nadie: el Excel no se guarda y
la elección de clientes no dejaba rastro en ningún lado.

Se escribe en los DOS pasos del wizard, porque son dos llamadas distintas y los
clientes se eligen hasta el segundo:
  IMPORT   → POST /productos/importar (crea/vincula productos y precios)
  CATALOGO → POST /productos/catalogo-cliente-batch (escribe producto_clientes)
Es append-only y no la lee ningún flujo de negocio: existe para la pregunta
forense, y una bitácora que falla nunca tumba el alta que sí funcionó.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk

IMPORT_LOG_ORIGENES = ("IMPORT", "CATALOGO")


class ImportProductosLog(Base):
    __tablename__ = "import_productos_log"

    id = uuid_pk()
    tenant_id = tenant_fk()
    origen = Column(String(12), nullable=False, server_default="IMPORT")
    # NULL cuando quien importó fue una conexión y no una persona.
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    # El archivo no se conserva; su nombre sí — es con lo que el usuario lo
    # reconoce y lo puede volver a abrir.
    archivo_nombre = Column(String(254))
    # Los clientes elegidos, como lista de ids en texto.
    cliente_ids = Column(JSONB, nullable=False, server_default="[]")
    filas_enviadas = Column(Integer, nullable=False, server_default=text("0"))
    productos_creados = Column(Integer, nullable=False, server_default=text("0"))
    productos_vinculados = Column(Integer, nullable=False, server_default=text("0"))
    # Filas de producto_clientes escritas (nuevas + pisadas).
    catalogo_guardado = Column(Integer, nullable=False, server_default=text("0"))
    precios_guardados = Column(Integer, nullable=False, server_default=text("0"))
    filas_con_error = Column(Integer, nullable=False, server_default=text("0"))
    # El resto del resumen (omitidos, categorías creadas, lista destino…).
    detalle = Column(JSONB)
