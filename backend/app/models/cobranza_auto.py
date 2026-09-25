"""Cobranza automática — el estado de cuenta que sale solo cada semana.

Tres piezas, todas por tenant y todas editables desde la pantalla:

- `CobranzaConfig` (una fila por tenant): cuándo sale, qué se incluye, a partir
  de cuántos días se escala y a quién, qué se adjunta y si se manda solo o
  espera a que alguien lo apruebe (modo REVISION).
- `CobranzaContacto`: a quién se le cobra. Uno por cliente y, si hace falta,
  uno por SERIE (en EHMO cada plaza factura con la suya y paga otra persona).
  `serie` NULL = todas las series del cliente en un solo estado de cuenta.
  `pausado` saca al cliente (o a esa serie) de la cobranza automática.
- `CobranzaEnvio`: la cola y la bitácora. Cada semana se genera una fila por
  contacto con saldo; en REVISION se queda PENDIENTE hasta que alguien la
  aprueba. Nunca dos filas para el mismo contacto y corte (índice único).
"""
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk

COBRANZA_MODOS = ("REVISION", "AUTOMATICO")
# PENDIENTE = en cola · ENVIANDO = reclamado por quien lo manda · ENVIADO ·
# ERROR = el SMTP lo rechazó · DESCARTADO = alguien lo quitó, o ya no hay saldo.
COBRANZA_ENVIO_ESTADOS = ("PENDIENTE", "ENVIANDO", "ENVIADO", "ERROR", "DESCARTADO")


class CobranzaConfig(Base, TimestampMixin):
    __tablename__ = "cobranza_config"

    id = uuid_pk()
    tenant_id = tenant_fk()
    activo = Column(Boolean, nullable=False, server_default=text("false"))
    modo = Column(String(12), nullable=False, server_default="REVISION")
    # 0 = lunes … 6 = domingo (datetime.weekday). Hora local de `zona`.
    dia_semana = Column(Integer, nullable=False, server_default=text("0"))
    hora = Column(Integer, nullable=False, server_default=text("8"))
    zona = Column(String(40), nullable=False, server_default="America/Mexico_City")
    # False = el estado de cuenta solo sale si hay algo VENCIDO.
    incluir_por_vencer = Column(Boolean, nullable=False, server_default=text("true"))
    saldo_minimo = Column(Numeric(18, 2), nullable=False, server_default=text("100"))
    # A partir de cuántos días de vencida la factura más vieja se copia a `escalar_cc`.
    escalar_dias = Column(Integer, nullable=False, server_default=text("30"))
    escalar_cc = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Copia en TODOS los envíos (p. ej. cobranza@ del propio negocio).
    cc_siempre = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    adjuntar_pdf = Column(Boolean, nullable=False, server_default=text("true"))
    adjuntar_excel = Column(Boolean, nullable=False, server_default=text("true"))
    # Candado: no se manda nada si el espejo de SAE no reportó en estas horas
    # (los pagos llegan por ahí: cobrar con el espejo viejo es cobrar lo pagado).
    # 0 = sin candado (tenants que no usan SAE).
    espejo_max_horas = Column(Integer, nullable=False, server_default=text("6"))
    asunto = Column(String(200))
    mensaje = Column(Text)
    ultima_generacion = Column(Date)


class CobranzaContacto(Base, TimestampMixin):
    __tablename__ = "cobranza_contactos"

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    serie = Column(String(10))
    correos = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cc = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    pausado = Column(Boolean, nullable=False, server_default=text("false"))
    motivo_pausa = Column(String(254))


class CobranzaEnvio(Base, TimestampMixin):
    __tablename__ = "cobranza_envios"

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    serie = Column(String(10))
    corte = Column(Date, nullable=False)
    estado = Column(String(12), nullable=False, server_default="PENDIENTE")
    # MANUAL = alguien presionó «Generar ahora» · PROGRAMADO = el reloj semanal
    origen = Column(String(12), nullable=False, server_default="PROGRAMADO")
    para = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cc = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # La foto al generar (lo que se revisa); al enviar se recalcula.
    saldo = Column(Numeric(18, 2), nullable=False, server_default=text("0"))
    vencido = Column(Numeric(18, 2), nullable=False, server_default=text("0"))
    facturas = Column(Integer, nullable=False, server_default=text("0"))
    dias_max_vencida = Column(Integer, nullable=False, server_default=text("0"))
    escalado = Column(Boolean, nullable=False, server_default=text("false"))
    error = Column(Text)
    aprobado_por = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    enviado_at = Column(DateTime(timezone=True))
