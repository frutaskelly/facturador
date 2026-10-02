"""Cobranza: Ajustes generales, el botón Enviar, el automático y la bitácora."""
import datetime as dt
import uuid

import pytest

from app.core.db import SessionLocal
from app.models import CobranzaConfig, CobranzaEnvio, CobranzaGrupo, EspejoSync
from app.services import cobranza_auto as svc
from tests.test_cobranza_api import (  # noqa: F401
    _factura_ppd_timbrada, _h, auth, env,
)

_BASE = "/api/v1/cobranza/automatica"
_GRUPOS = f"{_BASE}/grupos"


@pytest.fixture
def correo(monkeypatch):
    """SMTP falso: guarda lo que se habría mandado."""
    from app.services import email as email_service
    enviados = []
    monkeypatch.setattr(email_service, "configured", lambda tenant: True)
    monkeypatch.setattr(email_service, "smtp_config", lambda tenant: {})
    monkeypatch.setattr(email_service, "send_email",
                        lambda cfg, to, subject, html, attachments=None, reply_to=None, cc=None:
                        enviados.append({"to": to, "cc": cc, "subject": subject, "html": html,
                                         "adjuntos": [a[0] for a in attachments or []]}))
    return enviados


def _config(client, env, **cambios):
    """Ajustes generales; por omisión sin candado del espejo (los tests no tienen SAE)."""
    cuerpo = {"activo": False, "espejo_max_horas": 0, "cc_siempre": [], **cambios}
    r = client.put(f"{_BASE}/config", json=cuerpo, headers=_h(env))
    assert r.status_code == 200, r.text
    return r.json()


def _envio(client, env, **kw):
    cuerpo = {"nombre": "Cliente", "alcance": [{"cliente_id": env["cli"]}],
              "correos": ["pagos@cliente.example.com"], **kw}
    r = client.post(_GRUPOS, json=cuerpo, headers=_h(env))
    assert r.status_code == 201, r.text
    return r.json()


def _espejo_ok(env):
    db = SessionLocal()
    try:
        db.add(EspejoSync(tenant_id=uuid.UUID(str(env["tenant_id"])), estado="OK", origen="AUTOMATICA",
                          terminada_at=dt.datetime.now(dt.timezone.utc)))
        db.commit()
    finally:
        db.close()


def test_ajustes_generales(client, env, auth):
    d = client.get(f"{_BASE}/config", headers=_h(env)).json()
    assert d["activo"] is False and d["espejo_max_horas"] == 6
    d = _config(client, env, activo=True, espejo_max_horas=12, cc_siempre=["cobranza@negocio.example.com"])
    assert (d["activo"], d["espejo_max_horas"], d["cc_siempre"]) == (True, 12, ["cobranza@negocio.example.com"])
    r = client.put(f"{_BASE}/config", json={"activo": True, "espejo_max_horas": 0, "cc_siempre": ["x"]},
                   headers=_h(env))
    assert r.status_code == 422


def test_enviar_con_un_clic(client, env, auth, correo):
    _config(client, env, cc_siempre=["cobranza@negocio.example.com"])
    _factura_ppd_timbrada(env, total=1000, dias_atras=10, folio=1)       # por vencer
    _factura_ppd_timbrada(env, total=2000, dias_atras=40, folio=2)       # vencida
    g = _envio(client, env, cc=["conta@cliente.example.com"], asunto="Cobranza semanal",
               mensaje="Buen día.", adjuntar_pdf=False)

    r = client.post(f"{_GRUPOS}/{g['id']}/enviar", headers=_h(env))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["envio"]["estado"] == "ENVIADO" and float(out["envio"]["saldo"]) == 3000.0
    assert correo[0]["to"] == ["pagos@cliente.example.com"]
    assert correo[0]["cc"] == ["conta@cliente.example.com", "cobranza@negocio.example.com"]
    assert correo[0]["subject"] == "Cobranza semanal"
    assert "Buen día." in correo[0]["html"]
    assert [a.rsplit(".", 1)[1] for a in correo[0]["adjuntos"]] == ["xlsx"]   # el PDF va apagado
    # La fila de la lista ya trae el último envío; el historial lo apunta.
    assert out["grupo"]["ultimo"]["estado"] == "ENVIADO"
    hist = client.get(f"{_BASE}/envios", headers=_h(env)).json()
    assert [(h["envio"], h["origen"], h["estado"]) for h in hist] == [("Cliente", "MANUAL", "ENVIADO")]


