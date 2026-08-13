"""Facturas (Fase 6, P6.1): motor fiscal (unitario) + generar desde remisión.

El motor (calcular_linea/totales) se prueba puro; la generación desde remisión
prueba el cruce, los totales, el marcado de remisión facturada y los guards.
"""
import base64
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Almacen, Cliente, EsquemaImpuesto, Membership, Producto, Role, Tenant, User,
)
from app.services.fiscal import calcular_linea, totales


# ── motor fiscal (puro) ──────────────────────────────────────────────────────
def test_iva_16():
    r = calcular_linea(Decimal("100"), iva_tasa=Decimal("0.16"))
    assert r["iva_importe"] == Decimal("16.00")
    assert r["ieps_importe"] == Decimal("0.00")


def test_iva_0_alimento():
    r = calcular_linea(Decimal("100"), iva_tasa=Decimal("0"))
    assert r["iva_importe"] == Decimal("0.00")


def test_ieps_cuota_refresco_antes_de_iva():
    # 12 piezas × 0.6 L = 7.2 L; cuota 3.0818 → IEPS 22.19; IVA 16% sobre (180+22.19)
    r = calcular_linea(Decimal("180"), iva_tasa=Decimal("0.16"), tipo_ieps="CUOTA",
                       ieps_cuota=Decimal("3.0818"), litros_totales=Decimal("7.2"))
    assert r["ieps_importe"] == Decimal("22.19")
    assert r["iva_importe"] == Decimal("32.35")


def test_ieps_tasa_botana():
    r = calcular_linea(Decimal("100"), iva_tasa=Decimal("0"), tipo_ieps="TASA", ieps_tasa=Decimal("0.08"))
    assert r["ieps_importe"] == Decimal("8.00")
    assert r["iva_importe"] == Decimal("0.00")


def test_totales_suma():
    t = totales([
        calcular_linea(Decimal("100"), iva_tasa=Decimal("0.16")),
        calcular_linea(Decimal("50"), iva_tasa=Decimal("0")),
    ])
    assert t["subtotal"] == Decimal("150.00")
    assert t["iva_trasladado"] == Decimal("16.00")
    assert t["total"] == Decimal("166.00")


# ── generar factura desde remisión (integración) ─────────────────────────────
_PURGE = (
    "movimientos_inventario", "mermas", "lineas_remision", "lineas_factura",
    "remisiones", "facturas", "lotes_inventario", "productos",
    "esquemas_impuesto", "clientes", "almacenes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(slug=f"fac-{suffix}", legal_name="Fac SA", rfc=f"F{suffix.upper()}X"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        tomador_role = db.query(Role).filter(Role.nombre == "TOMADOR", Role.es_preset.is_(True)).one()

        def _user(role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        admin = _user(admin_role, "admin")
        tomador = _user(tomador_role, "tomador")

        esq = EsquemaImpuesto(tenant_id=tenant.id, codigo="IVA16", nombre="IVA 16%", iva_tasa=Decimal("0.16"))
        cli = Cliente(tenant_id=tenant.id, codigo="CLF", legal_name="Cliente Fac SA", rfc="XAXX010101000",
                      regimen_fiscal="601", uso_cfdi_default="G03")
        prod = Producto(tenant_id=tenant.id, sku="FAC-P", nombre="Prod Fac", clave_sat="01010101",
                        unidad_sat="H87", iva_tasa=Decimal("0.16"))
        alm = Almacen(tenant_id=tenant.id, codigo="FAC-BG", nombre="Bodega Fac")
        db.add_all([esq, cli, prod, alm]); db.flush()
        prod.esquema_impuesto_id = esq.id
        db.commit()
        yield {"admin": admin, "tomador": tomador, "cli_id": str(cli.id),
               "prod_id": str(prod.id), "alm_id": str(alm.id)}
    finally:
        for table in _PURGE:
            for tid in created["tenants"]:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tid})
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit(); db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"], email=user["email"], role="authenticated", claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _remision_confirmada(client, h, env, qty="10", precio="20", cli_id=None):
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod_id"], "almacen_id": env["alm_id"],
        "cantidad": "1000", "costo_unitario": "5"})
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": cli_id or env["cli_id"], "almacen_id": env["alm_id"],
        "lineas": [{"producto_id": env["prod_id"], "cantidad_solicitada": qty, "precio_unitario": precio}]}).json()
    client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h)
    return rem["id"]


