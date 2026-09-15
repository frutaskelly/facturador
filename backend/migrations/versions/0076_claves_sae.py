"""Espejo del catálogo de claves de SAE, para validar el masivo ANTES de generarlo.

El 14-sep-2026 la ZEHMOHOS 906 no se creó en SAE porque la clave FRESADOMOPZ no
existe en su inventario. El preview del masivo la dejó pasar: hoy solo comprueba
que el producto TENGA código de cliente, no que SAE lo conozca. El error se
descubrió después de importar, con el CFDI ya emitido a medias.

Una auditoría del catálogo de EHMO encontró 30 claves inexistentes y 1 dada de
baja, entre ellas CALABAZACASTIKG con 92 usos en 90 días: minas puestas.

Esta tabla es un ESPEJO de INVE02 (solo lectura hacia acá): el bot deposita las
claves que SAE tiene por empresa, y `export_sae.preparar()` marca error cuando
una partida usa una clave que SAE no conoce o que está dada de baja. Si un
tenant no tiene espejo, la validación NO aplica (fail-open): quien no usa el
bot sigue exportando igual que antes.

Revision ID: 0076_claves_sae
Revises: 0075_indices_listados
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076_claves_sae"
down_revision: Union[str, None] = "0075_indices_listados"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "claves_sae",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        # Empresa de SAE ('02', '03'): los catálogos son independientes.
        sa.Column("empresa", sa.String(4), nullable=False),
        # CVE_ART tal como SAE la guarda, ya normalizada (trim + mayúsculas).
        sa.Column("clave", sa.String(50), nullable=False),
        sa.Column("descripcion", sa.String(254)),
        # STATUS distinto de 'A' en SAE = dada de baja: existe pero no factura.
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "sincronizado_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "empresa", "clave", name="uq_clave_sae_tenant_empresa"),
    )
    op.create_index("ix_claves_sae_tenant_id", "claves_sae", ["tenant_id"])
    # El lookup del preview: una pasada por (tenant, empresa) y contra ella se
    # comprueban todas las claves del lote en memoria.
    op.create_index("ix_claves_sae_lookup", "claves_sae", ["tenant_id", "empresa"])

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON claves_sae TO app_user")
    op.execute("ALTER TABLE claves_sae ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON claves_sae "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.drop_index("ix_claves_sae_lookup", table_name="claves_sae")
    op.drop_index("ix_claves_sae_tenant_id", table_name="claves_sae")
    op.drop_table("claves_sae")
