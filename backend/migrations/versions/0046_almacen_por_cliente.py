"""De qué almacén sale la mercancía de cada cliente.

Hasta ahora el almacén se elegía remisión por remisión. Para la ingesta
automática eso no sirve: el bot no tiene cómo saberlo, y dejarlo vacío significa
que la remisión no mueve inventario.

Se resuelve igual que la SERIE, que es el patrón que este sistema ya usa y que el
equipo ya entiende: sucursal → cliente → predeterminado del inquilino. De ahí
salen las dos propiedades que pidió el dueño sin tener que modelar nada más:

  · Un almacén sirve a VARIOS clientes y sucursales — el enlace va del cliente
    hacia el almacén (muchos a uno), no al revés.
  · Un cliente puede NO tener almacén — cae al predeterminado; y si tampoco hay,
    la remisión sale sin almacén y no toca inventario, que sigue siendo válido.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_almacen_por_cliente"
down_revision: Union[str, None] = "0045_equivalencias_compartidas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for tabla in ("clientes", "sucursales"):
        op.add_column(
            tabla,
            sa.Column(
                "almacen_id",
                postgresql.UUID(as_uuid=True),
                # SET NULL y no CASCADE: borrar un almacén no puede llevarse por
                # delante al cliente. Se queda sin almacén y cae al default.
                sa.ForeignKey("almacenes.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for tabla in ("sucursales", "clientes"):
        op.drop_column(tabla, "almacen_id")