def _remision_borrador(client, h, env, qty="10", precio="20", con_stock=True):
    """Remisión en BORRADOR (sin confirmar). Con `con_stock` siembra existencia."""
    if con_stock:
        client.post("/api/v1/inventario/movimientos", headers=h, json={
            "tipo": "ENTRADA_COMPRA", "producto_id": env["prod_id"], "almacen_id": env["alm_id"],
            "cantidad": "1000", "costo_unitario": "5"})
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli_id"], "almacen_id": env["alm_id"],
        "lineas": [{"producto_id": env["prod_id"], "cantidad_solicitada": qty, "precio_unitario": precio}]}).json()
    return rem["id"]


def test_factura_desde_remision(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_confirmada(client, h, env, qty="10", precio="20")  # 200 + IVA 16%

    f = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]})
    assert f.status_code == 201, f.text
    fac = f.json()
    assert fac["estado"] == "BORRADOR"
    assert float(fac["subtotal"]) == 200.0
    assert float(fac["iva_trasladado"]) == 32.0
    assert float(fac["total"]) == 232.0
    assert fac["folio"] == 1 and len(fac["lineas"]) == 1
    assert fac["lineas"][0]["clave_prod_serv"] == "01010101"

    # re-facturar la misma remisión → 409 (ya facturada)
    assert client.post("/api/v1/facturas/desde-remisiones", headers=h,
                       json={"remision_ids": [rem_id]}).status_code == 409


def test_factura_desde_borrador_autoconfirma(client, env, auth_as):
    """Facturar un BORRADOR lo auto-confirma (reserva inventario) y lo deja FACTURADA."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_borrador(client, h, env, qty="10", precio="20")
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()["estado"] == "BORRADOR"

    f = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]})
    assert f.status_code == 201, f.text
    assert f.json()["estado"] == "BORRADOR"          # la factura nace como borrador
    assert float(f.json()["total"]) == 232.0

    # la remisión pasó a FACTURADA
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()["estado"] == "FACTURADA"

    # re-facturar → 409 (ya facturada), no 422 por el estado
    assert client.post("/api/v1/facturas/desde-remisiones", headers=h,
                       json={"remision_ids": [rem_id]}).status_code == 409


def test_factura_desde_borrador_sin_stock_sobregiro(client, env, auth_as):
    """Sin existencia el auto-confirm falla 422; con permitir_negativos pasa (sobregiro)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_borrador(client, h, env, qty="10", precio="20", con_stock=False)

    r = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]})
    assert r.status_code == 422, r.text
    # tras el 422 la remisión sigue en BORRADOR (rollback)
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()["estado"] == "BORRADOR"

    ok = client.post("/api/v1/facturas/desde-remisiones", headers=h,
                     json={"remision_ids": [rem_id], "permitir_negativos": True})
    assert ok.status_code == 201, ok.text
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()["estado"] == "FACTURADA"


def test_cruce_dos_remisiones(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r1 = _remision_confirmada(client, h, env, qty="5", precio="20")   # 100
    r2 = _remision_confirmada(client, h, env, qty="3", precio="20")   # 60
    f = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [r1, r2]})
    assert f.status_code == 201, f.text
    fac = f.json()
    assert float(fac["subtotal"]) == 160.0 and len(fac["lineas"]) == 2
    assert float(fac["total"]) == 185.6   # 160 + 16% = 185.60


