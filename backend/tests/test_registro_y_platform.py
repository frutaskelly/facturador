"""El endpoint público /registro (anti-abuso + validación) y el gate de
/platform (allowlist de operador) — las dos superficies más sensibles que no
tenían ningún test.

El camino feliz completo de /registro no se prueba aquí: requiere Supabase Auth
real. Lo que sí se asegura es que TODAS las barreras previas (kill-switch,
honeypot, formato, unicidad) respondan lo esperado, y que un payload válido
llegue hasta el guard de Auth (503 con Auth sin configurar = pasó todo lo demás).
"""
import uuid

import pytest
from fastapi import HTTPException

from app.core.auth import Principal
from app.core.config import settings
from app.core.db import SessionLocal
from app.api.v1.platform import require_operator
from app.models import User


def _payload(**overrides) -> dict:
    base = {
        "legal_name": "Empresa Prueba SA de CV",
        "rfc": "EPR990101AB1",
        "regimen_fiscal_sat": "601",
        "domicilio_fiscal_cp": "44100",
        "owner_email": "owner-registro@test.local",
        "password": "supersecreta1",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _sin_rate_limit(monkeypatch):
    # El rate limit por IP es fail-open sin Redis, pero si hay un Redis local
    # escuchando, correr la suite varias veces agotaría las 5 altas/hora.
    monkeypatch.setattr("app.api.v1.registro.hit", lambda *a, **k: (True, 0))


def test_registro_kill_switch(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ENABLED", False)
    r = client.post("/api/v1/registro", json=_payload())
    assert r.status_code == 403


def test_registro_honeypot(client):
    r = client.post("/api/v1/registro", json=_payload(website="http://spam.example"))
    assert r.status_code == 400


def test_registro_email_invalido(client):
    r = client.post("/api/v1/registro", json=_payload(owner_email="no-es-un-correo"))
    assert r.status_code == 422


def test_registro_rfc_invalido(client):
    r = client.post("/api/v1/registro", json=_payload(rfc="RFC-INVALIDO"))
    assert r.status_code == 422


def test_registro_cp_invalido(client):
    r = client.post("/api/v1/registro", json=_payload(domicilio_fiscal_cp="ABCDE"))
    assert r.status_code == 422


def test_registro_email_duplicado(client, db_engine):
    """Un correo ya provisionado responde 409 ANTES de tocar Supabase Auth."""
    suffix = uuid.uuid4().hex[:8]
    email = f"dup-{suffix}@test.local"
    db = SessionLocal()
    try:
        user = User(email=email, full_name="Duplicado")
        db.add(user)
        db.commit()
        r = client.post("/api/v1/registro", json=_payload(owner_email=email))
        assert r.status_code == 409
    finally:
        db.query(User).filter(User.email == email).delete()
        db.commit()
        db.close()


def test_registro_valido_topa_con_auth_no_configurado(client, db_engine):
    """Payload válido: pasa anti-abuso, formato y unicidad, y se detiene en el
    guard de Auth (503 en el entorno de tests, que no tiene service key)."""
    suffix = uuid.uuid4().hex[:3].upper()   # homoclave única [A-Z0-9]{3}
    r = client.post(
        "/api/v1/registro",
        json=_payload(owner_email=f"ok-{suffix.lower()}@test.local", rfc=f"EPR990101{suffix}"),
    )
    assert r.status_code == 503


# ─── /platform: allowlist de operador ─────────────────────────────────────────

def _principal(email: str) -> Principal:
    return Principal(auth_user_id="sub-x", email=email, role="authenticated", claims={"sub": "sub-x"})


def test_platform_requiere_token(client):
    assert client.get("/api/v1/platform/me").status_code == 401
    assert client.get("/api/v1/platform/tenants").status_code == 401


def test_platform_rechaza_email_fuera_de_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_OPERATORS", "op@empresa.mx")
    with pytest.raises(HTTPException) as exc:
        require_operator(_principal("intruso@otro.mx"))
    assert exc.value.status_code == 403


def test_platform_rechaza_allowlist_vacia(monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_OPERATORS", "")
    with pytest.raises(HTTPException) as exc:
        require_operator(_principal("cualquiera@x.mx"))
    assert exc.value.status_code == 403


def test_platform_acepta_operador(monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_OPERATORS", "op@empresa.mx, otra@empresa.mx")
    p = require_operator(_principal("OP@empresa.mx"))  # case-insensitive
    assert p.email == "OP@empresa.mx"
