"""Sucursal como unidad de negocio — parte 1: la estructura nueva (aditiva).

Rediseño 01-sep-2026: la sucursal deja de ser propiedad de un cliente
(`sucursales.cliente_id`) y pasa a ser la PLAZA del tenant, de la que se surten
varios clientes. Esta migración solo AGREGA — nada de lo viejo cambia todavía,
así que puede desplegarse sin tocar comportamiento:

  * `cliente_sucursales` — el vínculo cliente ↔ plaza, con la serie de folios
    DE LA RELACIÓN (EHMO×Tabasco → ZEHMOVH; Balles y Jubran comparten ZHGO en
    Pachuca). Se puebla desde el `cliente_id` que hoy vive en cada sucursal.
  * `cliente_sucursal_series` — el abanico de series del vínculo (sustituye a
    `sucursal_series`, que colgaba de la plaza).
  * `proyectos.sucursal_id` — un proyecto por plaza (decisión del dueño
    01-sep-2026); se puebla desde `proyecto_sucursales` cuando el alcance era
    de exactamente una.
  * El CHECK de `precio_overrides` se relaja de XOR a "al menos uno": con la
    plaza compartida, (cliente, sucursal) juntos es el override MÁS específico
    y (NULL, sucursal) significa "para todos los que surte la plaza". Por eso
    mismo, los overrides y las asignaciones que solo decían "sucursal" se ANCLAN
    al dueño que tenía esa sucursal: sin eso, la fusión de 0061 le regalaría el
    precio negociado de un cliente a los demás de la plaza.

La parte 2 (0061) fusiona las plazas duplicadas y tira las columnas viejas.

Revision ID: 0060_sucursal_unidad_negocio
Revises: 0059_categoria_sin_categorizar
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0060_sucursal_unidad_negocio"
down_revision: Union[str, None] = "0059_categoria_sin_categorizar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cliente_sucursales",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("cliente_id", UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sucursal_id", UUID(as_uuid=True),
                  sa.ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("serie_factura_id", UUID(as_uuid=True),
                  sa.ForeignKey("series.id", ondelete="SET NULL"), nullable=True),
        sa.Column("serie_remision_id", UUID(as_uuid=True),
                  sa.ForeignKey("series.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("cliente_id", "sucursal_id", name="uq_cliente_sucursal"),
    )
    op.create_table(
        "cliente_sucursal_series",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("cliente_sucursal_id", UUID(as_uuid=True),
                  sa.ForeignKey("cliente_sucursales.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("serie_id", UUID(as_uuid=True),
                  sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("cliente_sucursal_id", "serie_id", name="uq_cliente_sucursal_serie"),
    )
    # RLS y grants con la MISMA plantilla que el resto de tablas por tenant.
    for tabla in ("cliente_sucursales", "cliente_sucursal_series"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabla} TO app_user")
        op.execute(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {tabla}
                USING (tenant_id = public.current_tenant_id())
            """
        )

    op.add_column("proyectos", sa.Column("sucursal_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "proyectos_sucursal_id_fkey", "proyectos", "sucursales",
        ["sucursal_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_proyectos_sucursal_id", "proyectos", ["sucursal_id"])

    # ── backfills ──
    # Cada sucursal de hoy ya es "cliente X se surte de la plaza Y": el vínculo
    # nace de ahí, llevándose las series que colgaban de la sucursal.
    op.execute(
        """
        INSERT INTO cliente_sucursales
            (id, tenant_id, cliente_id, sucursal_id, serie_factura_id, serie_remision_id,
             created_at, updated_at)
        SELECT gen_random_uuid(), s.tenant_id, s.cliente_id, s.id,
               s.serie_factura_id, s.serie_remision_id, now(), now()
          FROM sucursales s
         WHERE s.deleted_at IS NULL
        """
    )
    # El abanico de la sucursal pasa al vínculo con SU cliente de hoy.
    op.execute(
        """
        INSERT INTO cliente_sucursal_series (id, tenant_id, cliente_sucursal_id, serie_id)
        SELECT gen_random_uuid(), ss.tenant_id, cs.id, ss.serie_id
          FROM sucursal_series ss
          JOIN sucursales s ON s.id = ss.sucursal_id
          JOIN cliente_sucursales cs
            ON cs.sucursal_id = s.id AND cs.cliente_id = s.cliente_id
         WHERE s.deleted_at IS NULL
        """
    )
    # Alcance 0058 → plaza única: solo cuando el proyecto tenía EXACTAMENTE una
    # (más de una no se puede mapear a "un proyecto por plaza"; queda NULL =
    # sin restricción, el comportamiento retrocompatible).
    op.execute(
        """
        UPDATE proyectos p
           SET sucursal_id = (
                SELECT ps.sucursal_id FROM proyecto_sucursales ps
                 WHERE ps.proyecto_id = p.id
               )
         WHERE (SELECT count(*) FROM proyecto_sucursales ps WHERE ps.proyecto_id = p.id) = 1
        """
    )

    # ── overrides: XOR → al menos una dimensión ──
    op.drop_constraint("ck_override_cliente_xor_sucursal", "precio_overrides", type_="check")
    op.create_check_constraint(
        "ck_override_alguna_dimension",
        "precio_overrides",
        "cliente_id IS NOT NULL OR sucursal_id IS NOT NULL",
    )
    # Un override que solo decía "sucursal" significaba, con el modelo viejo,
    # "ese cliente en esa plaza" — la sucursal tenía dueño. Con la plaza
    # COMPARTIDA, cliente_id NULL pasa a significar "todos los clientes que
    # surte", así que hay que ANCLAR el dueño de entonces o la fusión de 0061
    # le regalaría el precio a los demás. Va aquí y no en 0061 porque debe
    # ocurrir mientras sucursales.cliente_id todavía existe.
    op.execute(
        """
        UPDATE precio_overrides po
           SET cliente_id = s.cliente_id
          FROM sucursales s
         WHERE s.id = po.sucursal_id
           AND po.cliente_id IS NULL
        """
    )
    # Lo MISMO para las asignaciones de lista: un renglón (cliente NULL,
    # sucursal X) quería decir "ese cliente en esa plaza", y sin anclar pasaría
    # a cobrarle su lista negociada a todos los que surte la plaza fusionada.
    op.execute(
        """
        UPDATE lista_asignaciones la
           SET cliente_id = s.cliente_id
          FROM sucursales s
         WHERE s.id = la.sucursal_id
           AND la.cliente_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_override_alguna_dimension", "precio_overrides", type_="check")
    op.create_check_constraint(
        "ck_override_cliente_xor_sucursal",
        "precio_overrides",
        "(cliente_id IS NOT NULL) <> (sucursal_id IS NOT NULL)",
    )
    op.drop_index("ix_proyectos_sucursal_id", table_name="proyectos")
    op.drop_column("proyectos", "sucursal_id")
    op.drop_table("cliente_sucursal_series")
    op.drop_table("cliente_sucursales")
