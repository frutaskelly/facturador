"""Cobranza automática: config, contactos, cola, candados y envío."""
import datetime as dt
import uuid

import pytest

from app.core.db import SessionLocal
from app.models import EspejoSync
from app.services import cobranza_auto as svc
from tests.test_cobranza_api import (  # noqa: F401
    _factura_ppd_timbrada, _h, auth, env,
)

_BASE = "/api/v1/cobranza/automatica"

_CONFIG = {
    "activo": True, "modo": "REVISION", "dia_semana": 0, "hora": 8,
    "incluir_por_vencer": True, "saldo_minimo": 100, "escalar_dias": 30,
    "escalar_cc": ["jefe@negocio.example.com"], "cc_siempre": [], "adjuntar_pdf": True,
    "adjuntar_excel": True, "espejo_max_horas": 0, "asunto": None, "mensaje": "Buen día.",
}


@pytest.fixture
def correo(monkeypatch):
    """SMTP falso: guarda lo que se habría mandado."""
    from app.services import email as email_service
    enviados = []
    monkeypatch.setattr(email_service, "configured", lambda tenant: True)
    monkeypatch.setattr(email_service, "smtp_config", lambda tenant: {})
    monkeypatch.setattr(email_service, "send_email",
                        lambda cfg, to, subject, html, attachments=None, reply_to=None, cc=None:
                        enviados.append({"to": to, "cc": cc, "subject": subject,
                                         "adjuntos": [a[0] for a in attachments or []]}))
    return enviados


def _config(client, env, **cambios):
    r = client.put(f"{_BASE}/config", json={**_CONFIG, **cambios}, headers=_h(env))
    assert r.status_code == 200, r.text
    return r.json()


def _contacto(client, env, **kw):
    r = client.put(f"{_BASE}/contactos", json={"cliente_id": env["cli"], **kw}, headers=_h(env))
    assert r.status_code == 200, r.text
    return r.json()


def test_config_se_crea_apagada_y_se_guarda(client, env, auth):
    d = client.get(f"{_BASE}/config", headers=_h(env)).json()
    assert d["activo"] is False and d["modo"] == "REVISION"
    d = _config(client, env, dia_semana=2, hora=9, incluir_por_vencer=False)
    assert (d["dia_semana"], d["hora"], d["incluir_por_vencer"]) == (2, 9, False)
    r = client.put(f"{_BASE}/config", json={**_CONFIG, "escalar_cc": ["no-es-correo"]}, headers=_h(env))
    assert r.status_code == 422


def test_cola_sin_correo_se_marca_y_con_correo_se_envia(client, env, auth, correo):
    _config(client, env)
    _factura_ppd_timbrada(env, total=1000, dias_atras=10, folio=1)       # por vencer
    _factura_ppd_timbrada(env, total=2000, dias_atras=40, folio=2)       # vencida 10 días

    r = client.post(f"{_BASE}/generar", headers=_h(env)).json()
    assert r["creados"] == 1
    # generar otra vez el mismo día no duplica
    assert client.post(f"{_BASE}/generar", headers=_h(env)).json()["creados"] == 0

    cola = client.get(f"{_BASE}/envios", headers=_h(env)).json()
    assert len(cola) == 1 and cola[0]["estado"] == "PENDIENTE"
    assert float(cola[0]["saldo"]) == 3000.0 and float(cola[0]["vencido"]) == 2000.0
    assert "Sin correo" in cola[0]["error"]

    # sin correo el envío falla y lo dice
    out = client.post(f"{_BASE}/envios/enviar", json={"ids": [cola[0]["id"]]}, headers=_h(env)).json()
    assert out[0]["estado"] == "ERROR" and correo == []

    # se captura el correo DESPUÉS de armar la cola: el reintento ya lo usa
    _contacto(client, env, correos=["pagos@cliente.example.com"])
    out = client.post(f"{_BASE}/envios/enviar", json={"ids": [cola[0]["id"]]}, headers=_h(env)).json()
    assert out[0]["estado"] == "ENVIADO" and correo[0]["to"] == ["pagos@cliente.example.com"]


