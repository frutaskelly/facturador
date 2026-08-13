"""Nuevas funciones P6.x:
  - Factura DIRECTA (sin remisión, sin afectar inventario).
  - Cancelación de factura: sus remisiones se LIBERAN a BORRADOR (refacturables)
    y se devuelve el inventario reservado — sin importar el motivo SAT.
El PAC (Facturama) se mockea.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Almacen, Cliente, EsquemaImpuesto, LoteInventario, Membership, Producto, Role, Tenant, User,
)
import app.api.v1.facturas as facturas_mod

_PURGE = (
    "movimientos_inventario", "mermas", "lineas_remision", "lineas_factura",
    "remisiones", "facturas", "lotes_inventario", "productos",
    "esquemas_impuesto", "clientes", "almacenes",
)


class _FakePAC:
    """Stub de FacturamaClient: no llama al sandbox."""
    configured = True
    env_label = "sandbox"

    @classmethod
    def from_settings(cls, settings):
        return cls()

    def create_cfdi(self, payload):
        return {"Id": "FAKE-ID", "Complement": {"TaxStamp": {"Uuid": str(uuid.uuid4())}}}

    def download_xml(self, cfdi_id):
        return b"<xml/>"

    def buscar_cfdi(self, order_number, **kw):
        return True, None

    def cancel_cfdi(self, cfdi_id, motive, uuid_replacement=None):
        return {"status": "canceled"}


@pytest.fixture
def fake_pac(monkeypatch):
    monkeypatch.setattr(facturas_mod, "FacturamaClient", _FakePAC)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(slug=f"fdc-{suffix}", legal_name="FDC SA", rfc=f"F{suffix.upper()}D"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-fdc-{suffix}"
        u = User(email=f"fdc-{suffix}@t.test", auth_user_id=sub, full_name="admin")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)

        esq = EsquemaImpuesto(tenant_id=tenant.id, codigo="IVA16", nombre="IVA 16%", iva_tasa=Decimal("0.16"))
        cli = Cliente(tenant_id=tenant.id, codigo="CLD", legal_name="Cliente D SA", rfc="XAXX010101000",
                      regimen_fiscal="601", uso_cfdi_default="G03")
        prod = Producto(tenant_id=tenant.id, sku="FD-P", nombre="Prod FD", clave_sat="50406500",
                        unidad_sat="KGM", iva_tasa=Decimal("0.16"))
        alm = Almacen(tenant_id=tenant.id, codigo="FD-BG", nombre="Bodega FD")
        db.add_all([esq, cli, prod, alm]); db.flush()
        prod.esquema_impuesto_id = esq.id
        db.commit()
        yield {"sub": sub, "email": u.email, "tenant_id": tenant.id,
               "cli": str(cli.id), "prod": str(prod.id), "alm": str(alm.id)}
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
def auth(env):
    app.dependency_overrides[get_principal] = lambda: Principal(
        auth_user_id=env["sub"], email=env["email"], role="authenticated", claims={"sub": env["sub"]})
    yield
    app.dependency_overrides.pop(get_principal, None)


def _h(env):
    return {"X-Tenant-Id": str(env["tenant_id"])}


def _disponible(env):
    db = SessionLocal()
    try:
        row = db.query(LoteInventario).filter(
            LoteInventario.producto_id == uuid.UUID(env["prod"])).first()
        return (Decimal(row.cantidad_disponible), Decimal(row.cantidad_reservada)) if row else (None, None)
    finally:
        db.close()


# ── Factura directa ───────────────────────────────────────────────────────────
def test_factura_directa_requiere_almacen(client, env, auth):
    """La factura directa ahora exige almacen_id (de ahí sale el inventario al
    timbrar); sin él, 422."""
    body = {"cliente_id": env["cli"], "lineas": [
        {"producto_id": env["prod"], "cantidad": "10", "precio_unitario": "20"},
    ]}
    r = client.post("/api/v1/facturas/directa", headers=_h(env), json=body)
    assert r.status_code == 422, r.text


def test_factura_directa_no_mueve_inventario_al_crear(client, env, auth):
    """Al CREAR (BORRADOR) la factura directa no mueve inventario; el descuento
    ocurre al timbrar. Aquí solo se valida la creación y sus totales."""
    body = {"cliente_id": env["cli"], "almacen_id": env["alm"], "lineas": [
        {"producto_id": env["prod"], "cantidad": "10", "precio_unitario": "20"},
        {"producto_id": env["prod"], "cantidad": "5", "precio_unitario": "20"},
    ]}
    r = client.post("/api/v1/facturas/directa", headers=_h(env), json=body)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["estado"] == "BORRADOR"
    assert float(f["subtotal"]) == 300.0           # (10+5) × 20
    assert float(f["iva_trasladado"]) == 48.0       # 16 %
    assert float(f["total"]) == 348.0
    assert len(f["lineas"]) == 2
    assert f["lineas"][0]["clave_prod_serv"] == "50406500"
    # Sin timbrar no hay lote ni movimiento para esta factura
    assert _disponible(env) == (None, None)


# ── Cancelación con efecto por motivo ──────────────────────────────────────────
def _remision_facturada_timbrada(client, env):
    h = _h(env)
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod"], "almacen_id": env["alm"],
        "cantidad": "100", "costo_unitario": "5"})
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"], "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": "30", "precio_unitario": "20"}]}).json()
    client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h)
    fac = client.post("/api/v1/facturas/desde-remisiones", headers=h,
                      json={"remision_ids": [rem["id"]]}).json()
    # marcar TIMBRADA directamente (sin PAC)
    db = SessionLocal()
    try:
        from app.models import Factura
        f = db.query(Factura).filter(Factura.id == uuid.UUID(fac["id"])).one()
        f.estado = "TIMBRADA"; f.facturama_id = "FAKE123"; f.uuid = str(uuid.uuid4())
        db.commit()
    finally:
        db.close()
    return rem["id"], fac["id"]


def test_cancel_motivo_02_libera_para_refacturar(client, env, auth, fake_pac):
    rem_id, fac_id = _remision_facturada_timbrada(client, env)
    disp_antes, res_antes = _disponible(env)        # 70 disp (salida directa), 0 reservada
    assert (disp_antes, res_antes) == (Decimal("70"), Decimal("0"))

    r = client.post(f"/api/v1/facturas/{fac_id}/cancelar", headers=_h(env), json={"motivo": "02"})
    assert r.status_code == 200, r.text
    # el inventario reservado se devuelve (la remisión vuelve a BORRADOR)
    assert _disponible(env) == (Decimal("100"), Decimal("0"))
    # remisión LIBERADA a BORRADOR y refacturable (su factura quedó CANCELADA;
    # factura_id se conserva para mostrarla en la columna "Factura")
    det = client.get(f"/api/v1/remisiones/{rem_id}", headers=_h(env)).json()
    assert det["estado"] == "BORRADOR"
    assert det["factura_estado"] == "CANCELADA"
    refac = client.post("/api/v1/facturas/desde-remisiones", headers=_h(env),
                        json={"remision_ids": [rem_id]})
    assert refac.status_code == 201, refac.text


def test_cancel_simulada_sin_pac(client, env, auth, monkeypatch):
    """FACTURAMA_FAKE_CANCEL=true: cancela SIN llamar al PAC (sandbox no cancela),
    aplicando la lógica interna (motivo 03 → devuelve inventario)."""
    monkeypatch.setattr(facturas_mod.settings, "FACTURAMA_FAKE_CANCEL", True)
    rem_id, fac_id = _remision_facturada_timbrada(client, env)
    assert _disponible(env) == (Decimal("70"), Decimal("0"))
    # sin fake_pac: si intentara llamar al PAC fallaría; el flag debe saltarlo
    r = client.post(f"/api/v1/facturas/{fac_id}/cancelar", headers=_h(env), json={"motivo": "03"})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "CANCELADA"
    assert _disponible(env) == (Decimal("100"), Decimal("0"))


def test_cancel_motivo_03_devuelve_inventario(client, env, auth, fake_pac):
    rem_id, fac_id = _remision_facturada_timbrada(client, env)
    assert _disponible(env) == (Decimal("70"), Decimal("0"))

    r = client.post(f"/api/v1/facturas/{fac_id}/cancelar", headers=_h(env), json={"motivo": "03"})
    assert r.status_code == 200, r.text
    # inventario devuelto: 100 disponible, 0 reservada
    assert _disponible(env) == (Decimal("100"), Decimal("0"))
    # remisión LIBERADA a BORRADOR (refacturable)
    det = client.get(f"/api/v1/remisiones/{rem_id}", headers=_h(env)).json()
    assert det["estado"] == "BORRADOR"


def test_cancel_inventario_perdida_da_de_baja(client, env, auth, fake_pac):
    """inventario='perdida': la mercancía NO regresa (disponible igual → se
    pierde) y la remisión queda CANCELADA (no refacturable)."""
    rem_id, fac_id = _remision_facturada_timbrada(client, env)
    assert _disponible(env) == (Decimal("70"), Decimal("0"))

    r = client.post(f"/api/v1/facturas/{fac_id}/cancelar", headers=_h(env),
                    json={"motivo": "02", "inventario": "perdida"})
    assert r.status_code == 200, r.text
    # NO regresa a disponible: 70 disp (los 30 se perdieron)
    assert _disponible(env) == (Decimal("70"), Decimal("0"))
    # remisión CANCELADA (mercancía perdida, no refacturable)
    det = client.get(f"/api/v1/remisiones/{rem_id}", headers=_h(env)).json()
    assert det["estado"] == "CANCELADA"


# ── Timbrado: stock/sobregiro (#4) y bitácora de intentos (#1) ────────────────
def _factura_directa(client, env, cantidad="10"):
    body = {"cliente_id": env["cli"], "almacen_id": env["alm"], "lineas": [
        {"producto_id": env["prod"], "cantidad": cantidad, "precio_unitario": "20"}]}
    r = client.post("/api/v1/facturas/directa", headers=_h(env), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _cargar_stock(client, env, cantidad="50"):
    client.post("/api/v1/inventario/movimientos", headers=_h(env), json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod"], "almacen_id": env["alm"],
        "cantidad": cantidad, "costo_unitario": "5"})


def test_timbrar_directa_sin_stock_frena_y_sobregiro_autoriza(client, env, auth, fake_pac):
    """#4: sin existencia el timbrado frena con 422; con permitir_negativos
    timbra y deja el inventario en negativo (política unificada con remisiones)."""
    f = _factura_directa(client, env, "10")     # sin ENTRADA_COMPRA: no hay stock
    r = client.post(f"/api/v1/facturas/{f['id']}/timbrar", headers=_h(env))
    assert r.status_code == 422, r.text
    assert "insuficiente" in r.json()["detail"].lower()

    r2 = client.post(f"/api/v1/facturas/{f['id']}/timbrar", headers=_h(env),
                     json={"permitir_negativos": True})
    assert r2.status_code == 200, r2.text
    assert r2.json()["estado"] == "TIMBRADA"
    disp, _ = _disponible(env)
    assert disp == Decimal("-10")


def test_timbrar_deja_bitacora_timbrada(client, env, auth, fake_pac):
    """#1: cada timbrado exitoso deja su intento en TIMBRADA."""
    _cargar_stock(client, env)
    f = _factura_directa(client, env, "10")
    r = client.post(f"/api/v1/facturas/{f['id']}/timbrar", headers=_h(env))
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        from app.models import TimbradoIntento
        intentos = db.query(TimbradoIntento).filter(
            TimbradoIntento.factura_id == uuid.UUID(f["id"])).all()
        assert len(intentos) == 1 and intentos[0].estado == "TIMBRADA"
    finally:
        db.close()


