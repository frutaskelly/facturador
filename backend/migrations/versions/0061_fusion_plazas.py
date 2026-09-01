"""Sucursal como unidad de negocio — parte 2: fusionar duplicados y soltar lo viejo.

Con el vínculo poblado (0060), las plazas que existían una vez POR CLIENTE se
fusionan en una sola por nombre: las cuatro «Pachuca» (EHMO, JUBRAN, MAFAN,
BALLES) quedan como una plaza con cuatro vínculos. Sobrevive la fila más
antigua de cada (tenant, nombre normalizado); TODO lo que apuntaba a las demás
se reapunta a ella y las perdedoras se borran lógicamente.

Ocho tablas referencian sucursales y cada una tiene su trampa:
  * `producto_alias` — índice único funcional (0053): los alias que chocarían
    tras el reapunte se descartan (gana el que ya vivía en la superviviente).
  * `lista_asignaciones` — dos renglones idénticos tras la fusión (la lista
    EHMOHID estaba asignada a las dos «HIDALGO EHMO») se deduplican.
  * `cliente_sucursales` — un cliente con dos copias de la plaza queda con UN
    vínculo; sobrevive el que tenga serie configurada y su abanico se fusiona.
  * `oc_recibidas`, `remisiones`, `proyectos`, `cliente_externos`,
    `precio_overrides` — reapunte directo (sin restricciones que choquen).

Al final, los candados del modelo nuevo (nombre y código únicos por tenant
entre plazas vivas) y los drops: `sucursales.cliente_id` + sus series,
`proyecto_sucursales` (sustituida por `proyectos.sucursal_id`) y
`sucursal_series` (sustituida por `cliente_sucursal_series`).

Revision ID: 0061_fusion_plazas
Revises: 0060_sucursal_unidad_negocio
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0061_fusion_plazas"
down_revision: Union[str, None] = "0060_sucursal_unidad_negocio"
branch_labels = None
depends_on = None

_CERO = "'00000000-0000-0000-0000-000000000000'::uuid"


def upgrade() -> None:
    # ── mapa de fusión: perdedora → superviviente, por (tenant, nombre) ──
    op.execute(
        """
        CREATE TEMP TABLE _suc_map ON COMMIT DROP AS
        SELECT s.id AS old_id, k.keep_id
          FROM sucursales s
          JOIN (
                SELECT tenant_id, lower(btrim(nombre)) AS nom,
                       (array_agg(id ORDER BY created_at ASC, id ASC))[1] AS keep_id
                  FROM sucursales
                 WHERE deleted_at IS NULL
                 GROUP BY tenant_id, lower(btrim(nombre))
               ) k
            ON k.tenant_id = s.tenant_id AND lower(btrim(s.nombre)) = k.nom
         WHERE s.deleted_at IS NULL AND s.id <> k.keep_id
        """
    )

    # ── reapuntes directos ──
    for tabla, col in (
        ("oc_recibidas", "sucursal_id"),
        ("remisiones", "sucursal_id"),
        ("cliente_externos", "sucursal_id"),
        ("precio_overrides", "sucursal_id"),
        ("proyectos", "sucursal_id"),
        # `proyecto_sucursales` NO se reapunta: 0060 ya volcó su contenido a
        # proyectos.sucursal_id (que sí va en esta lista) y esta tabla se tira
        # al final de la migración. Reapuntarla solo servía para chocar con
        # uq_proyecto_sucursal, que Postgres valida fila por fila: dos filas del
        # mismo proyecto en plazas que se fusionan abortaban la migración entera.
    ):
        op.execute(
            f"UPDATE {tabla} t SET {col} = m.keep_id FROM _suc_map m WHERE t.{col} = m.old_id"
        )

    # ── producto_alias: descartar los que chocarían con el índice único ──
    # 1) contra un alias que YA vive en la superviviente (ese gana);
    op.execute(
        f"""
        DELETE FROM producto_alias pa
         USING _suc_map m
         WHERE pa.sucursal_id = m.old_id
           AND EXISTS (
                SELECT 1 FROM producto_alias pb
                 WHERE pb.tenant_id = pa.tenant_id
                   AND COALESCE(pb.cliente_id, {_CERO}) = COALESCE(pa.cliente_id, {_CERO})
                   AND pb.sucursal_id = m.keep_id
                   AND pb.alias_normalizado = pa.alias_normalizado
               )
        """
    )
    # 2) entre dos perdedoras que caerían en la misma llave (gana el más viejo).
    op.execute(
        f"""
        DELETE FROM producto_alias pa
         WHERE pa.id IN (
            SELECT id FROM (
                SELECT pa2.id,
                       row_number() OVER (
                           PARTITION BY pa2.tenant_id,
                                        COALESCE(pa2.cliente_id, {_CERO}),
                                        m.keep_id,
                                        pa2.alias_normalizado
                           ORDER BY pa2.created_at ASC, pa2.id ASC
                       ) AS rn
                  FROM producto_alias pa2
                  JOIN _suc_map m ON m.old_id = pa2.sucursal_id
            ) d WHERE d.rn > 1
         )
        """
    )
    op.execute(
        "UPDATE producto_alias t SET sucursal_id = m.keep_id FROM _suc_map m WHERE t.sucursal_id = m.old_id"
    )

    # ── lista_asignaciones: deduplicar ANTES de reapuntar ──
    # El índice único `uq_lista_asignacion_dims` cubre (tenant, cliente,
    # sucursal, serie, proyecto, vigencia_desde) — SIN lista: dos renglones que
    # tras la fusión caerían en la misma llave no caben, aunque asignen listas
    # distintas. Sobrevive el más reciente, que es el que el resolutor
    # preferiría de todos modos (a igual especificidad gana la última
    # negociación).
    op.execute(
        f"""
        DELETE FROM lista_asignaciones la
         WHERE la.id IN (
            SELECT id FROM (
                SELECT la2.id,
                       row_number() OVER (
                           PARTITION BY la2.tenant_id,
                                        COALESCE(la2.cliente_id, {_CERO}),
                                        COALESCE(m.keep_id, la2.sucursal_id, {_CERO}),
                                        COALESCE(la2.serie_id, {_CERO}),
                                        COALESCE(la2.proyecto_id, {_CERO}),
                                        COALESCE(la2.vigencia_desde, '0001-01-01'::date)
                           ORDER BY la2.created_at DESC, la2.id DESC
                       ) AS rn
                  FROM lista_asignaciones la2
                  LEFT JOIN _suc_map m ON m.old_id = la2.sucursal_id
            ) d WHERE d.rn > 1
         )
        """
    )
    op.execute(
        "UPDATE lista_asignaciones t SET sucursal_id = m.keep_id FROM _suc_map m WHERE t.sucursal_id = m.old_id"
    )

    # ── cliente_sucursales: un vínculo por (cliente, plaza superviviente) ──
    # Sobrevive el que tenga serie configurada (la más vieja como desempate);
    # el abanico de los perdedores se fusiona en él antes de borrarlos.
    op.execute(
        """
        CREATE TEMP TABLE _cs_map ON COMMIT DROP AS
        WITH mapped AS (
            SELECT cs.id, cs.cliente_id,
                   COALESCE(m.keep_id, cs.sucursal_id) AS plaza,
                   (cs.serie_factura_id IS NULL AND cs.serie_remision_id IS NULL) AS sin_serie,
                   cs.created_at
              FROM cliente_sucursales cs
              LEFT JOIN _suc_map m ON m.old_id = cs.sucursal_id
        ), ranked AS (
            SELECT id, cliente_id, plaza,
                   first_value(id) OVER (
                       PARTITION BY cliente_id, plaza
                       ORDER BY sin_serie ASC, created_at ASC, id ASC
                   ) AS keep_id
              FROM mapped
        )
        SELECT id AS old_id, keep_id FROM ranked WHERE id <> keep_id
        """
    )
    op.execute(
        """
        INSERT INTO cliente_sucursal_series (id, tenant_id, cliente_sucursal_id, serie_id)
        SELECT gen_random_uuid(), css.tenant_id, m.keep_id, css.serie_id
          FROM cliente_sucursal_series css
          JOIN _cs_map m ON m.old_id = css.cliente_sucursal_id
        ON CONFLICT ON CONSTRAINT uq_cliente_sucursal_serie DO NOTHING
        """
    )
    op.execute("DELETE FROM cliente_sucursales WHERE id IN (SELECT old_id FROM _cs_map)")
    op.execute(
        "UPDATE cliente_sucursales t SET sucursal_id = m.keep_id FROM _suc_map m WHERE t.sucursal_id = m.old_id"
    )

    # ── el almacén de las perdedoras no se tira ──
    # La superviviente es la fila más ANTIGUA, que puede no tener almacén
    # mientras una perdedora sí lo tenía; sin esto las remisiones de ese cliente
    # empezarían a salir del almacén default sin que nadie lo pidiera.
    op.execute(
        """
        UPDATE sucursales keep
           SET almacen_id = (
                SELECT s.almacen_id
                  FROM sucursales s
                  JOIN _suc_map m ON m.old_id = s.id
                 WHERE m.keep_id = keep.id AND s.almacen_id IS NOT NULL
                 ORDER BY s.created_at ASC, s.id ASC
                 LIMIT 1
               ),
               updated_at = now()
         WHERE keep.almacen_id IS NULL
           AND EXISTS (
                SELECT 1 FROM sucursales s JOIN _suc_map m ON m.old_id = s.id
                 WHERE m.keep_id = keep.id AND s.almacen_id IS NOT NULL
               )
        """
    )

    # ── las perdedoras se van (borrado lógico: el histórico ya no las apunta) ──
    op.execute(
        """
        UPDATE sucursales
           SET deleted_at = now(), updated_at = now()
         WHERE id IN (SELECT old_id FROM _suc_map)
        """
    )

    # ── candados del modelo nuevo ──
    # Códigos repetidos entre plazas vivas (los SUC-01 por-cliente): conserva el
    # de la fila más vieja y renumera el resto por tenant.
    op.execute(
        """
        WITH dups AS (
            SELECT id, row_number() OVER (
                       PARTITION BY tenant_id, codigo
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
              FROM sucursales
             WHERE deleted_at IS NULL AND codigo IS NOT NULL
        )
        UPDATE sucursales s SET codigo = NULL
          FROM dups d WHERE s.id = d.id AND d.rn > 1
        """
    )
    # Se reparten los HUECOS libres, no `count(*) + n`: los códigos vivos no son
    # SUC-01..SUC-N sin saltos (esta misma migración deja huecos al borrar las
    # perdedoras), y suponerlo generaba códigos repetidos que después reventaban
    # el índice único de abajo. Mismo criterio que `_generate_codigo` del router.
    op.execute(
        """
        WITH libres AS (
            SELECT t.id AS tenant_id, n,
                   row_number() OVER (PARTITION BY t.id ORDER BY n) AS slot
              FROM tenants t
              CROSS JOIN generate_series(1, 999) AS n
             WHERE NOT EXISTS (
                    SELECT 1 FROM sucursales s
                     WHERE s.tenant_id = t.id
                       AND s.deleted_at IS NULL
                       AND s.codigo = 'SUC-' || lpad(n::text, 2, '0')
                   )
        ), faltantes AS (
            SELECT id, tenant_id,
                   row_number() OVER (PARTITION BY tenant_id ORDER BY created_at, id) AS rn
              FROM sucursales
             WHERE deleted_at IS NULL AND codigo IS NULL
        )
        UPDATE sucursales s
           SET codigo = 'SUC-' || lpad(l.n::text, 2, '0')
          FROM faltantes f
          JOIN libres l ON l.tenant_id = f.tenant_id AND l.slot = f.rn
         WHERE s.id = f.id
        """
    )
    op.create_index(
        "uq_sucursal_tenant_nombre", "sucursales",
        [sa.text("tenant_id"), sa.text("lower(btrim(nombre))")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_sucursal_tenant_codigo", "sucursales",
        ["tenant_id", "codigo"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND codigo IS NOT NULL"),
    )

    # ── fuera lo viejo ──
    op.drop_column("sucursales", "cliente_id")
    op.drop_column("sucursales", "serie_factura_id")
    op.drop_column("sucursales", "serie_remision_id")
    op.drop_table("proyecto_sucursales")
    op.drop_table("sucursal_series")


def downgrade() -> None:
    raise NotImplementedError(
        "La fusión de plazas no es reversible: restaurar desde respaldo."
    )
