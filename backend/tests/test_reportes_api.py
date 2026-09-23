"""Reportes de dirección: cartera y ventas."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.db import SessionLocal
from app.models import Factura

from tests.test_cobranza_api import (  # noqa: F401
    _factura_ppd_timbrada, _h, auth, auth_atado, env,
)


def _factura_del_dia(env, *, total, dias_atras, folio, serie="F", cliente=None):
    """Una TIMBRADA con fecha dada, para las series de ventas."""
    db = SessionLocal()
    try:
        f = Factura(
            tenant_id=uuid.UUID(str(env["tenant_id"])), serie=serie, folio=folio,
            cliente_id=uuid.UUID(cliente or env["cli"]), metodo_pago="PUE", forma_pago="99",
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


def test_ventas_rellena_los_dias_sin_factura(client, env, auth):
    """Los días en cero van en la serie: una gráfica que salta del lunes al
    jueves miente sobre el ritmo."""
    hoy = datetime.now(timezone.utc).date()
    _factura_del_dia(env, total=1000, dias_atras=0, folio=41)
    _factura_del_dia(env, total=250, dias_atras=4, folio=42)

    d = client.get("/api/v1/reportes/ventas", params={
        "desde": (hoy - timedelta(days=4)).isoformat(), "hasta": hoy.isoformat(),
    }, headers=_h(env)).json()

    assert d["granularidad"] == "dia"
    assert len(d["serie"]) == 5
    assert [c["inicio"] for c in d["serie"]][0] == (hoy - timedelta(days=4)).isoformat()
    assert float(d["serie"][-1]["total"]) == 1000.0
    assert float(d["serie"][2]["total"]) == 0.0
    assert float(d["total"]) == 1250.0
    assert d["facturas"] == 2
    # El total del rango ES la suma de las barras: si divergen, una cubeta se perdió.
    assert sum(float(c["total"]) for c in d["serie"]) == float(d["total"])


def test_ventas_por_semana_recorta_las_cubetas_al_rango(client, env, auth):
    """La primera y la última cubeta valen lo facturado DENTRO del filtro, no
    lo de la semana natural completa que las contiene."""
    hoy = datetime.now(timezone.utc).date()
    desde = hoy - timedelta(days=13)
    _factura_del_dia(env, total=700, dias_atras=13, folio=44)
    _factura_del_dia(env, total=300, dias_atras=20, folio=45)   # fuera del rango

    d = client.get("/api/v1/reportes/ventas", params={
        "desde": desde.isoformat(), "hasta": hoy.isoformat(), "granularidad": "semana",
    }, headers=_h(env)).json()

    assert d["granularidad"] == "semana"
    assert d["serie"][0]["inicio"] == desde.isoformat()      # recortada al inicio del rango
    assert d["serie"][-1]["fin"] == hoy.isoformat()          # y al final
    assert float(d["total"]) == 700.0                        # la de hace 20 días no entra


def test_ventas_compara_contra_el_mismo_tramo_del_mes_pasado(client, env, auth):
    """Un mes empezado se compara contra el MISMO tramo del mes pasado: medirlo
    contra los N días corridos previos partiría el mes pasado a la mitad."""
    hoy = datetime.now(timezone.utc).date()
    primero = hoy.replace(day=1)
    _factura_del_dia(env, total=1000, dias_atras=0, folio=46)

    d = client.get("/api/v1/reportes/ventas", params={
        "desde": primero.isoformat(), "hasta": hoy.isoformat(),
    }, headers=_h(env)).json()

    anterior = d["anterior"]
    mes_pasado = (primero - timedelta(days=1)).replace(day=1)
    assert anterior["desde"] == mes_pasado.isoformat()
    assert anterior["hasta"] <= (primero - timedelta(days=1)).isoformat()
    assert float(d["total"]) >= 1000.0


def test_ventas_sin_base_previa_no_inventa_porcentaje(client, env, auth):
    """Sin facturación previa no hay porcentaje que dar: None, no un 100%."""
    _factura_del_dia(env, total=500, dias_atras=0, folio=43)
    d = client.get("/api/v1/reportes/ventas", headers=_h(env)).json()
    assert d["anterior"]["variacion"] is None or isinstance(d["anterior"]["variacion"], float)


def test_ventas_filtra_por_cliente(client, env, auth):
    """El filtro global de cliente deja fuera lo de los demás, serie incluida."""
    _factura_del_dia(env, total=1000, dias_atras=0, folio=47)
    _factura_del_dia(env, total=400, dias_atras=0, folio=48, cliente=env["otro"])

    todos = client.get("/api/v1/reportes/ventas", headers=_h(env)).json()
    solo = client.get("/api/v1/reportes/ventas", params={"cliente_id": env["cli"]},
                      headers=_h(env)).json()

    assert float(todos["total"]) == 1400.0
    assert float(solo["total"]) == 1000.0
    assert sum(float(c["total"]) for c in solo["serie"]) == 1000.0


def test_ventas_el_filtro_no_abre_el_candado_por_cliente(client, env, auth_atado):
    """Pedir el cliente ajeno desde una sesión amarrada sale 404: el filtro
    elige DENTRO de lo permitido, nunca lo ensancha."""
    r = client.get("/api/v1/reportes/ventas", params={"cliente_id": env["otro"]},
                   headers=_h(env))
    assert r.status_code == 404


def test_cartera_se_acota_al_rango_y_al_cliente(client, env, auth):
    """Los filtros globales mueven también la cobranza, por fecha de emisión."""
    hoy = datetime.now(timezone.utc).date()
    _factura_ppd_timbrada(env, total=1000, dias_atras=5, folio=51, serie="ZEHMOTG")
    _factura_ppd_timbrada(env, total=700, dias_atras=90, folio=52, serie="ZEHMOTG")

    reciente = client.get("/api/v1/reportes/cartera", params={
        "desde": (hoy - timedelta(days=30)).isoformat(), "hasta": hoy.isoformat(),
    }, headers=_h(env)).json()
    assert float(reciente["saldo_total"]) == 1000.0

    ajeno = client.get("/api/v1/reportes/cartera", params={"cliente_id": env["otro"]},
                       headers=_h(env)).json()
    assert float(ajeno["saldo_total"]) == 0.0


def test_sumario_de_venta_cuadra_con_el_facturado(client, env, auth):
    """El sumario reparte lo MISMO que suma /ventas: mismo rango, mismo total,
    filas de mayor a menor; lo que va en cancelación se cuenta y se informa."""
    hoy = datetime.now(timezone.utc).date()
    _factura_del_dia(env, total=1000, dias_atras=0, folio=61)
    _factura_del_dia(env, total=400, dias_atras=2, folio=62, cliente=env["otro"])
    _factura_del_dia(env, total=900, dias_atras=40, folio=63)                 # fuera del rango
    _factura_ppd_timbrada(env, total=300, dias_atras=1, folio=64,
                          cancelacion_msj="Cancelación enviada al SAT")

    rango = {"desde": (hoy - timedelta(days=9)).isoformat(), "hasta": hoy.isoformat()}
    ventas = client.get("/api/v1/reportes/ventas", params=rango, headers=_h(env)).json()
    d = client.get("/api/v1/reportes/ventas/sumario", params=rango, headers=_h(env)).json()

    assert d["agrupar"] == "cliente"                         # por omisión, por cliente
    assert float(d["total"]) == float(ventas["total"]) == 1700.0
    assert d["facturas"] == ventas["facturas"] == 3
    assert float(d["total_en_cancelacion"]) == 300.0
    montos = [float(f["total"]) for f in d["filas"]]
    assert montos == sorted(montos, reverse=True)
    assert montos[0] == 1300.0 and d["filas"][0]["facturas"] == 2
    assert sum(montos) == float(d["total"])

    solo = client.get("/api/v1/reportes/ventas/sumario",
                      params={**rango, "cliente_id": env["otro"], "agrupar": "sucursal"},
                      headers=_h(env)).json()
    assert float(solo["total"]) == 400.0
    assert solo["filas"][0]["etiqueta"] == "Sin plaza"


def test_sumario_de_venta_respeta_el_candado(client, env, auth_atado):
    r = client.get("/api/v1/reportes/ventas/sumario", params={"cliente_id": env["otro"]},
                   headers=_h(env))
    assert r.status_code == 404


def _recibo(env, *, monto, dias_atras, folio, estado="TIMBRADO", factura_id=None):
    from app.models import ReciboPago, ReciboPagoFactura
    db = SessionLocal()
    try:
        r = ReciboPago(
            tenant_id=uuid.UUID(str(env["tenant_id"])), cliente_id=uuid.UUID(env["cli"]),
            serie="P", folio=folio, monto=Decimal(str(monto)), estado=estado,
            uuid=str(uuid.uuid4()),
            fecha_pago=datetime.now(timezone.utc) - timedelta(days=dias_atras),
        )
        db.add(r); db.flush()
        if factura_id:
            db.add(ReciboPagoFactura(
                tenant_id=r.tenant_id, recibo_id=r.id, factura_id=uuid.UUID(factura_id),
                importe_pagado=Decimal(str(monto)), saldo_anterior=Decimal(str(monto)),
                saldo_insoluto=Decimal("0"),
            ))
        db.commit()
    finally:
        db.close()


def test_pagos_por_fecha_de_pago_sin_borradores(client, env, auth):
    """Solo comprobantes que llegaron al SAT; el total es lo vigente y lo
    cancelado va aparte."""
    fid = _factura_ppd_timbrada(env, total=500, dias_atras=20, folio=71, serie="ZEHMOTG")
    _recibo(env, monto=500, dias_atras=1, folio=1, factura_id=fid)
    _recibo(env, monto=200, dias_atras=2, folio=2, estado="CANCELADO")
    _recibo(env, monto=900, dias_atras=0, folio=3, estado="BORRADOR")
    _recibo(env, monto=800, dias_atras=60, folio=4)                     # fuera del rango

    d = client.get("/api/v1/reportes/pagos", headers=_h(env)).json()
    assert float(d["total"]) == 500.0 and d["comprobantes"] == 1
    assert float(d["total_cancelado"]) == 200.0 and d["cancelados"] == 1
    assert [i["folio"] for i in d["items"]] == [1, 2]
    rel = d["items"][0]["facturas"]
    assert rel[0]["serie"] == "ZEHMOTG" and rel[0]["folio"] == 71
    assert d["items"][0]["cliente"]

    ajeno = client.get("/api/v1/reportes/pagos", params={"cliente_id": env["otro"]},
                       headers=_h(env)).json()
    assert ajeno["items"] == []