def _insertar_intento(env, factura_id, *, viejo=False):
    from sqlalchemy import text as sqltext
    from app.models import TimbradoIntento
    db = SessionLocal()
    try:
        i = TimbradoIntento(tenant_id=uuid.UUID(str(env["tenant_id"])),
                            factura_id=uuid.UUID(factura_id), estado="PENDIENTE")
        db.add(i); db.flush()
        if viejo:
            db.execute(sqltext(
                "UPDATE timbrado_intentos SET created_at = now() - interval '10 minutes' "
                "WHERE id = :iid"), {"iid": str(i.id)})
        db.commit()
        return str(i.id)
    finally:
        db.close()


def test_timbrar_pendiente_fresco_bloquea_409(client, env, auth, fake_pac):
    """#1: un intento PENDIENTE fresco = timbrado en vuelo → 409, no doble CFDI."""
    _cargar_stock(client, env)
    f = _factura_directa(client, env, "10")
    _insertar_intento(env, f["id"])
    r = client.post(f"/api/v1/facturas/{f['id']}/timbrar", headers=_h(env))
    assert r.status_code == 409, r.text
    assert "en curso" in r.json()["detail"].lower()


def test_timbrar_pendiente_viejo_reconcilia_y_adopta(client, env, auth, monkeypatch):
    """#1: intento PENDIENTE viejo + el CFDI SÍ existe en Facturama → se adopta
    (jamás se re-timbra: create_cfdi explota si se llamara)."""
    uuid_sat = str(uuid.uuid4())

    class _PACReconcilia(_FakePAC):
        def create_cfdi(self, payload):
            raise AssertionError("no debe re-timbrar: el CFDI ya existía")

        def buscar_cfdi(self, order_number, **kw):
            return True, {"Id": "YA-EXISTIA", "OrderNumber": order_number,
                          "Complement": {"TaxStamp": {"Uuid": uuid_sat}}}

    monkeypatch.setattr(facturas_mod, "FacturamaClient", _PACReconcilia)
    _cargar_stock(client, env)
    f = _factura_directa(client, env, "10")
    _insertar_intento(env, f["id"], viejo=True)
    r = client.post(f"/api/v1/facturas/{f['id']}/timbrar", headers=_h(env))
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "TIMBRADA"
    assert r.json()["uuid"] == uuid_sat
    db = SessionLocal()
    try:
        from app.models import Factura
        f_db = db.query(Factura).filter(Factura.id == uuid.UUID(f["id"])).one()
        assert f_db.facturama_id == "YA-EXISTIA"    # adoptado, no re-timbrado
    finally:
        db.close()


