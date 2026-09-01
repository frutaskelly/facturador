"""Paridad de `resolver_precios_lote` con `resolver_precio`.

El lote existe por velocidad (abrir una orden de 25 partidas hacía ~6 consultas
por partida contra una BD remota: ~27 s), pero la velocidad no puede costar
corrección: ESTE archivo corre ambos resolutores sobre la misma matriz de
escenarios — overrides por sucursal y cliente, asignaciones por proyecto/serie/
sucursal/cliente, listas parciales que caen en cascada, tramos por volumen,
vigencias vencidas, presentación derivada de la base × factor y productos sin
precio — y exige el MISMO resultado, campo por campo.
"""
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from .conftest import crear_sucursal
from app.models import (
    Cliente, ListaAsignacion, ListaPrecios, Precio, PrecioOverride,
    Producto, Proyecto, Serie, Sucursal, Tenant,
)
from app.services.precios import resolver_precio, resolver_precios_lote

_PURGE = (
    "lista_asignaciones", "precio_overrides", "precios", "listas_precios",
    "proyectos", "cliente_sucursales", "sucursales", "series", "productos", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    tenants = []
    try:
        tenant = Tenant(slug=f"lote-{suffix}", legal_name="Lote SA", rfc=f"L{suffix.upper()}X"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
                        tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); tenants.append(tenant.id)
        tid = tenant.id

        # Productos: uno simple, uno con CAJA=12×KILO (fallback por factor),
        # uno que no está en ninguna lista, y uno borrado.
        agua = Producto(tenant_id=tid, sku="AGUA", nombre="Aguacate", clave_sat="50300000",
                        unidad_sat="KGM", unidad_base="KILO")
        caja = Producto(tenant_id=tid, sku="MANZ", nombre="Manzana", clave_sat="50310000",
                        unidad_sat="KGM", unidad_base="KILO",
                        presentaciones={"KILO": 1, "CAJA": 12}, presentacion_default="KILO")
        nada = Producto(tenant_id=tid, sku="NADA", nombre="Sin precio", clave_sat="50300000",
                        unidad_sat="KGM", unidad_base="KILO")
        del_ = Producto(tenant_id=tid, sku="DEL", nombre="Borrado", clave_sat="50300000",
                        unidad_sat="KGM", unidad_base="KILO")
        db.add_all([agua, caja, nada, del_]); db.flush()
        del_.deleted_at = db.execute(text("SELECT now()")).scalar()

        cli = Cliente(tenant_id=tid, codigo="C1", legal_name="Cliente SA", rfc="XAXX010101000")
        db.add(cli); db.flush()
        suc = crear_sucursal(db, tenant_id=tid, cliente_id=cli.id, nombre="Tabasco")
        serie = Serie(tenant_id=tid, codigo=f"S{suffix[:3].upper()}", tipo_documento="REMISION")
        proy = Proyecto(tenant_id=tid, codigo=f"P{suffix[:3].upper()}", nombre="Hospitales",
                        cliente_id=cli.id)
        db.add_all([serie, proy]); db.flush()

        base = ListaPrecios(tenant_id=tid, codigo="UNICO", nombre="Base")
        l_cli = ListaPrecios(tenant_id=tid, codigo="LCLI", nombre="Del cliente")
        l_proy = ListaPrecios(tenant_id=tid, codigo="LPRO", nombre="Del proyecto (parcial)")
        l_ven = ListaPrecios(tenant_id=tid, codigo="LVEN", nombre="Vencida")
        db.add_all([base, l_cli, l_proy, l_ven]); db.flush()

        hoy = date.today()
        precios = [
            # base: todo, con tramos para AGUA
            (base, agua, "KILO", "25", 1, None, None),
            (base, agua, "KILO", "22", 10, None, None),
            (base, caja, "KILO", "30", 1, None, None),
            # lista del cliente: AGUA y la MANZANA solo en KILO (CAJA cae por factor)
            (l_cli, agua, "KILO", "20", 1, None, None),
            (l_cli, agua, "KILO", "18", 10, None, None),
            (l_cli, caja, "KILO", "28", 1, None, None),
            # lista del proyecto: PARCIAL — solo AGUA (MANZANA cae a la del cliente)
            (l_proy, agua, "KILO", "17.5", 1, None, None),
            # lista vencida: precio irresistible que NO debe aplicar
            (l_ven, agua, "KILO", "1", 1, None, hoy - timedelta(days=1)),
        ]
        for lp, prod, pres, precio, cmin, desde, hasta in precios:
            db.add(Precio(tenant_id=tid, lista_id=lp.id, producto_id=prod.id,
                          presentacion=pres, precio_unitario=Decimal(precio),
                          cantidad_minima=cmin, vigencia_desde=desde, vigencia_hasta=hasta))

        db.add_all([
            ListaAsignacion(tenant_id=tid, lista_id=l_cli.id, cliente_id=cli.id),
            ListaAsignacion(tenant_id=tid, lista_id=l_proy.id, cliente_id=cli.id,
                            proyecto_id=proy.id),
            ListaAsignacion(tenant_id=tid, lista_id=l_ven.id, cliente_id=cli.id,
                            vigencia_desde=hoy - timedelta(days=30),
                            vigencia_hasta=hoy - timedelta(days=1)),
        ])

        # Overrides: sucursal gana a cliente; uno vencido que no debe hablar.
        db.add_all([
            PrecioOverride(tenant_id=tid, cliente_id=cli.id, producto_id=caja.id,
                           presentacion="KILO", precio_unitario=Decimal("26")),
            PrecioOverride(tenant_id=tid, sucursal_id=suc.id, producto_id=caja.id,
                           presentacion="KILO", precio_unitario=Decimal("24")),
            PrecioOverride(tenant_id=tid, sucursal_id=suc.id, producto_id=agua.id,
                           presentacion="KILO", precio_unitario=Decimal("2"),
                           vigencia_hasta=hoy - timedelta(days=1)),
        ])
        db.commit()

        yield {
            "db": db, "tid": tid,
            "agua": agua.id, "caja": caja.id, "nada": nada.id, "del": del_.id,
            "cli": cli.id, "suc": suc.id, "serie": serie.id, "proy": proy.id,
        }
    finally:
        for table in _PURGE:
            for t in tenants:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": t})
        for t in tenants:
            db.query(Tenant).filter(Tenant.id == t).delete()
        db.commit(); db.close()


@contextmanager
def _sesion_tenant(db_engine, tid):
    """Sesión con RLS activo y el tenant fijado — el mismo par SET LOCAL ROLE
    app_user + GUC que usa get_tenant_db. Sin esto, el postgres dueño de las
    tablas ve los datos de TODOS los tenants (p. ej. otra lista UNICO sembrada
    por otra suite) y `_lista_default` truena con MultipleResultsFound."""
    with db_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE app_user"))
            conn.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tid)})
            yield Session(bind=conn)


