"""Cobranza F1 — estado de cuenta del cliente con antigüedad de saldos.

Verifica: saldo_insoluto se inicializa al timbrar (PPD=total, PUE=0) y el
estado de cuenta agrupa por fecha de vencimiento (fecha + días de crédito) en
cubetas de 30 días con "por vencer", estilo SAE.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Cliente, Factura, Membership, Role, Tenant, User

_PURGE = ("lineas_factura", "facturas", "clientes")


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"m": [], "u": [], "t": []}
    try:
        tenant = Tenant(slug=f"cob-{suffix}", legal_name="COB SA", rfc=f"C{suffix.upper()}B"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
                        tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["t"].append(tenant.id)
        role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-cob-{suffix}"
        u = User(email=f"cob-{suffix}@t.test", auth_user_id=sub, full_name="admin")
        db.add(u); db.flush(); created["u"].append(u.id)
        m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
        db.add(m); db.flush(); created["m"].append(m.id)
        cli = Cliente(tenant_id=tenant.id, codigo="COBC", legal_name="Cliente Cobranza",
                      rfc="XAXX010101000", regimen_fiscal="601", uso_cfdi_default="G03",
                      dias_credito=30, limite_credito=Decimal("100000"))
        db.add(cli); db.flush()
        db.commit()
        yield {"sub": sub, "email": u.email, "tenant_id": tenant.id, "cli": str(cli.id)}
    finally:
        for tb in _PURGE:
            for tid in created["t"]:
                db.execute(text(f"DELETE FROM {tb} WHERE tenant_id = :t"), {"t": tid})
        for mid in created["m"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["u"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["t"]:
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit(); db.close()


@pytest.fixture
def auth(env):
    app.dependency_overrides[get_principal] = lambda: Principal(
        auth_user_id=env["sub"], email=env["email"], role="authenticated", claims={"sub": env["sub"]})
    yield
    app.dependency_overrides.pop(get_principal, None)


def _h(env):
    return {"X-Tenant-Id": str(env["tenant_id"])}


def _factura_ppd_timbrada(env, *, total, dias_atras, metodo="PPD", folio):
    """Inserta una factura TIMBRADA directamente (sin PAC) con fecha dada."""
    db = SessionLocal()
    try:
        f = Factura(
            tenant_id=uuid.UUID(str(env["tenant_id"])), serie="F", folio=folio,
            cliente_id=uuid.UUID(env["cli"]), metodo_pago=metodo, forma_pago="99",
            total=Decimal(str(total)), subtotal=Decimal(str(total)),
            estado="TIMBRADA", uuid=str(uuid.uuid4()),
            fecha=datetime.now(timezone.utc) - timedelta(days=dias_atras),
            saldo_insoluto=Decimal(str(total)) if metodo == "PPD" else Decimal("0"),
        )
        db.add(f); db.commit()
        return str(f.id)
    finally:
        db.close()


def test_estado_cuenta_antiguedad_por_vencimiento(client, env, auth):
    # Cliente con 30 días de crédito. Facturas:
    #  - $1000 hace 10 días  → vence en +20 → POR VENCER
    #  - $2000 hace 40 días  → venció hace 10 → 1-30
    #  - $3000 hace 100 días → venció hace 70 → 61-90
    #  - $500 PUE hace 5 días → saldo 0, NO aparece
    _factura_ppd_timbrada(env, total=1000, dias_atras=10, folio=1)
    _factura_ppd_timbrada(env, total=2000, dias_atras=40, folio=2)
    _factura_ppd_timbrada(env, total=3000, dias_atras=100, folio=3)
    _factura_ppd_timbrada(env, total=500, dias_atras=5, metodo="PUE", folio=4)

    r = client.get(f"/api/v1/cobranza/estado-cuenta/{env['cli']}", headers=_h(env))
    assert r.status_code == 200, r.text
    d = r.json()
    assert float(d["saldo_total"]) == 6000.0            # PUE no cuenta
    assert len(d["facturas"]) == 3
    a = d["antiguedad"]
    assert float(a["por_vencer"]) == 1000.0
    assert float(a["d1_30"]) == 2000.0
    assert float(a["d61_90"]) == 3000.0
    assert float(a["d31_60"]) == 0.0 and float(a["d90_mas"]) == 0.0
    assert d["dias_credito"] == 30


def test_estado_cuenta_vacio(client, env, auth):
    r = client.get(f"/api/v1/cobranza/estado-cuenta/{env['cli']}", headers=_h(env))
    assert r.status_code == 200, r.text
    assert float(r.json()["saldo_total"]) == 0.0
    assert r.json()["facturas"] == []