def test_el_boton_no_manda_si_algo_lo_impide(client, env, auth, correo):
    _config(client, env)
    g = _envio(client, env, correos=[])
    r = client.post(f"{_GRUPOS}/{g['id']}/enviar", headers=_h(env))
    assert r.status_code == 409 and "Falta el correo" in r.json()["detail"]

    g2 = _envio(client, env, nombre="Con correo")
    r = client.post(f"{_GRUPOS}/{g2['id']}/enviar", headers=_h(env))
    assert r.status_code == 409 and "No hay facturas" in r.json()["detail"]
    # Ninguno dejó rastro ni mandó nada.
    assert correo == [] and client.get(f"{_BASE}/envios", headers=_h(env)).json() == []


def test_candado_del_espejo(client, env, auth, correo):
    _config(client, env, espejo_max_horas=6)
    _factura_ppd_timbrada(env, total=1000, dias_atras=40, folio=1)
    g = _envio(client, env)
    r = client.post(f"{_GRUPOS}/{g['id']}/enviar", headers=_h(env))
    assert r.status_code == 409 and "espejo" in r.json()["detail"].lower()
    _espejo_ok(env)
    r = client.post(f"{_GRUPOS}/{g['id']}/enviar", headers=_h(env))
    assert r.json()["envio"]["estado"] == "ENVIADO"


def test_modo_con_un_clic_y_proximo(client, env, auth):
    _config(client, env, activo=True)
    g = _envio(client, env, dia_semana=2, hora=9)
    assert g["modo"] == "MANUAL" and g["proximo"] is None and g["cuando"] == "Miércoles a las 09:00"
    r = client.patch(f"{_GRUPOS}/{g['id']}/modo", json={"modo": "AUTOMATICO"}, headers=_h(env))
    assert r.status_code == 200 and r.json()["modo"] == "AUTOMATICO"
    proximo = dt.datetime.fromisoformat(r.json()["proximo"])
    assert proximo.weekday() == 2 and proximo.hour == 9
    # Con el interruptor maestro apagado no hay próximo.
    _config(client, env, activo=False)
    assert client.get(_GRUPOS, headers=_h(env)).json()["envios"][0]["proximo"] is None


def _grupo_db(gid):
    db = SessionLocal()
    return db, db.query(CobranzaGrupo).filter(CobranzaGrupo.id == uuid.UUID(gid)).one()


def test_automatico_una_vez_por_dia(client, env, auth, correo):
    _config(client, env, espejo_max_horas=6)
    _factura_ppd_timbrada(env, total=1000, dias_atras=40, folio=1)
    g = _envio(client, env, modo="AUTOMATICO")
    tid = uuid.UUID(str(env["tenant_id"]))

    db, grupo = _grupo_db(g["id"])
    try:
        # Espejo viejo: el día queda PENDIENTE y se reintenta.
        e = svc.enviar_grupo(db, tid, grupo, origen="PROGRAMADO"); db.commit()
        assert e.estado == "PENDIENTE" and correo == []
        _espejo_ok(env)
        e = svc.enviar_grupo(db, tid, grupo, origen="PROGRAMADO"); db.commit()
        assert e.estado == "ENVIADO" and len(correo) == 1
        # Ya salió hoy: otra vuelta del reloj no manda otro.
        assert svc.enviar_grupo(db, tid, grupo, origen="PROGRAMADO") is None
        db.commit()
        assert len(correo) == 1
        filas = db.query(CobranzaEnvio).filter(CobranzaEnvio.grupo_id == grupo.id).all()
        assert [(f.origen, f.estado) for f in filas] == [("PROGRAMADO", "ENVIADO")]
    finally:
        db.close()