def _contextos(env):
    """Los contextos (cliente, sucursal, serie, proyecto) que ejercitan cada
    rama de la cascada, incluido el comodín total (solo lista base)."""
    return [
        {},
        {"cliente_id": env["cli"]},
        {"sucursal_id": env["suc"]},                      # deriva el cliente de la sucursal
        {"cliente_id": env["cli"], "sucursal_id": env["suc"]},
        {"cliente_id": env["cli"], "proyecto_id": env["proy"]},
        {"cliente_id": env["cli"], "sucursal_id": env["suc"],
         "serie_id": env["serie"], "proyecto_id": env["proy"]},
    ]


def _items(env):
    """Las partidas que ejercitan tramos, factor, ausencias y borrados."""
    return [
        {"producto_id": env["agua"], "presentacion": "KILO", "cantidad": Decimal("5")},
        {"producto_id": env["agua"], "presentacion": "KILO", "cantidad": Decimal("15")},   # tramo mayoreo
        {"producto_id": env["caja"], "presentacion": "KILO", "cantidad": Decimal("3")},
        {"producto_id": env["caja"], "presentacion": "CAJA", "cantidad": Decimal("2")},    # base × factor 12
        {"producto_id": env["nada"], "presentacion": "KILO", "cantidad": Decimal("1")},    # solo en... nada
        {"producto_id": env["del"], "presentacion": "KILO", "cantidad": Decimal("1")},     # borrado
        {"producto_id": env["agua"], "presentacion": "COSTAL", "cantidad": Decimal("1")},  # presentación inexistente
    ]


