"""Cotizador de requisiciones (el flujo del bot de WhatsApp): lectura
determinista del PDF de SAE, las reglas de validación de precio con sus notas
rojas verbatim, y el PDF de salida con el mismo dibujo que manda el bot.

El fixture `requisicion_6477.pdf` es una requisición REAL de Balles (la misma
con la que se validó el port contra el PDF que generó el bot): 9 partidas,
folio 0000006477, una partida con precio incorrecto (tortilla OC $38.90 vs
lista $45.90) y tres productos sin precio en la lista.
"""
import io
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models import (
    Cliente, EsquemaImpuesto, ListaAsignacion, ListaPrecios, Precio, Producto, Tenant,
)
from app.services.requisicion_parse import process_pdf
from app.services.requisicion_pdf import numero_a_letra

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "requisicion_6477.pdf")


def _pdf_bytes() -> bytes:
    with open(FIXTURE, "rb") as fh:
        return fh.read()


# ── lectura determinista (sin BD, sin IA) ────────────────────────────────────

def test_process_pdf_lee_la_requisicion_completa():
    d = process_pdf(_pdf_bytes())
    assert d["doc_type"] == "REQUISICION"
    assert d["folio"] == "0000006477"
    assert d["fecha_documento"] == "28/08/2026"
    assert d["cliente_rfc"] == "OBV191007BS1"
    assert "OPERADORA BALLES" in (d["cliente_nombre"] or "")
    assert len(d["items"]) == 9
    assert d["totals"]["total_partidas"] == "9"
    assert d["totals"]["subtotal"] == pytest.approx(1784.60)
    assert d["observaciones"] == "ENTREGA EN BODEGA JUEVES 3 DE SEPTIEMBRE"
    assert d["warnings"] == []
    # la clave y el costo de OC vienen por partida — de ahí sale la validación
    tort = next(it for it in d["items"] if it["clave"] == "TORT-CERE-470")
    assert tort["costo_unitario"] == pytest.approx(38.90)
    assert tort["cantidad"] == pytest.approx(2)
    # el texto suelto pegado a la partida queda como nota, no revuelto en la
    # descripción
    caca = next(it for it in d["items"] if it["clave"] == "CACA-CERE-068")
    assert caca["nota"].startswith("2 BOLSAS DE 500 GRAMOS")


# ── importe con letra (idéntico al bot) ──────────────────────────────────────

def test_numero_a_letra():
    assert numero_a_letra(1798.60) == "MIL SETECIENTOS NOVENTA Y OCHO PESOS 60/100 M.N."
    assert numero_a_letra(7199.85) == "SIETE MIL CIENTO NOVENTA Y NUEVE PESOS 85/100 M.N."
    assert numero_a_letra(0) == "CERO PESOS 00/100 M.N."
    assert numero_a_letra(1_000_000) == "UN MILLON PESOS 00/100 M.N."
    assert numero_a_letra(121.21) == "CIENTO VEINTIUN PESOS 21/100 M.N."


# ── flujo completo contra la BD: reglas, alarma, totales y PDF ───────────────

_PURGE = (
    "lista_asignaciones", "precios", "listas_precios", "productos",
    "clientes", "esquemas_impuesto",
)

# (sku, nombre, unidad, precio en lista — None = sin precio, como en SAE)
_CATALOGO = [
    ("AGUA-FRUT-016", "AGUACATE", "KILO", "81.90"),
    ("HUEV-SANJ-1580", "HUEVO SAN JUAN", "KILO", "39.20"),
    ("PAPA-FRUT-321", "PAPA", "KILO", "43.00"),
    ("JAMO-CARN-2629", "JAMON DE PECHUGA DE PAVO FUD 1 KG", "PIEZA", "311.00"),
    ("TCPC-CARN-4053", "TOCINO PICADO", "KILO", None),
    ("TORT-CERE-470", "TORTILLA DE HARINA TRIGO 24 PZ TIA ROSA", "PIEZA", "45.90"),
    ("CACA-CERE-068", "CACAHUATE PELADO A GRANEL", "KILO", "57.75"),
    ("POLL-CARN-362", "POLLO EN PIEZA (MUSLO SIN PIEL)", "KILO", None),
    ("QOAX-LACT-4143", "QUESO OAXACA GANADERA", "KILO", None),
]


