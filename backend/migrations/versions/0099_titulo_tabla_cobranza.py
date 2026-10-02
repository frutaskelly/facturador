"""Envíos de cobranza: el nombre de la tabla, aparte del nombre del envío.

Dueño (2-oct-2026): el envío se llamaba «GRUPO OPERADOR DE ALIMENTOS EHMO»
pero juntaba EHMO, SUREÑA y MAFAN, y la tabla cerraba con «Total GRUPO
OPERADOR DE ALIMENTOS EHMO» sumando las tres. El nombre del envío es para
encontrarlo en la lista; el de la tabla es lo que lee el cliente (el comodín
{nombre} del asunto y el mensaje, el cuerpo, el total, el Excel, el PDF y el
nombre de sus archivos).

`titulo_tabla` vacío = el prellenado: los nombres cortos de las razones
sociales del envío unidos con guion (EHMO-MAFAN-SUREÑA), que se rehace solo si
cambian las razones sociales. Ver `cobranza_grupos.titulo_sugerido`.

Revision ID: 0099_titulo_tabla_cobranza
Revises: 0098_smart_supply_panel
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0099_titulo_tabla_cobranza"
down_revision: Union[str, None] = "0098_smart_supply_panel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cobranza_grupos", sa.Column("titulo_tabla", sa.String(120)))


def downgrade() -> None:
    op.drop_column("cobranza_grupos", "titulo_tabla")
