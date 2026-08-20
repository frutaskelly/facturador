"""El endpoint público /contacto: anti-abuso (kill-switch, honeypot, formato) y
envío (SMTP no configurado → 503; configurado → send_email con Reply-To).

No requiere base de datos: /contacto no toca la BD.
"""
import pytest

from app.core.config import settings


def _payload(**overrides) -> dict:
    base = {
        "nombre": "Juan Pérez",
        "correo": "prospecto@example.com",
        "empresa": "Distribuidora del Norte",
        "telefono": "8112345678",
        "mensaje": "Quiero información sobre facturación con IA.",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _sin_rate_limit(monkeypatch):
    # Fail-open sin Redis, pero si hay un Redis local, correr la suite agotaría
    # el límite por hora. Neutralizamos el rate limit para el determinismo.
    monkeypatch.setattr("app.api.v1.contacto.hit", lambda *a, **k: (True, 0))


def _smtp_listo(monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "CONTACT_SMTP_USERNAME", "buzon@example.com")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "app-password-fake")
    monkeypatch.setattr(settings, "CONTACT_RECIPIENT", "gerencia@facturador.mx")


def _capturar(monkeypatch) -> dict:
    cap = {}

    def _fake_send(cfg, to, subject, html, attachments=None, reply_to=None):
        cap.update(cfg=cfg, to=to, subject=subject, html=html, reply_to=reply_to)

    monkeypatch.setattr("app.api.v1.contacto.email_service.send_email", _fake_send)
    return cap


def test_kill_switch(client, monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_ENABLED", False)
    r = client.post("/api/v1/contacto", json=_payload())
    assert r.status_code == 403


def test_honeypot(client):
    r = client.post("/api/v1/contacto", json=_payload(website="http://spam.example"))
    assert r.status_code == 400


def test_correo_invalido(client):
    r = client.post("/api/v1/contacto", json=_payload(correo="no-es-un-correo"))
    assert r.status_code == 422


def test_mensaje_muy_corto(client):
    r = client.post("/api/v1/contacto", json=_payload(mensaje="hi"))
    assert r.status_code == 422


def test_smtp_no_configurado_responde_503(client, monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_SMTP_HOST", "")
    monkeypatch.setattr(settings, "CONTACT_SMTP_USERNAME", "")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "")
    r = client.post("/api/v1/contacto", json=_payload())
    assert r.status_code == 503


def test_envio_ok_con_reply_to(client, monkeypatch):
    _smtp_listo(monkeypatch)
    cap = _capturar(monkeypatch)
    r = client.post("/api/v1/contacto", json=_payload())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert cap["to"] == ["gerencia@facturador.mx"]
    assert cap["subject"].startswith("Contacto facturador.mx —")
    # Reply-To = correo del prospecto (para responderle directo).
    assert cap["reply_to"] == "prospecto@example.com"
    assert "Distribuidora del Norte" in cap["html"]


def test_html_escapado_previene_inyeccion(client, monkeypatch):
    _smtp_listo(monkeypatch)
    cap = _capturar(monkeypatch)
    r = client.post(
        "/api/v1/contacto",
        json=_payload(mensaje="<script>alert('x')</script> hola mundo"),
    )
    assert r.status_code == 200
    assert "<script>" not in cap["html"]
    assert "&lt;script&gt;" in cap["html"]


def test_solo_telefono_es_valido(client, monkeypatch):
    """Sin correo pero con teléfono: se envía, y sin Reply-To (no hay a quién)."""
    _smtp_listo(monkeypatch)
    cap = _capturar(monkeypatch)
    r = client.post("/api/v1/contacto", json=_payload(correo=None, telefono="8112345678"))
    assert r.status_code == 200
    assert cap["reply_to"] is None


def test_sin_correo_ni_telefono_rechaza(client, monkeypatch):
    _smtp_listo(monkeypatch)
    _capturar(monkeypatch)
    r = client.post("/api/v1/contacto", json=_payload(correo=None, telefono=None))
    assert r.status_code == 422