def _sembrar(db, tenants, slug, *, precio_de, es_default=False):
    """Un tenant completo con ESTE catálogo, su lista y su cliente Balles.

    `precio_de(sku, precio_del_catalogo)` decide el precio de cada producto
    (None = sin precio en la lista, como en SAE).
    """
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(slug=f"{slug}-{suffix}", legal_name="EMISOR REQ SA", rfc=f"R{suffix.upper()}X"[:13],
                    regimen_fiscal_sat="601", domicilio_fiscal_cp="78390",
                    domicilio_fiscal={"calle": "LEGUMBRES No. 302", "colonia": "ABASTOS",
                                      "ciudad": "SAN LUIS POTOSI", "estado": "SLP"},
                    tier="PRINCIPAL", status="ACTIVE")
    db.add(tenant); db.flush(); tenants.append(tenant.id)
    tid = tenant.id
    esq = EsquemaImpuesto(tenant_id=tid, codigo="E0", nombre="0%", iva_tasa=0, ieps_tasa=0)
    db.add(esq); db.flush()
    lista = ListaPrecios(tenant_id=tid, codigo="BALLES", nombre="BALLES JUBRAN",
                         es_default=es_default)
    db.add(lista); db.flush()
    for sku, nombre, unidad, precio in _CATALOGO:
        p = Producto(tenant_id=tid, sku=sku, nombre=nombre, esquema_impuesto_id=esq.id,
                     clave_sat="01010101", unidad_sat="KGM" if unidad == "KILO" else "H87",
                     unidad_base=unidad, presentaciones={unidad: 1},
                     presentacion_default=unidad)
        db.add(p); db.flush()
        monto = precio_de(sku, precio)
        if monto is not None:
            db.add(Precio(tenant_id=tid, lista_id=lista.id, producto_id=p.id,
                          presentacion=unidad, precio_unitario=Decimal(monto),
                          cantidad_minima=1))
    cli = Cliente(tenant_id=tid, codigo="7", legal_name="OPERADORA BALLES VEGA DE HIDALGO",
                  rfc="OBV191007BS1",
                  domicilio_fiscal={"calle": "SANTA CATARINA No. PARC 81",
                                    "colonia": "Santiago Tlapacoya Centro", "cp": "42110",
                                    "ciudad": "Pachuca de Soto", "estado": "Hidalgo"})
    db.add(cli); db.flush()
    db.add(ListaAsignacion(tenant_id=tid, lista_id=lista.id, cliente_id=cli.id))
    return tid


@pytest.fixture
def env(db_engine):
    """El tenant del test — y un VECINO con las mismas claves, a propósito.

    El vecino se siembra primero y trae precio para las nueve claves (todos
    $1.00) y su lista marcada como default: si alguna consulta del cotizador
    se olvida de filtrar por tenant, el cruce por SKU se lleva su producto y el
    resultado cambia entero. Así el test no depende de que la base de pruebas
    esté limpia (que no lo está: arrastra tenants de otras corridas) ni del
    orden en que Postgres devuelva las filas.
    """
    db = SessionLocal()
    tenants = []
    try:
        _sembrar(db, tenants, "cotreq-vecino",
                 precio_de=lambda sku, precio: "1.00", es_default=True)
        tid = _sembrar(db, tenants, "cotreq", precio_de=lambda sku, precio: precio)
        db.commit()
        yield {"tenant": tid}
    finally:
        for table in _PURGE:
            for t in tenants:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": t})
        for t in tenants:
            db.query(Tenant).filter(Tenant.id == t).delete()
        db.commit(); db.close()


def test_cotizar_requisicion_reglas_y_pdf(env):
    import base64

    import pdfplumber

    from app.services.cotizador import cotizar_requisicion

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == env["tenant"]).one()
        res = cotizar_requisicion(db, tenant, data=_pdf_bytes(), filename="req.pdf")
    finally:
        db.close()

    # el cliente se detecta del RFC impreso en la requisición
    assert res["cliente_nombre"] == "OPERADORA BALLES VEGA DE HIDALGO"
    assert res["folio"] == "0000006477"
    assert res["lineas"] == 9
    # 3 sin precio en lista pero CON precio de OC (se respeta) + 1 incorrecto;
    # el aguacate de 0.5 kg SÍ cotiza aunque el escalón arranque en 1
    assert res["alarma"] == ("ATENCION: 3 partida(s) con precio de producto no cotizado · "
                             "1 partida(s) con precio incorrecto — ver la nota en rojo de "
                             "cada partida.")
    assert [i["clave"] for i in res["incorrectos"]] == ["TORT-CERE-470"]
    assert sorted(i["clave"] for i in res["sin_autorizar"]) == [
        "POLL-CARN-362", "QOAX-LACT-4143", "TCPC-CARN-4053"]
    # el total usa el precio CORRECTO de la tortilla (45.90, no 38.90) y
    # respeta el de OC en las no cotizadas — igual que el bot
    assert res["total"] == "1798.60"
    assert res["pdf_filename"] == "Requisicion_0000006477.pdf"

    texto = ""
    with pdfplumber.open(io.BytesIO(base64.b64decode(res["pdf_base64"]))) as pdf:
        for page in pdf.pages:
            texto += page.extract_text() or ""
    assert "REQUISICION No.:" in texto
    assert "( 7 )" in texto
    assert "OPERADORA BALLES VEGA DE HIDALGO" in texto
    assert "SE RESPETA EL PRECIO DE OC" in texto
    assert "PRECIO INCORRECTO — OC $38.90" in texto
    assert "-> CORRECTO $45.90" in texto
    assert "MIL SETECIENTOS NOVENTA Y OCHO PESOS 60/100 M.N." in texto
    assert "ATENCION: 3 partida(s)" in texto
