"""Índices para las dos FKs sin índice que sí se consultan en caliente.

El advisor de Supabase lista 49 foreign keys sin índice. Revisadas contra las
queries reales del backend, solo dos aparecen en filtros frecuentes:

- ``remisiones.factura_id`` — el cruce del espejo la usa en una subconsulta
  correlacionada por cada factura de la pasada (bucle de 60 s del conector), y
  cancelar/sustituir/descartar una factura busca sus remisiones por aquí.
- ``oc_recibidas.remision_id`` — ``_adjuntar_oc`` filtra por ella en CADA
  página del listado de remisiones para colgar la OC de origen.

El resto de las señaladas (remisiones.serie_id/sucursal_id/lista_precios_id,
lineas_factura.producto_id, lineas_remision.lote_id, pagos.factura_id,
oc_recibidas.sucursal_id, lista_asignaciones.*, cliente_sucursales.serie_*)
solo se escriben o se leen fila a fila: un índice ahí sería puro mantenimiento
y acabaría en la lista de «índices nunca usados» del mismo advisor.

Sin CONCURRENTLY a propósito: Alembic corre en transacción y estas tablas son
de miles de filas — el candado SHARE del CREATE INDEX dura milisegundos.

Revision ID: 0068_indices_fk_calientes
Revises: 0067_clave_cliente_por_sucursal
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0068_indices_fk_calientes"
down_revision: Union[str, None] = "0067_clave_cliente_por_sucursal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_remisiones_factura_id", "remisiones", ["factura_id"])
    op.create_index("ix_oc_recibidas_remision_id", "oc_recibidas", ["remision_id"])


def downgrade() -> None:
    op.drop_index("ix_oc_recibidas_remision_id", table_name="oc_recibidas")
    op.drop_index("ix_remisiones_factura_id", table_name="remisiones")
