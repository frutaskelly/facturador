"""Espejo de REP y notas de crédito de SAE (migración 0086).

Lo que tiene que sostenerse: el conector deposita y re-deposita sin duplicar,
los documentos llegan a Reportes, un REP del SAE no se cancela ni se imprime
desde el Facturador, y NADA de esto mueve el saldo de las facturas (ese saldo
ya llega calculado desde la CxC de SAE).
"""
from decimal import Decimal

from app.core.db import SessionLocal
from app.models import Factura

from .test_factura_espejo import (  # noqa: F401
    _clave_bot, _espejo, _hdr, auth_as, env, sin_sesion,
)


def _rep(**over):
    body = {
        "empresa": "02", "cve_doc": "11", "serie": "", "folio": 11, "cliente_sae": "6",
        "fecha_pago": "2026-08-20T00:00:00Z", "forma_pago": "03", "estado": "TIMBRADO",
        "uuid": "29EA930D-9BE9-47B8-8C94-95FC7DA0A1CC",
        "facturas": [
            {"serie": "ZHGO", "folio": 233, "importe": "500.00", "num_parcialidad": 1,
             "saldo_anterior": "969.76", "saldo_insoluto": "469.76"},
            {"serie": "ZHGO", "folio": 999, "importe": "100.00"},   # no está en el espejo
        ],
    }
    body.update(over)
    return body


def _saldo(serie, folio, tenant):
    db = SessionLocal()
    try:
        f = db.query(Factura).filter(Factura.tenant_id == tenant, Factura.serie == serie,
                                     Factura.folio == folio).one()
        return Decimal(f.saldo_insoluto)
    finally:
        db.close()


def test_rep_espejo_idempotente_y_en_reportes(client, env, auth_as, sin_sesion):
    bot = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/facturas/espejo", json=_espejo(saldo_insoluto="469.76"), headers=bot)
    assert r.status_code == 201, r.text
    antes = _saldo("ZHGO", 233, env["tenant"])

    r = client.post("/api/v1/cobranza/espejo/recibo-pago", json=_rep(), headers=bot)
    assert r.status_code == 200, r.text
    assert Decimal(str(r.json()["monto"])) == Decimal("600.00")
    # re-depositar ACTUALIZA, no duplica
    r2 = client.post("/api/v1/cobranza/espejo/recibo-pago", json=_rep(), headers=bot)
    assert r2.json()["id"] == r.json()["id"]
    # el mismo CVE_DOC de OTRA empresa es otro comprobante (SAE numera por empresa)
    otro = client.post("/api/v1/cobranza/espejo/recibo-pago",
                       json=_rep(empresa="04", facturas=[]), headers=bot)
    # la 04:6 no tiene equivalencia en el fixture: se rechaza, no se pisa el de la 02
    assert otro.status_code == 422

    # el saldo de la factura NO se toca: lo manda la CxC de SAE
    assert _saldo("ZHGO", 233, env["tenant"]) == antes

    auth_as(env["dueno"])
    d = client.get("/api/v1/reportes/pagos", params={"desde": "2026-08-01", "hasta": "2026-08-31"},
                   headers=_hdr(env["dueno"])).json()
    assert d["comprobantes"] == 1 and Decimal(str(d["total"])) == Decimal("600.00")
    it = d["items"][0]
    assert it["origen"] == "ESPEJO_SAE"
    refs = {f["factura_ref"]: (f["serie"], f["folio"]) for f in it["facturas"]}
    # la que no está en el espejo conserva su nombre de SAE en vez de perderse
    assert refs == {"ZHGO233": ("ZHGO", 233), "ZHGO999": (None, None)}

    # desde el Facturador no se cancela ni se imprime: eso es de SAE
    rid = it["id"]
    assert client.post(f"/api/v1/cobranza/recibos-pago/{rid}/cancelar", json={},
                       headers=_hdr(env["dueno"])).status_code == 409
    assert client.get(f"/api/v1/cobranza/recibos-pago/{rid}/pdf",
                      headers=_hdr(env["dueno"])).status_code == 409


def test_rep_cancelado_conserva_renglones_y_sale_aparte(client, env, auth_as, sin_sesion):
    bot = _clave_bot(client, env, auth_as, sin_sesion)
    client.post("/api/v1/facturas/espejo", json=_espejo(), headers=bot)
    client.post("/api/v1/cobranza/espejo/recibo-pago", json=_rep(), headers=bot)
    # SAE borra los renglones de la CxC al cancelar: llega sin facturas
    r = client.post("/api/v1/cobranza/espejo/recibo-pago",
                    json=_rep(estado="CANCELADO", facturas=[],
                              fecha_cancelacion="2026-08-25T00:00:00Z"), headers=bot)
    assert r.status_code == 200, r.text
    # y un reintento viejo no lo revive
    assert client.post("/api/v1/cobranza/espejo/recibo-pago", json=_rep(),
                       headers=bot).status_code == 409

    auth_as(env["dueno"])
    d = client.get("/api/v1/reportes/pagos", params={"desde": "2026-08-01", "hasta": "2026-08-31"},
                   headers=_hdr(env["dueno"])).json()
    assert d["comprobantes"] == 0 and d["cancelados"] == 1
    assert Decimal(str(d["total_cancelado"])) == Decimal("600.00")
    assert len(d["items"][0]["facturas"]) == 2


def test_nota_credito_espejo_en_reportes(client, env, auth_as, sin_sesion):
    bot = _clave_bot(client, env, auth_as, sin_sesion)
    client.post("/api/v1/facturas/espejo", json=_espejo(), headers=bot)
    nc = {
        "empresa": "02", "cve_doc": "NCZMAFAN         1", "serie": "NCZMAFAN", "folio": 1,
        "cliente_sae": "6", "fecha": "2026-08-15T09:24:08Z", "estado": "VIGENTE",
        "uuid": "667AFEC0-3BF2-4719-873E-5D91BD9F158E",
        "facturas": [{"serie": "ZHGO", "folio": 233, "importe": "147.58"}],
    }
    r = client.post("/api/v1/cobranza/espejo/nota-credito", json=nc, headers=bot)
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v1/cobranza/espejo/nota-credito", json=nc, headers=bot)
    assert r2.json()["id"] == r.json()["id"]

    auth_as(env["dueno"])
    d = client.get("/api/v1/reportes/notas-credito",
                   params={"desde": "2026-08-01", "hasta": "2026-08-31"},
                   headers=_hdr(env["dueno"])).json()
    assert d["notas"] == 1 and Decimal(str(d["total"])) == Decimal("147.58")
    assert d["items"][0]["facturas"][0]["folio"] == 233

    r = client.post("/api/v1/cobranza/espejo/nota-credito",
                    json={**nc, "estado": "CANCELADA", "facturas": []}, headers=bot)
    assert r.status_code == 200
    d = client.get("/api/v1/reportes/notas-credito",
                   params={"desde": "2026-08-01", "hasta": "2026-08-31"},
                   headers=_hdr(env["dueno"])).json()
    assert d["notas"] == 0 and d["canceladas"] == 1

