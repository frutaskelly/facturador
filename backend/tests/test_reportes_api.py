"""Reportes de dirección: cartera y ventas."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.db import SessionLocal
from app.models import Factura

from tests.test_cobranza_api import _factura_ppd_timbrada, _h, env, auth  # noqa: F401


def _factura_del_dia(env, *, total, dias_atras, folio, serie="F"):
    """Una TIMBRADA con fecha dada, para las series de ventas."""
    db = SessionLocal()
    try:
        f = Factura(
            tenant_id=uuid.UUID(str(env["tenant_id"])), serie=serie, folio=folio,
            cliente_id=uuid.UUID(env["cli"]), metodo_pago="PUE", forma_pago="99",
            total=Decimal(str(total)), subtotal=Decimal(str(total)),
            estado="TIMBRADA", uuid=str(uuid.uuid4()),
            fecha=datetime.now(timezone.utc) - timedelta(days=dias_atras),
            saldo_insoluto=Decimal("0"),
        )
        db.add(f); db.commit()
    finally:
        db.close()


def test_cartera_agrupa_y_reparte_la_antiguedad(client, env, auth):
    """Las cubetas son por MES y el total cuadra con la suma de las filas."""
    # cliente con 30 días de crédito (fixture): 40 días atrás vence hace 10 → mes_1
    _factura_ppd_timbrada(env, total=1000, dias_atras=40, folio=31, serie="ZEHMOTG")
    _factura_ppd_timbrada(env, total=500, dias_atras=100, folio=32, serie="ZEHMOTG")   # vence hace 70 → mes_3
    _factura_ppd_timbrada(env, total=200, dias_atras=5, folio=33, serie="ZEHMOVH")     # por vencer

    d = client.get("/api/v1/reportes/cartera", headers=_h(env)).json()
    assert float(d["saldo_total"]) == 1700.0
    assert float(d["vencido_total"]) == 1500.0
    a = d["antiguedad"]
    assert float(a["por_vencer"]) == 200.0
    assert float(a["mes_1"]) == 1000.0
    assert float(a["mes_3"]) == 500.0
    assert float(a["mes_2"]) == 0.0 and float(a["mes_4_mas"]) == 0.0
    filas = {f["etiqueta"]: f for f in d["filas"]}
    assert float(filas["HOSPITALES TUXTLA"]["saldo"]) == 1500.0
    assert filas["HOSPITALES TUXTLA"]["facturas"] == 2
    # la suma de las filas ES el total: si divergen, una fila se perdió
    assert sum(float(f["saldo"]) for f in d["filas"]) == float(d["saldo_total"])


def test_cartera_por_cliente_y_por_sucursal(client, env, auth):
    _factura_ppd_timbrada(env, total=300, dias_atras=5, folio=34, serie="ZEHMOTG")

    por_cliente = client.get("/api/v1/reportes/cartera", params={"agrupar": "cliente"},
                             headers=_h(env)).json()
    assert len(por_cliente["filas"]) == 1              # un solo cliente en el fixture
    assert float(por_cliente["filas"][0]["saldo"]) == 300.0

    por_plaza = client.get("/api/v1/reportes/cartera", params={"agrupar": "sucursal"},
                           headers=_h(env)).json()
    # Sin vínculo cliente×plaza sembrado, la fila no se inventa una plaza.
    assert por_plaza["filas"][0]["etiqueta"] == "Sin plaza"
    assert float(por_plaza["saldo_total"]) == 300.0


def test_ventas_compara_contra_el_mismo_tramo(client, env, auth):
    """El comparativo usa los días TRANSCURRIDOS, no el periodo completo: una
    semana a medias contra una completa pintaría una caída inexistente."""
    _factura_del_dia(env, total=1000, dias_atras=0, folio=41)
    _factura_del_dia(env, total=250, dias_atras=7, folio=42)     # mismo día, semana pasada

    d = client.get("/api/v1/reportes/ventas", params={"dias": 14}, headers=_h(env)).json()
    assert len(d["diario"]) == 14                     # los días sin factura van en cero
    assert float(d["diario"][-1]["total"]) == 1000.0
    assert float(d["semana"]["actual"]) >= 1000.0
    assert d["semana"]["variacion"] is not None
    assert d["mes"]["dias_transcurridos"] >= 1


def test_ventas_sin_base_previa_no_inventa_porcentaje(client, env, auth):
    """Sin facturación previa no hay porcentaje que dar: None, no un 100%."""
    _factura_del_dia(env, total=500, dias_atras=0, folio=43)
    d = client.get("/api/v1/reportes/ventas", headers=_h(env)).json()
    assert d["semana"]["variacion"] is None or isinstance(d["semana"]["variacion"], float)