def test_guard_remision_borrador(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli_id"], "almacen_id": env["alm_id"],
        "lineas": [{"producto_id": env["prod_id"], "cantidad_solicitada": "1", "precio_unitario": "10"}]}).json()
    # sin confirmar → 422
    assert client.post("/api/v1/facturas/desde-remisiones", headers=h,
                       json={"remision_ids": [rem["id"]]}).status_code == 422


def test_tomador_cannot_invoice(client, env, auth_as):
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    assert client.get("/api/v1/facturas", headers=h).status_code == 403
    assert client.post("/api/v1/facturas/desde-remisiones", headers=h,
                       json={"remision_ids": [str(uuid.uuid4())]}).status_code == 403


def test_remision_expone_factura_en_lista(client, env, auth_as):
    """Tras facturar, la remisión expone el folio y estado de su factura (columna)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_confirmada(client, h, env, qty="2", precio="20")
    fac = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]}).json()
    lista = client.get("/api/v1/remisiones?limit=200", headers=h).json()["items"]
    row = next(x for x in lista if x["id"] == rem_id)
    assert row["factura_folio"] == f"{fac['serie']}{fac['folio']}"
    assert row["factura_estado"] == "BORRADOR"


def test_descartar_factura_borrador_libera_remisiones(client, env, auth_as):
    """Descartar una factura en BORRADOR regresa sus remisiones a CONFIRMADA y
    permite refacturarlas (rompe el deadlock de la factura nunca timbrada)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_confirmada(client, h, env, qty="4", precio="20")
    fac = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]}).json()
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()["estado"] == "FACTURADA"

    # descartar el borrador
    d = client.delete(f"/api/v1/facturas/{fac['id']}", headers=h)
    assert d.status_code == 204, d.text
    # la factura ya no existe
    assert client.get(f"/api/v1/facturas/{fac['id']}", headers=h).status_code == 404
    # la remisión volvió a CONFIRMADA y perdió el vínculo a la factura
    row = client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()
    assert row["estado"] == "CONFIRMADA"
    assert row.get("factura_id") in (None, "")
    # y se puede refacturar
    re = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]})
    assert re.status_code == 201, re.text


def test_descartar_solo_borrador(client, env, auth_as):
    """Solo se descarta una factura en BORRADOR; inexistente → 404."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert client.delete(f"/api/v1/facturas/{uuid.uuid4()}", headers=h).status_code == 404


def test_remision_facturada_no_se_cancela_ni_elimina(client, env, auth_as):
    """Una remisión FACTURADA no se puede cancelar ni eliminar directo (409):
    debe pasar por la cancelación de su factura (que libera el inventario)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_confirmada(client, h, env, qty="4", precio="20")
    client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]})
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()["estado"] == "FACTURADA"

    assert client.post(f"/api/v1/remisiones/{rem_id}/cancelar", headers=h).status_code == 409
    assert client.delete(f"/api/v1/remisiones/{rem_id}", headers=h).status_code == 409
    # sigue FACTURADA (no se tocó)
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=h).json()["estado"] == "FACTURADA"


