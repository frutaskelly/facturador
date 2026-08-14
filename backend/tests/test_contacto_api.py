"""El endpoint público /contacto: anti-abuso (kill-switch, honeypot, formato) y
envío (SMTP no configurado → 503; configurado → llama a send_email con Reply-To).

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
    # min_length=5 en el schema → 422 de validación de Pydantic.
    r = client.post("/api/v1/contacto", json=_payload(mensaje="hi"))
    assert r.status_code == 422


def test_smtp_no_configurado_responde_503(client, monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_SMTP_HOST", "")
    monkeypatch.setattr(settings, "CONTACT_SMTP_USERNAME", "")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "")
    r = client.post("/api/v1/contacto", json=_payload())
    assert r.status_code == 503


def test_envio_ok_llama_send_email_con_reply_to(client, monkeypatch):
    # SMTP "configurado".
    monkeypatch.setattr(settings, "CONTACT_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "CONTACT_SMTP_USERNAME", "gerencia@facturador.mx")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "app-password-fake")
    monkeypatch.setattr(settings, "CONTACT_RECIPIENT", "gerencia@facturador.mx")

    capturado = {}

    def _fake_send(cfg, to, subject, html, attachments=None, reply_to=None):
        capturado.update(cfg=cfg, to=to, subject=subject, html=html, reply_to=reply_to)

    monkeypatch.setattr("app.api.v1.contacto.email_service.send_email", _fake_send)

    r = client.post("/api/v1/contacto", json=_payload())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert capturado["to"] == ["gerencia@facturador.mx"]
    # Reply-To = correo del prospecto (para responderle directo).
    assert capturado["reply_to"] == "prospecto@example.com"
    # El input del usuario va escapado (sin inyección de HTML).
    assert "Distribuidora del Norte" in capturado["html"]


def test_html_escapado_previene_inyeccion(client, monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "CONTACT_SMTP_USERNAME", "gerencia@facturador.mx")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "app-password-fake")

    capturado = {}

    def _fake_send(cfg, to, subject, html, attachments=None, reply_to=None):
        capturado.update(html=html)

    monkeypatch.setattr("app.api.v1.contacto.email_service.send_email", _fake_send)

    r = client.post(
        "/api/v1/contacto",
        json=_payload(mensaje="<script>alert('x')</script> hola mundo"),
    )
    assert r.status_code == 200
    assert "<script>" not in capturado["html"]
    assert "&lt;script&gt;" in capturado["html"]