def test_automatico_respeta_el_minimo_y_el_boton_no(client, env, auth, correo):
    _config(client, env)
    _factura_ppd_timbrada(env, total=50, dias_atras=40, folio=1)
    g = _envio(client, env, modo="AUTOMATICO", saldo_minimo=100)
    db, grupo = _grupo_db(g["id"])
    try:
        e = svc.enviar_grupo(db, uuid.UUID(str(env["tenant_id"])), grupo, origen="PROGRAMADO"); db.commit()
        assert e.estado == "DESCARTADO" and "mínimo" in e.error and correo == []
    finally:
        db.close()
    r = client.post(f"{_GRUPOS}/{g['id']}/enviar", headers=_h(env))
    assert r.json()["envio"]["estado"] == "ENVIADO" and len(correo) == 1


def test_escalamiento_por_envio(client, env, auth, correo):
    _config(client, env)
    _factura_ppd_timbrada(env, total=2000, dias_atras=80, folio=1)       # vencida 50 días (30 de crédito)
    g = _envio(client, env, escalar_dias=30, escalar_cc=["jefe@negocio.example.com"])
    out = client.post(f"{_GRUPOS}/{g['id']}/enviar", headers=_h(env)).json()
    assert out["envio"]["escalado"] is True and correo[0]["cc"] == ["jefe@negocio.example.com"]


def test_solo_vencidas_por_envio(client, env, auth, correo):
    _config(client, env)
    _factura_ppd_timbrada(env, total=1000, dias_atras=10, folio=1)       # por vencer: fuera
    _factura_ppd_timbrada(env, total=500, dias_atras=40, folio=2)
    g = _envio(client, env, incluir_por_vencer=False)
    assert float(g["saldo"]) == 500.0
    out = client.post(f"{_GRUPOS}/{g['id']}/enviar", headers=_h(env)).json()
    assert float(out["envio"]["saldo"]) == 500.0 and out["envio"]["facturas"] == 1


def test_enviar_varios(client, env, auth, correo):
    _config(client, env)
    _factura_ppd_timbrada(env, total=1000, dias_atras=40, folio=1)
    a = _envio(client, env, nombre="A")
    b = _envio(client, env, nombre="B", correos=[])
    out = client.post(f"{_GRUPOS}/enviar", json={"ids": [a["id"], b["id"]]}, headers=_h(env)).json()
    assert [(x["nombre"], x["estado"]) for x in out] == [("A", "ENVIADO"), ("B", "NO_SALIO")]
    assert len(correo) == 1


def test_toca_y_proximo():
    cfg = CobranzaConfig(zona="America/Mexico_City")
    g = CobranzaGrupo(dia_semana=0, hora=8)
    lunes_9 = dt.datetime(2026, 9, 28, 15, 0, tzinfo=dt.timezone.utc)   # 9:00 en CDMX
    lunes_7 = dt.datetime(2026, 9, 28, 13, 0, tzinfo=dt.timezone.utc)   # 7:00 en CDMX
    martes = dt.datetime(2026, 9, 29, 15, 0, tzinfo=dt.timezone.utc)
    assert svc.toca(g, cfg, lunes_9) is True
    assert svc.toca(g, cfg, lunes_7) is False
    assert svc.toca(g, cfg, martes) is False
    assert svc.proximo(g, cfg, lunes_7, salio_hoy=False).date() == dt.date(2026, 9, 28)
    assert svc.proximo(g, cfg, lunes_9, salio_hoy=True).date() == dt.date(2026, 10, 5)
    assert svc.proximo(g, cfg, martes, salio_hoy=False).date() == dt.date(2026, 10, 5)