def test_timbrar_pendiente_viejo_sin_cfdi_marca_error_y_timbra(client, env, auth, fake_pac):
    """#1: intento viejo + el CFDI NO existe en Facturama → el intento muerto se
    marca ERROR y el timbrado procede normal."""
    _cargar_stock(client, env)
    f = _factura_directa(client, env, "10")
    iid = _insertar_intento(env, f["id"], viejo=True)
    r = client.post(f"/api/v1/facturas/{f['id']}/timbrar", headers=_h(env))
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        from app.models import TimbradoIntento
        viejo = db.query(TimbradoIntento).filter(TimbradoIntento.id == uuid.UUID(iid)).one()
        assert viejo.estado == "ERROR"
    finally:
        db.close()


def test_series_folio_no_rebobina_bajo_lo_emitido(client, env, auth, fake_pac):
    """#7: `folio_actual` es editable pero nunca por debajo del folio más alto
    ya emitido con esa serie (evita folios duplicados)."""
    s = client.post("/api/v1/series", headers=_h(env), json={
        "codigo": "ZZ", "tipo_documento": "FACTURA", "nombre": "Serie ZZ"})
    assert s.status_code == 201, s.text
    serie = s.json()

    _cargar_stock(client, env)
    body = {"cliente_id": env["cli"], "almacen_id": env["alm"], "serie_id": serie["id"],
            "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "20"}]}
    f = client.post("/api/v1/facturas/directa", headers=_h(env), json=body).json()
    assert f["serie"] == "ZZ" and int(f["folio"]) == 1

    # Por debajo de lo emitido → 422 con el piso en el mensaje.
    r = client.patch(f"/api/v1/series/{serie['id']}", headers=_h(env), json={"folio_actual": 0})
    assert r.status_code == 422, r.text
    assert "ZZ1" in r.json()["detail"]
    # Hacia adelante (o corregir por encima del piso) → OK.
    r2 = client.patch(f"/api/v1/series/{serie['id']}", headers=_h(env), json={"folio_actual": 5})
    assert r2.status_code == 200, r2.text
    assert r2.json()["folio_actual"] == 5


def test_remision_con_impuestos_y_preview_coinciden(client, env, auth):
    """Decisión 2026-07-29: la remisión guarda IVA/IEPS (mismo cerebro fiscal
    que facturas) y el preview del servidor coincide con lo que se guarda."""
    body = {"cliente_facturacion_id": env["cli"], "almacen_id": env["alm"],
            "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": "10",
                        "precio_unitario": "20"}]}
    rem = client.post("/api/v1/remisiones", headers=_h(env), json=body).json()
    # producto con esquema IVA 16%: subtotal 200 → iva 32 → total 232
    assert float(rem["subtotal"]) == 200.0
    assert float(rem["iva"]) == 32.0
    assert float(rem["total"]) == 232.0
    assert float(rem["lineas"][0]["iva_importe"]) == 32.0

    prev = client.post("/api/v1/remisiones/preview-totales", headers=_h(env), json={
        "lineas": [{"producto_id": env["prod"], "cantidad": "10", "precio_unitario": "20"}]})
    assert prev.status_code == 200, prev.text
    p = prev.json()
    assert float(p["subtotal"]) == 200.0 and float(p["iva"]) == 32.0 and float(p["total"]) == 232.0


