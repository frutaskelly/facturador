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


# ─── Ruteo por sitio: un endpoint, varias landings ────────────────────────────

def _smtp_listo(monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "CONTACT_SMTP_USERNAME", "buzon@example.com")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "app-password-fake")
    monkeypatch.setattr(
        settings,
        "CONTACT_RECIPIENTS",
        "facturador.mx=gerencia@facturador.mx,"
        "smartsupply.mx=gerencia@smartsupply.mx,"
        "miniconta.mx=gerencia@miniconta.mx",
    )
    monkeypatch.setattr(settings, "CONTACT_RECIPIENT", "fallback@example.com")


def _capturar(monkeypatch) -> dict:
    cap = {}

    def _fake_send(cfg, to, subject, html, attachments=None, reply_to=None):
        cap.update(to=to, subject=subject, html=html)

    monkeypatch.setattr("app.api.v1.contacto.email_service.send_email", _fake_send)
    return cap


@pytest.mark.parametrize(
    "origin,destino,etiqueta",
    [
        ("https://facturador.mx", "gerencia@facturador.mx", "facturador.mx"),
        ("https://www.smartsupply.mx", "gerencia@smartsupply.mx", "smartsupply.mx"),
        ("https://miniconta.mx", "gerencia@miniconta.mx", "miniconta.mx"),
    ],
)
def test_cada_sitio_entrega_en_su_alias(client, monkeypatch, origin, destino, etiqueta):
    _smtp_listo(monkeypatch)
    cap = _capturar(monkeypatch)
    r = client.post("/api/v1/contacto", json=_payload(), headers={"Origin": origin})
    assert r.status_code == 200
    assert cap["to"] == [destino]
    # El asunto y el cuerpo dicen de qué sitio viene (www. se normaliza).
    assert cap["subject"].startswith(f"Contacto {etiqueta} —")
    assert etiqueta in cap["html"]


def test_host_desconocido_cae_al_fallback(client, monkeypatch):
    """Un Origin no mapeado NO puede redirigir el correo a un tercero: los
    destinos son lista blanca, así que cae en CONTACT_RECIPIENT."""
    _smtp_listo(monkeypatch)
    cap = _capturar(monkeypatch)
    r = client.post(
        "/api/v1/contacto", json=_payload(), headers={"Origin": "https://sitio-malicioso.example"}
    )
    assert r.status_code == 200
    assert cap["to"] == ["fallback@example.com"]


# ─── Contrato "correo O teléfono" (smartsupply usa un solo campo) ─────────────

def test_solo_telefono_es_valido(client, monkeypatch):
    """Sin correo pero con teléfono: se envía, y sin Reply-To (no hay a quién)."""
    _smtp_listo(monkeypatch)
    cap = {}

    def _fake_send(cfg, to, subject, html, attachments=None, reply_to=None):
        cap.update(to=to, reply_to=reply_to)

    monkeypatch.setattr("app.api.v1.contacto.email_service.send_email", _fake_send)
    r = client.post(
        "/api/v1/contacto",
        json=_payload(correo=None, telefono="8112345678"),
        headers={"Origin": "https://smartsupply.mx"},
    )
    assert r.status_code == 200
    assert cap["reply_to"] is None
    assert cap["to"] == ["gerencia@smartsupply.mx"]


def test_sin_correo_ni_telefono_rechaza(client, monkeypatch):
    _smtp_listo(monkeypatch)
    _capturar(monkeypatch)
    r = client.post("/api/v1/contacto", json=_payload(correo=None, telefono=None))
    assert r.status_code == 422


# ─── Una App Password por sitio (revocable por separado) ─────────────────────

def test_cada_sitio_usa_su_propio_token(client, monkeypatch):
    _smtp_listo(monkeypatch)
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "token-generico")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD_FACTURADOR", "token-facturador")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD_SMARTSUPPLY", "token-smartsupply")

    usados = {}

    def _fake_send(cfg, to, subject, html, attachments=None, reply_to=None):
        usados[to[0]] = cfg["password"]

    monkeypatch.setattr("app.api.v1.contacto.email_service.send_email", _fake_send)

    client.post("/api/v1/contacto", json=_payload(), headers={"Origin": "https://facturador.mx"})
    client.post("/api/v1/contacto", json=_payload(), headers={"Origin": "https://smartsupply.mx"})

    assert usados["gerencia@facturador.mx"] == "token-facturador"
    assert usados["gerencia@smartsupply.mx"] == "token-smartsupply"


def test_sitio_sin_token_propio_cae_al_generico(client, monkeypatch):
    """Sin token propio no se rompe: usa el genérico (evita apagar un sitio)."""
    _smtp_listo(monkeypatch)
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "token-generico")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD_SMARTSUPPLY", "")

    cap = {}
    monkeypatch.setattr(
        "app.api.v1.contacto.email_service.send_email",
        lambda cfg, to, subject, html, attachments=None, reply_to=None: cap.update(pw=cfg["password"]),
    )
    r = client.post("/api/v1/contacto", json=_payload(), headers={"Origin": "https://smartsupply.mx"})
    assert r.status_code == 200
    assert cap["pw"] == "token-generico"


def test_sin_ningun_token_responde_503(client, monkeypatch):
    _smtp_listo(monkeypatch)
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD", "")
    monkeypatch.setattr(settings, "CONTACT_SMTP_PASSWORD_FACTURADOR", "")
    r = client.post("/api/v1/contacto", json=_payload(), headers={"Origin": "https://facturador.mx"})
    assert r.status_code == 503
