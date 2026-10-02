"""Cobranza: los envíos del estado de cuenta (migraciones 0088 y 0097).

Cada estado de cuenta se configura por separado (dueño, 2-oct-2026):

- `CobranzaGrupo`: un ENVÍO. Una razón social o varias (EHMO + SUREÑA +
  MAFAN), la tabla del correo por proyecto/serie/sucursal/razón social, a
  quién se manda y cuándo: AUTOMATICO sale solo el día y la hora que diga;
  MANUAL, solo con el botón Enviar. Trae también lo que antes era general:
  saldo mínimo, escalamiento, adjuntos, asunto y mensaje.
- `CobranzaGrupoAlcance`: qué entra al envío, por razón social.
- `CobranzaEnvio`: la bitácora. Cada correo que salió (o no pudo salir), con
  su envío y la foto de lo que se cobró. Un programado por envío y día
  (índice parcial): el reloj y un reintento no mandan dos.
- `CobranzaConfig` (una por inquilino): lo general. El interruptor maestro
  de los automáticos, el candado del espejo de SAE y la copia fija a todos.
  Sus demás columnas son de antes de 0097 y ya no se usan.

`cobranza_contactos` (0088) se quedó en la base como respaldo; ya nada la lee.
"""
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk

COBRANZA_MODOS = ("AUTOMATICO", "MANUAL")
COBRANZA_AGRUPAR_POR = ("PROYECTO", "SERIE", "SUCURSAL", "CLIENTE")
# PENDIENTE = programado que espera al espejo · ENVIANDO = reclamado por quien
# lo manda · ENVIADO · ERROR = el SMTP lo rechazó o faltó el correo ·
# DESCARTADO = ya no había saldo (o quedó bajo el mínimo).
COBRANZA_ENVIO_ESTADOS = ("PENDIENTE", "ENVIANDO", "ENVIADO", "ERROR", "DESCARTADO")


class CobranzaConfig(Base, TimestampMixin):
    __tablename__ = "cobranza_config"

    id = uuid_pk()
    tenant_id = tenant_fk()
    # Interruptor maestro: apagado, ningún envío AUTOMATICO sale (los MANUAL sí).
    activo = Column(Boolean, nullable=False, server_default=text("false"))
    # Candado: no se cobra si el espejo de SAE no reportó en estas horas (los
    # pagos llegan por ahí: cobrar con el espejo viejo es cobrar lo pagado).
    # 0 = sin candado (inquilinos que no usan SAE).
    espejo_max_horas = Column(Integer, nullable=False, server_default=text("6"))
    # Copia en TODOS los envíos (p. ej. cobranza@ del propio negocio).
    cc_siempre = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    zona = Column(String(40), nullable=False, server_default="America/Mexico_City")
    # ── De antes de 0097 (ahora viven en cada envío); ya no se leen ──
    modo = Column(String(12), nullable=False, server_default="REVISION")
    dia_semana = Column(Integer, nullable=False, server_default=text("0"))
    hora = Column(Integer, nullable=False, server_default=text("8"))
    incluir_por_vencer = Column(Boolean, nullable=False, server_default=text("true"))
    saldo_minimo = Column(Numeric(18, 2), nullable=False, server_default=text("100"))
    escalar_dias = Column(Integer, nullable=False, server_default=text("30"))
    escalar_cc = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    adjuntar_pdf = Column(Boolean, nullable=False, server_default=text("true"))
    adjuntar_excel = Column(Boolean, nullable=False, server_default=text("true"))
    asunto = Column(String(200))
    mensaje = Column(Text)
    ultima_generacion = Column(Date)


class CobranzaGrupo(Base, TimestampMixin):
    """Un envío del estado de cuenta, configurado por separado."""
    __tablename__ = "cobranza_grupos"

    id = uuid_pk()
    tenant_id = tenant_fk()
    nombre = Column(String(80), nullable=False)
    agrupar_por = Column(String(10), nullable=False, server_default="PROYECTO")
    mostrar_antiguedad = Column(Boolean, nullable=False, server_default=text("false"))
    correos = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cc = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    modo = Column(String(12), nullable=False, server_default="MANUAL")
    # 0 = lunes … 6 = domingo (datetime.weekday). Hora local de la config.
    dia_semana = Column(Integer, nullable=False, server_default=text("0"))
    hora = Column(Integer, nullable=False, server_default=text("8"))
    # False = solo las vencidas (y sin vencidas no sale nada).
    incluir_por_vencer = Column(Boolean, nullable=False, server_default=text("true"))
    saldo_minimo = Column(Numeric(18, 2), nullable=False, server_default=text("100"))
    # A partir de cuántos días de vencida la factura más vieja se copia a `escalar_cc`.
    escalar_dias = Column(Integer, nullable=False, server_default=text("30"))
    escalar_cc = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    adjuntar_pdf = Column(Boolean, nullable=False, server_default=text("true"))
    adjuntar_excel = Column(Boolean, nullable=False, server_default=text("true"))
    asunto = Column(String(200))
    mensaje = Column(Text)
    nota = Column(String(254))


class CobranzaGrupoAlcance(Base):
    """Qué entra al envío, por razón social. Sin proyecto ni serie = la razón
    social completa (lo nuevo entra solo). Con proyecto = las facturas que se
    reportan en ese proyecto. Con serie = las de esa serie que no caen en
    ningún proyecto."""
    __tablename__ = "cobranza_grupo_alcance"

    id = uuid_pk()
    tenant_id = tenant_fk()
    grupo_id = Column(UUID(as_uuid=True), ForeignKey("cobranza_grupos.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    proyecto_id = Column(UUID(as_uuid=True), ForeignKey("proyectos.id", ondelete="CASCADE"), index=True)
    serie = Column(String(10))


class CobranzaEnvio(Base, TimestampMixin):
    __tablename__ = "cobranza_envios"

    id = uuid_pk()
    tenant_id = tenant_fk()
    grupo_id = Column(UUID(as_uuid=True), ForeignKey("cobranza_grupos.id", ondelete="SET NULL"), index=True)
    # El nombre del envío al mandarlo: la bitácora se lee aunque se borre.
    grupo_nombre = Column(String(80))
    # Solo en la bitácora vieja (0088), cuando se cobraba por cliente y serie.
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    serie = Column(String(10))
    corte = Column(Date, nullable=False)
    estado = Column(String(12), nullable=False, server_default="PENDIENTE")
    # MANUAL = alguien presionó Enviar · PROGRAMADO = salió sola en su día y hora
    origen = Column(String(12), nullable=False, server_default="PROGRAMADO")
    para = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cc = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # La foto de lo que se cobró en ese correo.
    saldo = Column(Numeric(18, 2), nullable=False, server_default=text("0"))
    vencido = Column(Numeric(18, 2), nullable=False, server_default=text("0"))
    facturas = Column(Integer, nullable=False, server_default=text("0"))
    dias_max_vencida = Column(Integer, nullable=False, server_default=text("0"))
    escalado = Column(Boolean, nullable=False, server_default=text("false"))
    error = Column(Text)
    aprobado_por = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    enviado_at = Column(DateTime(timezone=True))