def test_el_lote_calcula_lo_mismo_que_la_cascada(env, db_engine):
    for ctx in _contextos(env):
        items = _items(env)
        with _sesion_tenant(db_engine, env["tid"]) as db:
            lote = resolver_precios_lote(db, items=items, **ctx)
            uno_a_uno = [
                resolver_precio(
                    db,
                    producto_id=it["producto_id"],
                    presentacion=it["presentacion"],
                    cantidad=it["cantidad"],
                    **ctx,
                )
                for it in items
            ]
        assert lote == uno_a_uno, f"divergencia con contexto {ctx}"


def test_lote_vacio(env, db_engine):
    with _sesion_tenant(db_engine, env["tid"]) as db:
        assert resolver_precios_lote(db, items=[], cliente_id=env["cli"]) == []


def test_el_lote_respeta_precedencias_concretas(env, db_engine):
    """No solo paridad: los valores esperados, para que un bug simétrico en
    ambos resolutores no pase como 'iguales'."""
    with _sesion_tenant(db_engine, env["tid"]) as db:
        lote = resolver_precios_lote(
            db, items=_items(env), cliente_id=env["cli"], sucursal_id=env["suc"],
            proyecto_id=env["proy"],
        )
    # AGUA 5: lista del proyecto (más específica) $17.50
    assert lote[0] == {"precio": Decimal("17.5"), "origen": "lista_proyecto",
                       "lista_id": lote[0]["lista_id"], "asignacion_id": lote[0]["asignacion_id"]}
    # MANZANA KILO: override de la SUCURSAL ($24) gana al del cliente ($26)
    assert lote[2]["precio"] == Decimal("24") and lote[2]["origen"] == "override_sucursal"
    # MANZANA CAJA: sin precio propio → KILO del override sucursal × factor 12
    assert lote[3]["precio"] == Decimal("288.00") and lote[3]["origen"] == "override_sucursal"
    # Sin precio en ningún lado
    assert lote[4] is None


def test_desempate_determinista_en_overrides(env, db_engine):
    """Dos overrides de la MISMA llave en la misma transacción comparten
    created_at (func.now() es por transacción): el ganador debe ser el mismo en
    la cascada y en el lote — el id como desempate lo garantiza."""
    db = env["db"]
    db.add_all([
        PrecioOverride(tenant_id=env["tid"], cliente_id=env["cli"], producto_id=env["agua"],
                       presentacion="KILO", precio_unitario=Decimal("11")),
        PrecioOverride(tenant_id=env["tid"], cliente_id=env["cli"], producto_id=env["agua"],
                       presentacion="KILO", precio_unitario=Decimal("12")),
    ])
    db.commit()
    item = {"producto_id": env["agua"], "presentacion": "KILO", "cantidad": Decimal("1")}
    with _sesion_tenant(db_engine, env["tid"]) as sdb:
        lote = resolver_precios_lote(sdb, items=[item], cliente_id=env["cli"])
        uno = resolver_precio(sdb, producto_id=env["agua"], presentacion="KILO",
                              cantidad=Decimal("1"), cliente_id=env["cli"])
    assert lote[0] == uno
    assert lote[0]["origen"] == "override_cliente"