def test_envio_con_contacto_y_escalamiento(client, env, auth, correo):
    _config(client, env)
    _contacto(client, env, correos=["pagos@cliente.example.com"], cc=["conta@cliente.example.com"])
    _factura_ppd_timbrada(env, total=2000, dias_atras=80, folio=2)       # vencida 50 días → escala

    client.post(f"{_BASE}/generar", headers=_h(env))
    cola = client.get(f"{_BASE}/envios", headers=_h(env)).json()
    assert cola[0]["para"] == ["pagos@cliente.example.com"]
    assert cola[0]["escalado"] is True and "jefe@negocio.example.com" in cola[0]["cc"]

    out = client.post(f"{_BASE}/envios/enviar", json={"ids": [cola[0]["id"]]}, headers=_h(env)).json()
    assert out[0]["estado"] == "ENVIADO", out
    assert correo[0]["to"] == ["pagos@cliente.example.com"]
    assert set(correo[0]["cc"]) == {"conta@cliente.example.com", "jefe@negocio.example.com"}
    assert any(a.endswith(".pdf") for a in correo[0]["adjuntos"])
    assert any(a.endswith(".xlsx") for a in correo[0]["adjuntos"])
    # ya enviado: un segundo clic no manda otro correo
    client.post(f"{_BASE}/envios/enviar", json={"ids": [cola[0]["id"]]}, headers=_h(env))
    assert len(correo) == 1


def test_pausado_y_solo_vencidas(client, env, auth, correo):
    _config(client, env, incluir_por_vencer=False)
    _contacto(client, env, correos=["pagos@cliente.example.com"])
    _factura_ppd_timbrada(env, total=1000, dias_atras=10, folio=1)       # por vencer: fuera

    assert client.post(f"{_BASE}/generar", headers=_h(env)).json()["creados"] == 0

    _factura_ppd_timbrada(env, total=500, dias_atras=40, folio=2)
    _contacto(client, env, correos=["pagos@cliente.example.com"], pausado=True, motivo_pausa="convenio")
    assert client.post(f"{_BASE}/generar", headers=_h(env)).json()["creados"] == 0

    _contacto(client, env, correos=["pagos@cliente.example.com"], pausado=False)
    client.post(f"{_BASE}/generar", headers=_h(env))
    cola = client.get(f"{_BASE}/envios", headers=_h(env)).json()
    assert float(cola[0]["saldo"]) == 500.0 and cola[0]["facturas"] == 1


def test_contacto_por_serie_parte_el_estado_de_cuenta(client, env, auth, correo):
    _config(client, env)
    _contacto(client, env, correos=["general@cliente.example.com"])
    _contacto(client, env, serie="ZEH", correos=["plaza@cliente.example.com"])
    _factura_ppd_timbrada(env, total=1000, dias_atras=10, folio=1, serie="ZEH")
    _factura_ppd_timbrada(env, total=2000, dias_atras=10, folio=2, serie="FEH")

    client.post(f"{_BASE}/generar", headers=_h(env))
    cola = {(e["serie"] or ""): e for e in client.get(f"{_BASE}/envios", headers=_h(env)).json()}
    assert float(cola["ZEH"]["saldo"]) == 1000.0 and cola["ZEH"]["para"] == ["plaza@cliente.example.com"]
    assert float(cola[""]["saldo"]) == 2000.0 and cola[""]["para"] == ["general@cliente.example.com"]


def test_candado_del_espejo(client, env, auth, correo):
    _config(client, env, espejo_max_horas=6)
    _contacto(client, env, correos=["pagos@cliente.example.com"])
    _factura_ppd_timbrada(env, total=1000, dias_atras=40, folio=1)
    client.post(f"{_BASE}/generar", headers=_h(env))
    eid = client.get(f"{_BASE}/envios", headers=_h(env)).json()[0]["id"]

    out = client.post(f"{_BASE}/envios/enviar", json={"ids": [eid]}, headers=_h(env)).json()
    assert out[0]["estado"] == "PENDIENTE" and "espejo" in out[0]["error"].lower()
    assert correo == []

    db = SessionLocal()
    try:
        db.add(EspejoSync(tenant_id=uuid.UUID(str(env["tenant_id"])), estado="OK", origen="AUTOMATICA",
                          terminada_at=dt.datetime.now(dt.timezone.utc)))
        db.commit()
    finally:
        db.close()
    out = client.post(f"{_BASE}/envios/enviar", json={"ids": [eid]}, headers=_h(env)).json()
    assert out[0]["estado"] == "ENVIADO"


def test_toca_generar():
    cfg = svc.CobranzaConfig(dia_semana=0, hora=8, zona="America/Mexico_City", ultima_generacion=None)
    lunes_9 = dt.datetime(2026, 9, 28, 15, 0, tzinfo=dt.timezone.utc)   # 9:00 en CDMX
    lunes_7 = dt.datetime(2026, 9, 28, 13, 0, tzinfo=dt.timezone.utc)   # 7:00 en CDMX
    assert svc.toca_generar(cfg, lunes_9) is True
    assert svc.toca_generar(cfg, lunes_7) is False
    cfg.ultima_generacion = dt.date(2026, 9, 28)
    assert svc.toca_generar(cfg, lunes_9) is False