# ── Lote: PDF y correo (Objetivo 2) ──────────────────────────────────────────
def test_facturas_pdf_lote(client, env, auth):
    f1 = _factura_directa(client, env, "1")
    f2 = _factura_directa(client, env, "2")
    r = client.get(f"/api/v1/facturas/pdf?ids={f1['id']},{f2['id']}", headers=_h(env))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"
    assert client.get("/api/v1/facturas/pdf?ids=,", headers=_h(env)).status_code == 422


def test_enviar_lote_rechaza_no_timbradas(client, env, auth):
    f = _factura_directa(client, env, "1")   # BORRADOR
    r = client.post("/api/v1/facturas/enviar-lote", headers=_h(env), json={"ids": [f["id"]]})
    assert r.status_code == 409, r.text
    assert f"{f['serie']}{f['folio']}" in r.json()["detail"]


# ── Devoluciones desde remisión (decisión 2026-07-29: ajustan a lo neto) ─────
def _remision_confirmada(client, env, cantidad="30"):
    h = _h(env)
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod"], "almacen_id": env["alm"],
        "cantidad": "100", "costo_unitario": "5"})
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"], "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": cantidad,
                    "precio_unitario": "20"}]}).json()
    assert client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h).status_code == 200
    return rem


def test_devolucion_parcial_ajusta_neto_e_inventario(client, env, auth):
    rem = _remision_confirmada(client, env, "30")     # sale 30 → disponible 70
    linea_id = rem["lineas"][0]["id"]
    r = client.post(f"/api/v1/remisiones/{rem['id']}/devolucion", headers=_h(env), json={
        "lineas": [{"linea_id": linea_id, "cantidad": "10"}], "motivo": "Rechazo del cliente"})
    assert r.status_code == 200, r.text
    d = r.json()
    # Neto: 20 × $20 = 400 + IVA 64 = 464 (mismo cerebro fiscal)
    assert float(d["lineas"][0]["cantidad_solicitada"]) == 20.0
    assert float(d["subtotal"]) == 400.0
    assert float(d["iva"]) == 64.0
    assert float(d["total"]) == 464.0
    assert len(d["devoluciones"]) == 1
    assert float(d["devoluciones"][0]["lineas"][0]["cantidad_base"]) == 10.0
    disp, _ = _disponible(env)
    assert disp == Decimal("80")                       # 70 + 10 devueltos
    movs = client.get("/api/v1/inventario/movimientos", headers=_h(env),
                      params={"tipo": "ENTRADA_DEVOLUCION"}).json()
    assert movs["total"] >= 1


