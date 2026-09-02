"""Sincronizaciones del espejo SAE — la fecha de «SAE actualizado» y el botón.

El backend NO ve SAE (quien lo consulta es el conector, sqlcmd desde la Mac),
así que «Sincronizar SAE» funciona por solicitud: el botón deja aquí una fila
PENDIENTE, el conector la reclama (EN_CURSO), corre el espejo y reporta el
resultado (OK/ERROR). Las pasadas automáticas de cada 30 min también reportan
al terminar — de ahí sale la fecha de última actualización aunque nadie
presione el botón.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk

ESPEJO_SYNC_ESTADOS = ("PENDIENTE", "EN_CURSO", "OK", "ERROR")


class EspejoSync(Base):
    __tablename__ = "espejo_syncs"

    id = uuid_pk()
    tenant_id = tenant_fk()
    estado = Column(String(10), nullable=False, server_default="PENDIENTE")
    # MANUAL = alguien presionó el botón · AUTOMATICA = la pasada del timer
    origen = Column(String(12), nullable=False, server_default="MANUAL")
    dias = Column(Integer, nullable=False, server_default=text("3"))
    solicitada_por = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    solicitada_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    iniciada_at = Column(DateTime(timezone=True))
    terminada_at = Column(DateTime(timezone=True))
    # El JSON que devuelve el conector (enviadas, canceladas, errores…), tal cual.
    resultado = Column(JSONB)