def test_pdf_propio_es_pdf(client, env, auth_as):
    """El PDF de la factura lo genera la app (reportlab): responde un %PDF válido."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_confirmada(client, h, env, qty="3", precio="20")
    fac = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]}).json()
    r = client.get(f"/api/v1/facturas/{fac['id']}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


# PNG 1x1 transparente, para probar el ciclo del logo.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def test_empresa_logo_ciclo(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert client.get("/api/v1/empresa", headers=h).json()["has_logo"] is False

    up = client.post("/api/v1/empresa/logo", headers=h,
                     files={"logo": ("logo.png", _PNG_1x1, "image/png")})
    assert up.status_code == 200, up.text
    assert up.json()["has_logo"] is True

    got = client.get("/api/v1/empresa/logo", headers=h)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert got.content == _PNG_1x1

    bad = client.post("/api/v1/empresa/logo", headers=h,
                      files={"logo": ("x.txt", b"hola", "text/plain")})
    assert bad.status_code == 422

    dele = client.delete("/api/v1/empresa/logo", headers=h)
    assert dele.status_code == 200 and dele.json()["has_logo"] is False
    assert client.get("/api/v1/empresa/logo", headers=h).status_code == 404


# ── Sustitución CFDI (refacturación, relación 04) ────────────────────────────
def _marcar_timbrada(fid):
    """Marca una factura como TIMBRADA con un UUID del SAT (sin llamar al PAC)."""
    from app.models import Factura
    db = SessionLocal()
    try:
        f = db.query(Factura).filter(Factura.id == uuid.UUID(fid)).one()
        f.estado = "TIMBRADA"
        f.uuid = str(uuid.uuid4())
        f.facturama_id = "FAC-" + uuid.uuid4().hex[:8]
        db.commit()
        return f.uuid
    finally:
        db.close()


def _cliente_normal(env):
    """Cliente con RFC NORMAL (no público en general): las globales no se sustituyen."""
    from app.models import Cliente
    db = SessionLocal()
    try:
        c = Cliente(tenant_id=uuid.UUID(str(env["admin"]["tenant_id"])),
                    codigo="CLN" + uuid.uuid4().hex[:4], legal_name="Cliente Normal SA",
                    rfc="EKU9003173C9", regimen_fiscal="601", uso_cfdi_default="G03")
        db.add(c); db.commit()
        return str(c.id)
    finally:
        db.close()


def _vieja_timbrada(client, h, env, cli_id):
    """Remisión confirmada → factura → marcada TIMBRADA. Devuelve (vieja, rem_id).
    El UUID del SAT queda en vieja["uuid"]."""
    rem_id = _remision_confirmada(client, h, env, cli_id=cli_id)
    vieja = client.post("/api/v1/facturas/desde-remisiones", headers=h,
                        json={"remision_ids": [rem_id]}).json()
    vieja["uuid"] = _marcar_timbrada(vieja["id"])
    return vieja, rem_id


def test_sustituir_crea_copia_ligada(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    vieja, _ = _vieja_timbrada(client, h, env, _cliente_normal(env))

    r = client.post(f"/api/v1/facturas/{vieja['id']}/sustituir", headers=h, json={"forma_pago": "01"})
    assert r.status_code == 201, r.text
    nueva = r.json()
    assert nueva["estado"] == "BORRADOR"
    assert nueva["sustituye_a_factura_id"] == vieja["id"]        # nueva → vieja
    assert nueva["forma_pago"] == "01"                           # corrección aplicada
    assert float(nueva["total"]) == float(vieja["total"])        # copia verbatim
    assert nueva["folio"] != vieja["folio"]
    assert len(nueva["lineas"]) == len(vieja["lineas"])

    # una segunda sustituta viva para la misma vieja → 409
    assert client.post(f"/api/v1/facturas/{vieja['id']}/sustituir", headers=h, json={}).status_code == 409


def test_sustituir_solo_timbrada(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_confirmada(client, h, env, cli_id=_cliente_normal(env))
    vieja = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]}).json()
    # sigue en BORRADOR (no timbrada) → no se puede sustituir
    assert client.post(f"/api/v1/facturas/{vieja['id']}/sustituir", headers=h, json={}).status_code == 409


def test_sustituir_global_bloqueado(client, env, auth_as):
    # El cliente del fixture tiene RFC XAXX010101000 (público en general): global.
    auth_as(env["admin"]); h = _hdr(env["admin"])
    vieja, _ = _vieja_timbrada(client, h, env, env["cli_id"])
    r = client.post(f"/api/v1/facturas/{vieja['id']}/sustituir", headers=h, json={})
    assert r.status_code == 409 and "global" in r.json()["detail"].lower()


def test_build_payload_relations_04(client, env, auth_as):
    from app.models import Factura
    from app.services.cfdi import build_payload
    auth_as(env["admin"]); h = _hdr(env["admin"])
    vieja, _ = _vieja_timbrada(client, h, env, _cliente_normal(env))
    uuid_vieja = vieja["uuid"]
    nueva = client.post(f"/api/v1/facturas/{vieja['id']}/sustituir", headers=h, json={}).json()

    db = SessionLocal()
    try:
        f = db.query(Factura).filter(Factura.id == uuid.UUID(nueva["id"])).one()
        payload = build_payload(db, f)
    finally:
        db.close()
    # La sustituta reporta al SAT el nodo Relations 04 → UUID de la vieja.
    assert payload["Relations"] == {"Type": "04", "Cfdis": [{"Uuid": uuid_vieja}]}


def test_factura_normal_sin_relations(client, env, auth_as):
    from app.models import Factura
    from app.services.cfdi import build_payload
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_id = _remision_confirmada(client, h, env, cli_id=_cliente_normal(env))
    fac = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem_id]}).json()
    db = SessionLocal()
    try:
        f = db.query(Factura).filter(Factura.id == uuid.UUID(fac["id"])).one()
        payload = build_payload(db, f)
    finally:
        db.close()
    assert "Relations" not in payload   # una factura normal no lleva relación


def test_cancelar_01_autollena_neutral_y_reapunta(client, env, auth_as, monkeypatch):
    import app.core.config as cfg
    monkeypatch.setattr(cfg.settings, "FACTURAMA_FAKE_CANCEL", True)
    from app.models import Remision
    auth_as(env["admin"]); h = _hdr(env["admin"])
    vieja, rem_id = _vieja_timbrada(client, h, env, _cliente_normal(env))
    nueva = client.post(f"/api/v1/facturas/{vieja['id']}/sustituir", headers=h, json={}).json()
    uuid_nueva = _marcar_timbrada(nueva["id"])

    # cancelar la vieja con motivo 01 SIN pasar uuid → se autollena con la sustituta
    c = client.post(f"/api/v1/facturas/{vieja['id']}/cancelar", headers=h, json={"motivo": "01"})
    assert c.status_code == 200, c.text
    assert c.json()["estado"] == "CANCELADA"
    assert c.json()["motivo_cancelacion"] == "01"
    assert c.json()["uuid_sustitucion"] == uuid_nueva           # vieja → nueva

    db = SessionLocal()
    try:
        rem = db.query(Remision).filter(Remision.id == uuid.UUID(rem_id)).one()
        assert rem.estado == "FACTURADA"                        # inventario-neutral
        assert str(rem.factura_id) == nueva["id"]               # re-apuntada a la sustituta
    finally:
        db.close()


def test_cancelar_02_bloqueado_con_sustituta(client, env, auth_as, monkeypatch):
    import app.core.config as cfg
    monkeypatch.setattr(cfg.settings, "FACTURAMA_FAKE_CANCEL", True)
    auth_as(env["admin"]); h = _hdr(env["admin"])
    vieja, _ = _vieja_timbrada(client, h, env, _cliente_normal(env))
    nueva = client.post(f"/api/v1/facturas/{vieja['id']}/sustituir", headers=h, json={}).json()
    _marcar_timbrada(nueva["id"])
    # con sustituta timbrada, cancelar la vieja con motivo 02 → 409 (debe ser 01)
    r = client.post(f"/api/v1/facturas/{vieja['id']}/cancelar", headers=h, json={"motivo": "02"})
    assert r.status_code == 409 and "sustituta" in r.json()["detail"].lower()


def test_cancelar_01_sin_sustituta_422(client, env, auth_as, monkeypatch):
    import app.core.config as cfg
    monkeypatch.setattr(cfg.settings, "FACTURAMA_FAKE_CANCEL", True)
    auth_as(env["admin"]); h = _hdr(env["admin"])
    vieja, _ = _vieja_timbrada(client, h, env, _cliente_normal(env))
    # motivo 01 sin sustituta timbrada ni uuid → 422
    assert client.post(f"/api/v1/facturas/{vieja['id']}/cancelar", headers=h,
                       json={"motivo": "01"}).status_code == 422