def test_devolucion_total_y_factura_sin_lineas(client, env, auth):
    rem = _remision_confirmada(client, env, "30")
    linea_id = rem["lineas"][0]["id"]
    r = client.post(f"/api/v1/remisiones/{rem['id']}/devolucion", headers=_h(env), json={
        "lineas": [{"linea_id": linea_id, "cantidad": "30"}]})
    assert r.status_code == 200, r.text
    assert float(r.json()["total"]) == 0.0
    disp, _ = _disponible(env)
    assert disp == Decimal("100")                      # todo regresó
    # Facturar una remisión totalmente devuelta → 422 (nada que facturar)
    f = client.post("/api/v1/facturas/desde-remisiones", headers=_h(env),
                    json={"remision_ids": [rem["id"]]})
    assert f.status_code == 422, f.text


def test_devolucion_valida_estado_y_cantidad(client, env, auth):
    h = _h(env)
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"], "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": "5",
                    "precio_unitario": "20"}]}).json()
    linea_id = rem["lineas"][0]["id"]
    # BORRADOR → 409 (no ha salido mercancía)
    r = client.post(f"/api/v1/remisiones/{rem['id']}/devolucion", headers=h, json={
        "lineas": [{"linea_id": linea_id, "cantidad": "1"}]})
    assert r.status_code == 409
    # CONFIRMADA pero devolver más de lo entregado → 422
    rem2 = _remision_confirmada(client, env, "5")
    r2 = client.post(f"/api/v1/remisiones/{rem2['id']}/devolucion", headers=h, json={
        "lineas": [{"linea_id": rem2["lineas"][0]["id"], "cantidad": "6"}]})
    assert r2.status_code == 422
