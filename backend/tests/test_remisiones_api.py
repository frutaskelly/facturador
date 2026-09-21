"""Remisiones end-to-end (Phase 4e): draft creation + folios, confirm reserving
stock, cancel releasing it, the almacén requirement, lifecycle guards, RBAC,
and isolation."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Almacen, Cliente, Membership, Producto, Role, Tenant, User

_PURGE = (
    "movimientos_inventario", "mermas", "lineas_remision", "remisiones",
    "cliente_sucursales", "sucursales",
    "lotes_inventario", "precios", "listas_precios", "productos", "almacenes", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        def _tenant(s):
            t = Tenant(slug=f"rem-{s}-{suffix}", legal_name=f"Rem {s} SA",
                       rfc=f"R{s.upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
            db.add(t); db.flush(); created["tenants"].append(t.id); return t

        tenant_a, tenant_b = _tenant("a"), _tenant("b")
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        tomador_role = db.query(Role).filter(Role.nombre == "TOMADOR", Role.es_preset.is_(True)).one()

        def _user(tenant, role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        admin_a = _user(tenant_a, admin_role, "admin-a")
        tomador_a = _user(tenant_a, tomador_role, "tomador-a")
        admin_b = _user(tenant_b, admin_role, "admin-b")

        cli = Cliente(tenant_id=tenant_a.id, codigo="CL1", legal_name="Cliente 1", rfc="XAXX010101000")
        prod = Producto(tenant_id=tenant_a.id, sku="R-P", nombre="Prod R", clave_sat="01010101", unidad_sat="KGM")
        prod_bulto = Producto(
            tenant_id=tenant_a.id, sku="R-PB", nombre="Prod Bulto R",
            clave_sat="50300000", unidad_sat="KGM",
            unidad_base="KILO", presentaciones={"KILO": 1, "BULTO": 20},
        )
        alm = Almacen(tenant_id=tenant_a.id, codigo="R-BG", nombre="Bodega R")
        db.add_all([cli, prod, alm, prod_bulto]); db.flush()
        db.commit()
        yield {"admin_a": admin_a, "tomador_a": tomador_a, "admin_b": admin_b,
               "cli_a": str(cli.id), "prod_a": str(prod.id), "alm_a": str(alm.id),
               "prod_bulto_a": str(prod_bulto.id)}
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
            auth_user_id=user["sub"], email=user["email"], role="authenticated",
            claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _load_stock(client, h, env, qty, costo):
    return client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod_a"], "almacen_id": env["alm_a"],
        "cantidad": qty, "costo_unitario": costo})


def _create_rem(client, h, env, qty, precio, *, almacen=True):
    body = {"cliente_facturacion_id": env["cli_a"],
            "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": qty, "precio_unitario": precio}]}
    if almacen:
        body["almacen_id"] = env["alm_a"]
    return client.post("/api/v1/remisiones", headers=h, json=body)


def _disp(client, h, env):
    rows = client.get("/api/v1/inventario/existencias", headers=h, params={"producto_id": env["prod_a"]}).json()
    return next((r for r in rows if r["almacen_id"] == env["alm_a"]), None)


def test_create_draft_and_folio(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    r = _create_rem(client, h, env, "10", "5")
    assert r.status_code == 201, r.text
    rem = r.json()
    assert rem["folio_interno"] == "R1"
    assert rem["estado"] == "BORRADOR"
    assert float(rem["subtotal"]) == 50.0
    assert float(rem["total"]) == 50.0
    assert len(rem["lineas"]) == 1 and rem["lineas"][0]["numero_linea"] == 1
    assert _create_rem(client, h, env, "1", "1").json()["folio_interno"] == "R2"


def test_list_ordena_folio_como_numero(client, env, auth_as):
    """Como texto "R9" > "R72" y el listado salía barajado (9, 72, 37): a fecha
    igual, el número del folio se ordena como número (72, 37, 9)."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    ids = [_create_rem(client, h, env, "1", "1").json()["id"] for _ in range(3)]
    db = SessionLocal()
    try:
        for rid, folio in zip(ids, ["QZX9", "QZX72", "QZX37"]):
            db.execute(text("UPDATE remisiones SET folio_interno = :f WHERE id = :id"),
                       {"f": folio, "id": rid})
        db.commit()
    finally:
        db.close()
    data = client.get("/api/v1/remisiones", headers=h,
                      params={"cliente_id": env["cli_a"], "limit": 200}).json()
    folios = [r["folio_interno"] for r in data["items"] if r["folio_interno"].startswith("QZX")]
    assert folios == ["QZX72", "QZX37", "QZX9"]


def test_confirm_descuenta_then_cancel_restituye(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _load_stock(client, h, env, "100", "4")
    rem_id = _create_rem(client, h, env, "30", "5").json()["id"]

    c = client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h)
    assert c.status_code == 200, c.text
    assert c.json()["estado"] == "CONFIRMADA"
    row = _disp(client, h, env)
    assert float(row["disponible"]) == 70.0
    assert float(row["reservada"]) == 0.0   # salida directa: sin cubeta de apartado
    movs = client.get("/api/v1/inventario/movimientos", headers=h, params={"tipo": "SALIDA_REMISION"}).json()
    assert movs["total"] >= 1

    x = client.post(f"/api/v1/remisiones/{rem_id}/cancelar", headers=h)
    assert x.status_code == 200 and x.json()["estado"] == "CANCELADA"
    row2 = _disp(client, h, env)
    assert float(row2["disponible"]) == 100.0
    assert float(row2["reservada"]) == 0.0


def test_confirmar_conserva_sucursal_y_patch_la_asigna(client, env, auth_as):
    """Ticket 86bbykyu2: confirmar NO toca la sucursal de la remisión, y un
    PATCH de solo `sucursal_id` (la acción rápida de la celda) la quita o la
    asigna sin tocar nada más — también en una CONFIRMADA. Una plaza sin
    vínculo con el cliente se rechaza."""
    from app.models import ClienteSucursal, Sucursal

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    tid = env["admin_a"]["tenant_id"]
    db = SessionLocal()
    try:
        suc = Sucursal(tenant_id=tid, nombre="Plaza Conserva")
        ajena = Sucursal(tenant_id=tid, nombre="Plaza Sin Vínculo")
        db.add_all([suc, ajena]); db.flush()
        db.add(ClienteSucursal(tenant_id=tid, cliente_id=env["cli_a"], sucursal_id=suc.id))
        db.commit()
        suc_id, ajena_id = str(suc.id), str(ajena.id)
    finally:
        db.close()

    _load_stock(client, h, env, "50", "4")
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
        "sucursal_id": suc_id,
        "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "5", "precio_unitario": "10"}],
    }).json()
    assert rem["sucursal_id"] == suc_id

    c = client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h)
    assert c.status_code == 200, c.text
    assert c.json()["estado"] == "CONFIRMADA"
    assert c.json()["sucursal_id"] == suc_id      # confirmar la conserva

    # Solo-sucursal en una CONFIRMADA: quitar, rechazar la ajena, reasignar.
    p = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={"sucursal_id": None})
    assert p.status_code == 200, p.text
    assert p.json()["sucursal_id"] is None
    bad = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={"sucursal_id": ajena_id})
    assert bad.status_code == 422
    p2 = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={"sucursal_id": suc_id})
    assert p2.status_code == 200, p2.text
    assert p2.json()["sucursal_id"] == suc_id
    # El inventario reservado no se movió por los PATCH de sucursal.
    row = _disp(client, h, env)
    assert float(row["disponible"]) == 45.0


def test_confirm_with_presentation_descuenta_base_units(client, env, auth_as):
    """Selling in BULTO (1 BULTO = 20 KILO) descuenta el equivalente en unidad
    base: 100 KILO en stock, 2 BULTO → salen 40 KILO. Cancelar restituye 40."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    pid = env["prod_bulto_a"]
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": pid, "almacen_id": env["alm_a"],
        "cantidad": "100", "costo_unitario": "4"})
    rem_id = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
        "lineas": [{"producto_id": pid, "cantidad_solicitada": "2",
                    "precio_unitario": "150", "presentacion": "BULTO"}]}).json()["id"]

    def _row():
        rows = client.get("/api/v1/inventario/existencias", headers=h, params={"producto_id": pid}).json()
        return next(r for r in rows if r["almacen_id"] == env["alm_a"])

    assert client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h).status_code == 200
    row = _row()
    assert float(row["disponible"]) == 60.0   # 100 − (2 × 20)
    assert float(row["reservada"]) == 0.0   # salida directa: sin cubeta de apartado

    assert client.post(f"/api/v1/remisiones/{rem_id}/cancelar", headers=h).status_code == 200
    row2 = _row()
    assert float(row2["disponible"]) == 100.0
    assert float(row2["reservada"]) == 0.0


def test_auto_precio_desde_lista(client, env, auth_as):
    """Sin precio en la línea → se resuelve desde la lista base (UNICO)."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    lista = client.post("/api/v1/listas-precios", headers=h, json={"codigo": "UNICO", "nombre": "Único"}).json()
    client.post(f"/api/v1/listas-precios/{lista['id']}/precios", headers=h,
                json={"producto_id": env["prod_a"], "precio_unitario": "7.50", "cantidad_minima": 1})
    r = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
        "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "4"}]})  # sin precio_unitario
    assert r.status_code == 201, r.text
    rem = r.json()
    assert float(rem["lineas"][0]["precio_unitario"]) == 7.50
    assert float(rem["subtotal"]) == 30.0  # 4 × 7.50


def test_sin_precio_entra_en_cero_pero_no_confirma_ni_factura(client, env, auth_as):
    """Sin precio en línea ni lista ni override: la captura NO se detiene.

    Regla del dueño (1-sep-2026): la línea entra en $0 y el borrador se guarda
    —el hueco se ve—, pero confirmar y facturar quedan cerrados hasta que
    alguien ponga el precio. Antes esto era un 422 al guardar, que obligaba a
    inventar un precio para no perder la captura.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    r = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
        "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "1"}]})
    assert r.status_code == 201, r.text
    rem = r.json()
    assert float(rem["lineas"][0]["precio_unitario"]) == 0.0
    assert rem["estado"] == "BORRADOR"

    confirmar = client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h)
    assert confirmar.status_code == 422
    assert "sin precio" in confirmar.json()["detail"]

    facturar = client.post("/api/v1/facturas/desde-remisiones", headers=h,
                           json={"remision_ids": [rem["id"]]})
    assert facturar.status_code == 422
    assert "sin precio" in facturar.json()["detail"]

    # Con el precio puesto a mano, el mismo borrador ya confirma.
    parche = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={
        "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                    "cantidad_solicitada": "1", "precio_unitario": "9.00"}]})
    assert parche.status_code == 200, parche.text
    assert client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h,
                       json={"permitir_negativos": True}).status_code == 200


def test_lista_base_no_se_adivina(client, env, auth_as):
    """Una lista cualquiera NO se vuelve la base del negocio por ser la más vieja.

    Es el error del 1-sep-2026: sin ninguna marcada, el resolutor tomaba la
    primera lista creada —una negociación de un cliente— y le cobraba con ella
    a todo el mundo. Solo `es_default` (o la convención UNICO) hace base.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    lista = client.post("/api/v1/listas-precios", headers=h,
                        json={"codigo": "NEGOCIADA", "nombre": "Negociada con otro"}).json()
    client.post(f"/api/v1/listas-precios/{lista['id']}/precios", headers=h,
                json={"producto_id": env["prod_a"], "precio_unitario": "99", "cantidad_minima": 1})

    cot = client.get("/api/v1/precios/cotizar", headers=h,
                     params={"producto_id": env["prod_a"], "cliente_id": env["cli_a"]}).json()
    assert cot["precio"] is None, "una lista sin asignar no debe cobrarle a nadie"


def test_confirm_with_real_weight_catch_weight(client, env, auth_as):
    """Confirmar con peso real por línea (catch-weight): descuenta 43 kg (no el
    estimado 40 = 2×20), y cancelar restituye exactamente esos 43."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    pid = env["prod_bulto_a"]
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": pid, "almacen_id": env["alm_a"],
        "cantidad": "100", "costo_unitario": "4"})
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
        "lineas": [{"producto_id": pid, "cantidad_solicitada": "2",
                    "precio_unitario": "150", "presentacion": "BULTO"}]}).json()
    rem_id, linea_id = rem["id"], rem["lineas"][0]["id"]

    def _row():
        rows = client.get("/api/v1/inventario/existencias", headers=h, params={"producto_id": pid}).json()
        return next(r for r in rows if r["almacen_id"] == env["alm_a"])

    c = client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h,
                    json={"pesos": [{"linea_id": linea_id, "cantidad_base": "43"}]})
    assert c.status_code == 200, c.text
    assert float(_row()["disponible"]) == 57.0   # 100 − 43 (real, no 40)
    assert float(_row()["reservada"]) == 0.0   # salida directa: sin cubeta de apartado

    assert client.post(f"/api/v1/remisiones/{rem_id}/cancelar", headers=h).status_code == 200
    assert float(_row()["disponible"]) == 100.0 and float(_row()["reservada"]) == 0.0


def test_patch_confirmada_sin_tocar_cantidades_conserva_inventario(client, env, auth_as):
    """Reeditar una CONFIRMADA cambiando SOLO el precio no debe mover
    inventario: las líneas se reconstruyen (se borran en bloque y se reinsertan)
    y la reserva se hereda por firma (producto, presentación, cantidad)."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _load_stock(client, h, env, "100", "4")
    rem_id = _create_rem(client, h, env, "10", "5").json()["id"]
    assert client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h).status_code == 200
    disponible_tras_confirmar = float(_disp(client, h, env)["disponible"])
    assert disponible_tras_confirmar == 90.0                      # 100 − 10

    r = client.patch(f"/api/v1/remisiones/{rem_id}", headers=h, json={
        "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "10",
                    "precio_unitario": "9"}]})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "CONFIRMADA"
    assert len(cuerpo["lineas"]) == 1                             # no se duplicaron
    assert float(cuerpo["subtotal"]) == 90.0                      # 10 × 9, reprecificado
    # La cantidad no cambió → el inventario tampoco
    assert float(_disp(client, h, env)["disponible"]) == disponible_tras_confirmar


def test_patch_confirmada_con_mas_cantidad_re_reserva(client, env, auth_as):
    """Si la edición SÍ cambia la cantidad, se libera la reserva previa y se
    re-reserva con las líneas nuevas."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _load_stock(client, h, env, "100", "4")
    rem_id = _create_rem(client, h, env, "10", "5").json()["id"]
    client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h)

    r = client.patch(f"/api/v1/remisiones/{rem_id}", headers=h, json={
        "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "25",
                    "precio_unitario": "5"}]})
    assert r.status_code == 200, r.text
    assert float(r.json()["subtotal"]) == 125.0
    assert float(_disp(client, h, env)["disponible"]) == 75.0     # 100 − 25, no 100−10−25


def test_patch_varias_lineas_sin_precio_las_cotiza_en_lote(client, env, auth_as):
    """Guardar sin `precio_unitario` resuelve el precio de CADA línea (el
    frontend lo omite salvo que se teclee a mano). Con varias partidas eso se
    resuelve en un lote — el resultado debe ser el mismo que línea por línea."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    lista = client.post("/api/v1/listas-precios", headers=h,
                        json={"codigo": "UNICO", "nombre": "Único"}).json()
    # Con tramo: 7.50 al menudeo y 6.00 desde 10 — así el lote tiene que elegir
    # tramo POR LÍNEA, no repetir el precio de la primera.
    for precio, minimo in (("7.50", 1), ("6.00", 10)):
        client.post(f"/api/v1/listas-precios/{lista['id']}/precios", headers=h,
                    json={"producto_id": env["prod_a"], "precio_unitario": precio,
                          "cantidad_minima": minimo})
    rem_id = _create_rem(client, h, env, "1", "1").json()["id"]
    r = client.patch(f"/api/v1/remisiones/{rem_id}", headers=h, json={
        "lineas": [
            {"producto_id": env["prod_a"], "cantidad_solicitada": "2"},
            {"producto_id": env["prod_a"], "cantidad_solicitada": "15"},
            {"producto_id": env["prod_bulto_a"], "cantidad_solicitada": "4"},
        ]})
    assert r.status_code == 200, r.text
    lineas = sorted(r.json()["lineas"], key=lambda l: l["numero_linea"])
    assert len(lineas) == 3
    assert float(lineas[0]["precio_unitario"]) == 7.5             # tramo menudeo
    assert float(lineas[1]["precio_unitario"]) == 6.0             # tramo mayoreo (15 ≥ 10)
    assert float(lineas[2]["precio_unitario"]) == 0.0             # sin precio: entra en 0
    assert float(r.json()["subtotal"]) == 105.0                   # 2×7.50 + 15×6 + 0


def test_patch_producto_fuera_de_alcance_es_422(client, env, auth_as):
    """La validación de productos en bloque debe seguir cazando un id inválido
    aunque venga acompañado de otros buenos (existe por RLS: Postgres no aplica
    RLS al chequeo de la FK, así que el constraint solo no basta)."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem_id = _create_rem(client, h, env, "1", "1").json()["id"]
    r = client.patch(f"/api/v1/remisiones/{rem_id}", headers=h, json={
        "lineas": [
            {"producto_id": env["prod_a"], "cantidad_solicitada": "1", "precio_unitario": "5"},
            {"producto_id": str(uuid.uuid4()), "cantidad_solicitada": "1", "precio_unitario": "5"},
        ]})
    assert r.status_code == 422, r.text
    assert "producto_id" in r.text


def test_confirm_insufficient_stock(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem_id = _create_rem(client, h, env, "30", "5").json()["id"]  # no stock loaded
    assert client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h).status_code == 422


def test_confirm_requires_almacen(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem_id = _create_rem(client, h, env, "5", "5", almacen=False).json()["id"]
    assert client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h).status_code == 422


def test_cancel_draft_and_guard(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem_id = _create_rem(client, h, env, "5", "5").json()["id"]
    assert client.post(f"/api/v1/remisiones/{rem_id}/cancelar", headers=h).json()["estado"] == "CANCELADA"
    # can't confirm a cancelled remisión
    assert client.post(f"/api/v1/remisiones/{rem_id}/confirmar", headers=h).status_code == 409


def test_tomador_cannot_touch_remisiones(client, env, auth_as):
    auth_as(env["tomador_a"]); h = _hdr(env["tomador_a"])  # no menu:remisiones
    assert client.get("/api/v1/remisiones", headers=h).status_code == 403
    assert _create_rem(client, h, env, "1", "1").status_code == 403


def test_remisiones_isolated_between_tenants(client, env, auth_as):
    auth_as(env["admin_a"]); ha = _hdr(env["admin_a"])
    rem_id = _create_rem(client, ha, env, "1", "1").json()["id"]

    auth_as(env["admin_b"]); hb = _hdr(env["admin_b"])
    assert client.get(f"/api/v1/remisiones/{rem_id}", headers=hb).status_code == 404
    # B referencing tenant A's cliente → ensure_fk 422
    cross = client.post("/api/v1/remisiones", headers=hb, json={
        "cliente_facturacion_id": env["cli_a"],
        "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "1", "precio_unitario": "1"}]})
    assert cross.status_code == 422


# ── Importación masiva estilo SAE (Excel → varias remisiones) ────────────────
def _xlsx_sae(rows):
    """Construye un .xlsx en memoria con el layout SAE (FOLIO/CLIENTE/…)."""
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["FOLIO", "CLIENTE", "FECHA", "SU PEDIDO", "CLAVE", "CANTIDAD", "PRECIO", "Observaciones"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_importar_preview_agrupa_y_cruza(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    sku = client.get("/api/v1/productos", headers=h, params={"limit": 1}).json()["items"][0]
    data = _xlsx_sae([
        ["0000001230", "C-X", "29/07/2026", "OC 1", sku["sku"], "2", "10", "entrega lunes"],
        ["0000001230", "C-X", "29/07/2026", None, "SKU-QUE-NO-EXISTE", "1", "5", None],
        ["ZHGO9",      "C-X", "29/07/2026", None, sku["sku"], "3", "12", None],
    ])
    r = client.post("/api/v1/remisiones/importar-preview", headers=h,
                    files={"archivo": ("pedidos.xlsx", data,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    p = r.json()
    assert len(p["grupos"]) == 2
    g1 = next(g for g in p["grupos"] if g["folio_ref"] == "1230")   # sin ceros
    assert g1["su_pedido"] == "OC 1"
    assert len(g1["lineas"]) == 2
    cruzada = next(l for l in g1["lineas"] if l["clave"] == sku["sku"])
    assert cruzada["producto_id"] == sku["id"]                       # CLAVE = SKU exacto
    sin_cruce = next(l for l in g1["lineas"] if l["clave"] == "SKU-QUE-NO-EXISTE")
    assert sin_cruce["producto_id"] is None
    assert p["productos_sin_cruce"] == 1


def test_importar_preview_rechaza_formato_invalido(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    r = client.post("/api/v1/remisiones/importar-preview", headers=h,
                    files={"archivo": ("nota.txt", b"esto no es excel", "text/plain")})
    assert r.status_code == 422


# ── Importación del Master Ordenes (hoja "Master" del concentrado de OC) ─────
def _xlsx_master(rows):
    """Construye un .xlsx con el layout del Master Ordenes: la hoja de renglones
    se llama "Master" y el libro arrastra además "Summary"/"Totales"."""
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Master"
    ws.append([
        "Archivo", "Tipo Documento", "RFC Cliente", "Nombre Cliente", "RFC Proveedor",
        "Nombre Proveedor", "Folio", "Requisicion Folio", "Referencia", "Fecha",
        "Cantidad", "Unidad", "Clave", "Descripcion", "Costo unitario", "DESC",
        "Subtotal", "Observacion del documento", "Entregar Bodega",
    ])
    for r in rows:
        ws.append(r)
    wb.create_sheet("Summary").append(["Folio", "Cliente", "Total"])
    wb.create_sheet("Totales").append(["Documentos", len(rows)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_importar_master_ordenes_cruza_por_rfc_y_nombre(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    sku = client.get("/api/v1/productos", headers=h, params={"limit": 1}).json()["items"][0]
    fila = lambda folio, rfc, nombre, clave, cant: [  # noqa: E731
        "OCO 458.pdf", "ORDEN DE COMPRA", rfc, nombre, "ZAOC830517RF9",
        "CRISTIAN GERARDO ZARATE OROZCO", folio, "0000000271", "CEUHM VERDURA",
        "46209",  # serial de Excel = 2026-07-06
        cant, "KILOGRAMO", clave, "AJO MORADO", "90", "0", "45",
        "ENTREGAR EN COMEDOR EL JUEVES", "16 DE JUL",
    ]
    data = _xlsx_master([
        fila("0000000458", "XAXX010101000", "Cliente 1", sku["sku"], "2"),
        fila("0000000458", "XAXX010101000", "Cliente 1", "AJO -FRUT-017", "0.5"),
        # RFC desconocido: el cruce cae al nombre, sin la razón social.
        fila("0000024460", "AAA010101AAA", "Cliente 1 S.A. de C.V.", sku["sku"], "3"),
    ])
    r = client.post("/api/v1/remisiones/importar-preview", headers=h,
                    files={"archivo": ("Master Ordenes.xlsx", data,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    p = r.json()
    assert len(p["grupos"]) == 2
    g1 = next(g for g in p["grupos"] if g["folio_ref"] == "458")     # sin ceros
    assert g1["cliente_id"] == env["cli_a"]                          # cruzó por RFC
    assert g1["fecha"] == "2026-07-06"                               # serial de Excel
    assert g1["su_pedido"] == "CEUHM VERDURA"                        # Referencia
    assert g1["observaciones"] == "ENTREGAR EN COMEDOR EL JUEVES"
    assert g1["requisicion"] == "271"                                # sin ceros
    assert g1["entregar_bodega"] == "16 DE JUL"
    cruzada = next(l for l in g1["lineas"] if l["clave"] == sku["sku"])
    assert cruzada["producto_id"] == sku["id"] and float(cruzada["precio"]) == 90
    sin_cruce = next(l for l in g1["lineas"] if l["clave"] == "AJO -FRUT-017")
    assert sin_cruce["producto_id"] is None
    assert sin_cruce["descripcion"] == "AJO MORADO" and sin_cruce["unidad"] == "KILOGRAMO"
    g2 = next(g for g in p["grupos"] if g["folio_ref"] == "24460")
    assert g2["cliente_id"] == env["cli_a"]                          # cruzó por nombre
    assert p["clientes_sin_cruce"] == 0


# ── Factura de SAE → estado RESERVADO ─────────────────────────────────────────
def test_factura_sae_reserva_y_al_quitarla_vuelve_a_borrador(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    base = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
            "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "2", "precio_unitario": "10"}]}

    # Nace RESERVADA si el alta trae el folio de SAE.
    r = client.post("/api/v1/remisiones", headers=h,
                    json={**base, "factura_sae": "ZHGO 233", "su_pedido": "0000024478"})
    assert r.status_code == 201, r.text
    assert r.json()["estado"] == "RESERVADO"
    assert r.json()["factura_sae"] == "ZHGO 233"
    # "Su pedido" es la OC del cliente y va en su propia columna, no en las notas.
    assert r.json()["su_pedido"] == "0000024478"

    # Sin folio nace BORRADOR; ponérselo después la reserva.
    rem = client.post("/api/v1/remisiones", headers=h, json=base).json()
    assert rem["estado"] == "BORRADOR" and rem["factura_sae"] is None
    up = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={"factura_sae": "ZHGO 234"})
    assert up.status_code == 200, up.text
    assert up.json()["estado"] == "RESERVADO"

    # Quitarlo la regresa a BORRADOR.
    up = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={"factura_sae": ""})
    assert up.json()["estado"] == "BORRADOR" and up.json()["factura_sae"] is None

    # Una RESERVADA se confirma igual que un borrador (la salida es el confirmar).
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod_a"], "almacen_id": env["alm_a"],
        "cantidad": "10", "costo_unitario": "4"})
    client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={"factura_sae": "ZHGO 235"})
    conf = client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h, json={})
    assert conf.status_code == 200, conf.text
    assert conf.json()["estado"] == "CONFIRMADA"
    # Ya confirmada, el folio de SAE es un dato más: no la regresa a borrador.
    up = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={"factura_sae": ""})
    assert up.json()["estado"] == "CONFIRMADA"


def test_busqueda_q_folio_pedido_factura_sae(client, env, auth_as):
    """`q` busca en el servidor (folio interno, su pedido, factura SAE): el
    buscador de la tabla solo ve la página cargada y lo viejo se le escapaba.
    SAE muestra los folios rellenos de ceros, así que esa variante también pega.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    # Términos únicos (la BD de pruebas no aísla por RLS) y SOLO letras: un
    # dígito en la "serie" chocaría con la normalización de ceros que se prueba.
    tag = uuid.uuid4().hex[:6].translate(str.maketrans("0123456789", "ghjkmnpqrt")).upper()

    def _crear(**extra):
        body = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
                "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": "1",
                            "precio_unitario": "5"}], **extra}
        r = client.post("/api/v1/remisiones", headers=h, json=body)
        assert r.status_code == 201, r.text
        return r.json()

    con_pedido = _crear(su_pedido=f"HO-34-{tag}")
    con_sae = _crear(factura_sae=f"Z{tag} 588")
    con_oc = _crear(su_pedido=f"VH-{tag}SAL-LUN")
    _crear()  # ruido: no debe aparecer en las búsquedas

    def _ids(q):
        res = client.get("/api/v1/remisiones", headers=h, params={"q": q})
        assert res.status_code == 200, res.text
        return {r["id"] for r in res.json()["items"]}

    assert _ids(f"HO-34-{tag}") == {con_pedido["id"]}
    assert con_pedido["id"] in _ids(con_pedido["folio_interno"])
    # La factura SAE pega como se guardó, sin espacio, y como la muestra SAE
    # (folio con ceros a la izquierda).
    assert _ids(f"Z{tag} 588") == {con_sae["id"]}
    assert _ids(f"Z{tag}588") == {con_sae["id"]}
    assert _ids(f"Z{tag} 0000588") == {con_sae["id"]}
    # Por PALABRAS: la OC va pegada ("VH-…SAL-LUN") y se teclea separada.
    assert _ids(f"VH-{tag} LUN") == {con_oc["id"]}
    assert _ids(f"sin-coincidencias-{tag}") == set()


# ─── Una remisión impresa no la reescribe una sincronización ─────────────────
# El 16-sep-2026 el vigía del bot (Master de Sheets -> Facturador, cada hora)
# pisó nueve remisiones de la semana 38 que ya estaban impresas y firmadas por
# el cliente: les devolvió las cantidades del pedido encima de los pesos de
# báscula y les volvió a meter partidas que no se entregaron.

def _ctx_de_conexion(tenant_id):
    """El contexto que arma una CONEXIÓN (el bot), sin persona detrás."""
    from app.core.rbac import AuthContext, PERMISOS_CONEXION
    return AuthContext(
        user_id=None, auth_user_id="conexion-test", email=None,
        tenant_id=uuid.UUID(str(tenant_id)), role_id=None,
        role_name="Conexión · test", is_owner=False,
        permissions=set(PERMISOS_CONEXION), memberships=[],
        conexion_id=uuid.uuid4(),
    )


def test_imprimir_marca_la_remision_y_congela_el_sync(client, env, auth_as):
    from app.core.rbac import get_auth_context

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem = _create_rem(client, h, env, "10", "5").json()
    assert rem["impresa_at"] is None

    pdf = client.get(f"/api/v1/remisiones/{rem['id']}/pdf", headers=h)
    assert pdf.status_code == 200, pdf.text

    detalle = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    impresa_at = detalle["impresa_at"]
    assert impresa_at is not None, "imprimir tiene que dejar rastro"

    # Reimprimir no mueve la fecha: lo que importa es el PRIMER papel.
    client.get(f"/api/v1/remisiones/{rem['id']}/pdf", headers=h)
    assert client.get(f"/api/v1/remisiones/{rem['id']}",
                      headers=h).json()["impresa_at"] == impresa_at

    partidas = {"lineas": [{"producto_id": env["prod_a"],
                            "cantidad_solicitada": "99", "precio_unitario": "5"}]}

    # La conexión ya no puede tocar las partidas del papel firmado.
    app.dependency_overrides[get_auth_context] = (
        lambda: _ctx_de_conexion(env["admin_a"]["tenant_id"]))
    try:
        r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json=partidas)
        assert r.status_code == 409, r.text
        assert "ya se imprimió" in r.json()["detail"]
        # Lo que NO es el detalle firmado sigue pasando.
        assert client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                            json={"notas": "llegó tarde"}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_auth_context, None)

    # Una PERSONA sí corrige: es quien puede hablar con el cliente.
    r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json=partidas)
    assert r.status_code == 200, r.text
    assert float(r.json()["subtotal"]) == 495.0


def test_sin_imprimir_el_sync_sigue_entrando(client, env, auth_as):
    """El candado es por el papel, no por la conexión: mientras no se imprima,
    el bot sigue pudiendo corregir lo que bodega pesó."""
    from app.core.rbac import get_auth_context

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem = _create_rem(client, h, env, "10", "5").json()

    app.dependency_overrides[get_auth_context] = (
        lambda: _ctx_de_conexion(env["admin_a"]["tenant_id"]))
    try:
        r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json={
            "lineas": [{"producto_id": env["prod_a"],
                        "cantidad_solicitada": "10.3", "precio_unitario": "5"}]})
        assert r.status_code == 200, r.text
        assert float(r.json()["subtotal"]) == 51.5
    finally:
        app.dependency_overrides.pop(get_auth_context, None)


def test_exportada_a_sae_se_congela_para_todos(client, env, auth_as):
    """Lo que ya salió en un archivo de SAE no se reescribe (21-sep-2026).

    Tres cosas que el candado de la impresión NO cubría y ésta sí: congela en
    vez de avisar, congela el ENCABEZADO además de las partidas, y congela
    también a las PERSONAS —no solo a la conexión—. El motivo es que el archivo
    ya viajó: si aquí cambia el cliente o el importe, SAE y el Facturador
    cuentan dos historias de la misma venta y nadie se entera.
    """
    from app.core.rbac import get_auth_context
    from app.models.remision import Remision

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem = _create_rem(client, h, env, "10", "5").json()

    partidas = {"lineas": [{"producto_id": env["prod_a"],
                            "cantidad_solicitada": "99", "precio_unitario": "5"}]}

    # Antes de exportar, todo pasa: el candado es por el archivo, no por el estado.
    assert client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                        json=partidas).status_code == 200
    assert client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                        json={"notas": "antes de exportar"}).status_code == 200

    # Se marca como exportada, que es lo que hace export_sae.py al generar.
    with SessionLocal() as s:
        r = s.query(Remision).filter(Remision.id == uuid.UUID(rem["id"])).one()
        r.export_sae_at = datetime.now(timezone.utc)
        s.commit()

    # La PERSONA tampoco: ésta es la diferencia con el candado de la impresión.
    r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json=partidas)
    assert r.status_code == 409, r.text
    assert "ya salió en el masivo de SAE" in r.json()["detail"]

    # Y el ENCABEZADO queda igual de congelado, sin tocar una sola línea.
    for cuerpo in ({"notas": "después de exportar"},
                   {"descuento": "10"},
                   {"fecha_entrega": "2030-01-01"}):
        r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json=cuerpo)
        assert r.status_code == 409, f"{cuerpo} debería estar congelado: {r.text}"

    # La conexión, igual.
    app.dependency_overrides[get_auth_context] = (
        lambda: _ctx_de_conexion(env["admin_a"]["tenant_id"]))
    try:
        r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h, json=partidas)
        assert r.status_code == 409, r.text
    finally:
        app.dependency_overrides.pop(get_auth_context, None)

    # Un pedido exportado congela igual: el archivo salió aunque no sea fiscal.
    rem2 = _create_rem(client, h, env, "3", "7").json()
    with SessionLocal() as s:
        r2 = s.query(Remision).filter(Remision.id == uuid.UUID(rem2["id"])).one()
        r2.export_pedido_at = datetime.now(timezone.utc)
        s.commit()
    r = client.patch(f"/api/v1/remisiones/{rem2['id']}", headers=h, json={"notas": "x"})
    assert r.status_code == 409, r.text
    assert "ya salió en un pedido de SAE" in r.json()["detail"]


def test_exportada_deja_pasar_solo_el_acuse_de_sae(client, env, auth_as):
    """La única excepción del congelamiento: sellar `factura_sae`.

    Eso no edita el documento, acusa lo que SAE hizo con él — y es la llave que
    después lo libera, porque `export_sae_at` solo se limpia cuando el espejo
    confirma que esa factura se canceló en SAE. Congelar el acuse convertiría el
    candado en una trampa sin salida.
    """
    from app.models.remision import Remision

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem = _create_rem(client, h, env, "4", "9").json()
    with SessionLocal() as s:
        r = s.query(Remision).filter(Remision.id == uuid.UUID(rem["id"])).one()
        r.export_sae_at = datetime.now(timezone.utc)
        s.commit()

    # Solo el acuse: pasa.
    r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                     json={"factura_sae": "ZHGO 900"})
    assert r.status_code == 200, r.text
    assert r.json()["factura_sae"] == "ZHGO 900"

    # El acuse acompañado de cualquier otra cosa: NO. Si no, sería la puerta de
    # atrás para editar el documento colando un sello.
    r = client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                     json={"factura_sae": "ZHGO 901", "notas": "colado"})
    assert r.status_code == 409, r.text


def test_liberar_pedido_es_la_llave_del_candado(client, env, auth_as):
    """El candado de PEDIDO no tenía llave: `export_pedido_at` no lo limpia
    nadie (el de factura lo limpia el espejo cuando SAE cancela). Liberar exige
    una PERSONA y un motivo, deja rastro en las notas, y la remisión vuelve a
    ser editable."""
    from app.core.rbac import get_auth_context
    from app.models.remision import Remision

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem = _create_rem(client, h, env, "4", "9").json()
    with SessionLocal() as s:
        r = s.query(Remision).filter(Remision.id == uuid.UUID(rem["id"])).one()
        r.export_pedido_at = datetime.now(timezone.utc)
        r.export_pedido_folio = "77"
        s.commit()

    # Congelada: ni una nota pasa.
    assert client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                        json={"notas": "x"}).status_code == 409

    # Sin motivo no hay liberación.
    assert client.post(f"/api/v1/remisiones/{rem['id']}/liberar-pedido", headers=h,
                       json={}).status_code == 422

    # Una conexión no puede: no tiene con qué saber qué pasó con el archivo.
    app.dependency_overrides[get_auth_context] = (
        lambda: _ctx_de_conexion(env["admin_a"]["tenant_id"]))
    try:
        r2 = client.post(f"/api/v1/remisiones/{rem['id']}/liberar-pedido", headers=h,
                         json={"motivo": "el archivo nunca se importó"})
        assert r2.status_code == 403, r2.text
    finally:
        app.dependency_overrides.pop(get_auth_context, None)

    # Una persona sí, y queda el rastro.
    r3 = client.post(f"/api/v1/remisiones/{rem['id']}/liberar-pedido", headers=h,
                     json={"motivo": "el archivo nunca se importó en Aspel"})
    assert r3.status_code == 200, r3.text
    det = r3.json()
    assert det["export_pedido_at"] is None and det["export_pedido_folio"] is None
    assert "Liberada del pedido 77" in (det["notas"] or "")
    assert "nunca se importó" in det["notas"]

    # Y la remisión vuelve a ser editable.
    assert client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                        json={"notas": det["notas"] + "\neditada"}).status_code == 200

    # Liberar dos veces no tiene sentido: ya no está congelada.
    assert client.post(f"/api/v1/remisiones/{rem['id']}/liberar-pedido", headers=h,
                       json={"motivo": "otra vez"}).status_code == 409


def test_liberar_pedido_no_abre_el_candado_de_factura(client, env, auth_as):
    """Si además salió en el masivo de FACTURA, esta llave no aplica: esa se
    libera cancelando en SAE. Abrirla por aquí dejaría un CFDI exportado con la
    remisión editable debajo."""
    from app.models.remision import Remision

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem = _create_rem(client, h, env, "2", "5").json()
    with SessionLocal() as s:
        r = s.query(Remision).filter(Remision.id == uuid.UUID(rem["id"])).one()
        r.export_pedido_at = datetime.now(timezone.utc)
        r.export_sae_at = datetime.now(timezone.utc)
        s.commit()
    r2 = client.post(f"/api/v1/remisiones/{rem['id']}/liberar-pedido", headers=h,
                     json={"motivo": "x y z"})
    assert r2.status_code == 409
    assert "cancelando en SAE" in r2.json()["detail"]


def test_reporte_compras_pivotea_por_fecha_y_presentacion(client, env, auth_as):
    """La materia prima de la lista de compras: cuánto se pide de cada
    producto+presentación por fecha de entrega, desde remisiones vivas. La
    canceladas no compran nada y la presentación nunca se mezcla."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])

    def rem_con_fecha(qty, fecha):
        body = {
            "cliente_facturacion_id": env["cli_a"],
            "almacen_id": env["alm_a"],
            "fecha_entrega": fecha,
            "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                        "cantidad_solicitada": qty, "precio_unitario": "5"}],
        }
        r = client.post("/api/v1/remisiones", headers=h, json=body)
        assert r.status_code == 201, r.text
        return r.json()

    rem_con_fecha("10", "2031-01-06")
    rem_con_fecha("7", "2031-01-06")      # mismo día: se suma
    rem_con_fecha("3", "2031-01-07")      # otro día: otra fila
    cancelada = rem_con_fecha("99", "2031-01-06")
    client.post(f"/api/v1/remisiones/{cancelada['id']}/cancelar", headers=h)

    r = client.get("/api/v1/remisiones/reporte-compras"
                   "?fechas=2031-01-06,2031-01-07", headers=h)
    assert r.status_code == 200, r.text
    filas = r.json()["filas"]
    por_fecha = {f["fecha"]: f for f in filas if f["unidad"] == "KILO"}
    assert float(por_fecha["2031-01-06"]["cantidad"]) == 17.0, filas   # 10+7, sin la cancelada
    assert float(por_fecha["2031-01-07"]["cantidad"]) == 3.0, filas

    # fechas ilegibles no pasan
    assert client.get("/api/v1/remisiones/reporte-compras?fechas=ayer",
                      headers=h).status_code == 422

    # con perfil, solo el universo de ese Master (aquí: ninguno → vacío)
    r2 = client.get("/api/v1/remisiones/reporte-compras"
                    "?fechas=2031-01-06&perfil=ehmo", headers=h)
    assert r2.status_code == 200 and r2.json()["filas"] == []


def test_reporte_compras_usa_el_documento_nuevo_si_hay_incidencia(client, env, auth_as):
    """Cuando la OC recibió una versión posterior sin aplicar, la lista de
    compras usa las líneas del DOCUMENTO nuevo y excluye las capturadas: comprar
    con la versión vieja compra de menos justo donde el cliente cambió."""
    from app.models.oc_recibida import OCRecibida

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    body = {
        "cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
        "fecha_entrega": "2031-02-03",
        "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                    "cantidad_solicitada": "10", "precio_unitario": "5"}],
    }
    rem = client.post("/api/v1/remisiones", headers=h, json=body).json()
    with SessionLocal() as s:
        s.add(OCRecibida(
            tenant_id=env["admin_a"]["tenant_id"],
            canal="WHATSAPP", origen_externo="EHMO:prueba:X-1", folio_externo="X-1",
            estado="ASIGNADA", remision_id=uuid.UUID(rem["id"]),
            payload={"lineas": [{"descripcion": "AJO", "cantidad": "10", "unidad": "KILO"}]},
            payload_nuevo={"lineas": [{"descripcion": "AJO", "cantidad": "25",
                                       "unidad": "KILO", "clave": "AJOKG"},
                                      {"descripcion": "SAL DE GRANO", "cantidad": "2",
                                       "unidad": "KILO"}]},
            cambio_detectado_at=datetime.now(timezone.utc),
        ))
        s.commit()

    r = client.get("/api/v1/remisiones/reporte-compras?fechas=2031-02-03", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["con_cambio_abierto"] == 1
    filas = out["filas"]
    # las capturadas (10 KILO del producto) NO están; las del documento sí
    doc = [f for f in filas if f.get("documento_nuevo")]
    assert {(f["descripcion"], f["cantidad"]) for f in doc} == {("AJO", "25"), ("SAL DE GRANO", "2")}
    assert not [f for f in filas if not f.get("documento_nuevo")
                and f["fecha"] == "2031-02-03"], filas


def test_reporte_armado_por_fecha_con_categoria_y_sin_fecha(client, env, auth_as):
    """La materia prima de la hoja de armado: detalle por remisión (folio del
    cliente, cliente, bodega, líneas con categoría). Las canceladas no arman
    nada, y una remisión SIN fecha de bodega viaja en `sin_fecha` — que se
    caiga de la hoja en silencio es el hoyo de la 24973 ($50,633.78)."""
    from app.models import CategoriaProducto, Producto

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    with SessionLocal() as s:
        cat = CategoriaProducto(tenant_id=env["admin_a"]["tenant_id"],
                                codigo="FRUTA2", nombre="FRUTAS Y VERDURAS")
        s.add(cat); s.flush()
        s.query(Producto).filter(Producto.id == uuid.UUID(env["prod_a"])).update(
            {"categoria_id": cat.id})
        s.commit()

    def rem(qty, fecha, folio):
        body = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
                "su_pedido": folio,
                "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                            "cantidad_solicitada": qty, "precio_unitario": "5",
                            "notas": "en malla"}]}
        if fecha:
            body["fecha_entrega"] = fecha
        r = client.post("/api/v1/remisiones", headers=h, json=body)
        assert r.status_code == 201, r.text
        return r.json()

    rem("10", "2031-03-02", "24610")
    rem("7", "2031-03-02", "24611")
    rem("3", "2031-03-03", "24612")          # otro día: fuera del filtro
    cancelada = rem("99", "2031-03-02", "24613")
    client.post(f"/api/v1/remisiones/{cancelada['id']}/cancelar", headers=h)
    rem("4", None, "24973")                  # sin bodega: al aviso

    r = client.get("/api/v1/remisiones/reporte-armado?fechas=2031-03-02", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    por_folio = {x["folio"]: x for x in out["remisiones"]}
    assert set(por_folio) == {"24610", "24611"}, out["remisiones"]
    ln = por_folio["24610"]["lineas"][0]
    assert ln["unidad"] == "KILO" and ln["nota"] == "en malla"
    assert ln["categoria"] == "FRUTAS Y VERDURAS"
    assert float(ln["cantidad"]) == 10.0
    assert por_folio["24610"]["bodega"] == "2031-03-02"
    assert "24973" in {x["folio"] for x in out["sin_fecha"]}, out["sin_fecha"]

    # sin filtro no hay reporte; con fecha ilegible tampoco
    assert client.get("/api/v1/remisiones/reporte-armado", headers=h).status_code == 422
    assert client.get("/api/v1/remisiones/reporte-armado?fechas=lunes",
                      headers=h).status_code == 422


def test_reporte_armado_por_folios_ignora_fecha(client, env, auth_as):
    """Con lista de OC la fecha no pinta (el comando manda igual), y el folio
    casa con ceros a la izquierda de CUALQUIERA de los dos lados: en la base
    `su_pedido` llega relleno desde SAE ('0000024620' — con el filtro literal
    no casaba ni una, medido 21-sep-2026)."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    body = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
            "su_pedido": "0000024620", "fecha_entrega": "2031-04-06",
            "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                        "cantidad_solicitada": "6", "precio_unitario": "5"}]}
    assert client.post("/api/v1/remisiones", headers=h, json=body).status_code == 201

    r = client.get("/api/v1/remisiones/reporte-armado"
                   "?folios=0024620&fechas=1999-01-01", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [x["folio"] for x in out["remisiones"]] == ["24620"]
    # por folios no se filtra por fecha, y el aviso de sin_fecha no aplica
    assert out["sin_fecha"] == []


def test_reporte_armado_usa_el_documento_nuevo_si_hay_incidencia(client, env, auth_as):
    """Misma regla que la lista de compras: con incidencia de cambio abierta,
    la hoja arma con las líneas del DOCUMENTO nuevo (marcadas) y las capturadas
    se excluyen — armar con la versión vieja surte de menos justo donde el
    cliente cambió."""
    from app.models.oc_recibida import OCRecibida

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    body = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
            "su_pedido": "24630", "fecha_entrega": "2031-05-04",
            "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                        "cantidad_solicitada": "10", "precio_unitario": "5"}]}
    rem = client.post("/api/v1/remisiones", headers=h, json=body).json()
    with SessionLocal() as s:
        s.add(OCRecibida(
            tenant_id=env["admin_a"]["tenant_id"],
            canal="WHATSAPP", origen_externo="WA:grupo:24630", folio_externo="24630",
            estado="ASIGNADA", remision_id=uuid.UUID(rem["id"]),
            payload={"lineas": [{"descripcion": "AJO", "cantidad": "10", "unidad": "KILO"}]},
            payload_nuevo={"lineas": [{"descripcion": "AJO", "cantidad": "25",
                                       "unidad": "KILO"},
                                      {"descripcion": "SAL DE GRANO", "cantidad": "2",
                                       "unidad": "KILO", "notas": "grano grueso"}]},
            cambio_detectado_at=datetime.now(timezone.utc),
        ))
        s.commit()

    r = client.get("/api/v1/remisiones/reporte-armado?folios=24630", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["con_cambio_abierto"] == 1
    (fila,) = out["remisiones"]
    assert fila["documento_nuevo"] is True
    assert {(l["descripcion"], l["cantidad"]) for l in fila["lineas"]} == \
        {("AJO", "25"), ("SAL DE GRANO", "2")}
    assert [l["nota"] for l in fila["lineas"] if l["descripcion"] == "SAL DE GRANO"] == \
        ["grano grueso"]


def test_reporte_armado_origen_acota_el_carril(client, env, auth_as):
    """El Master de Balles/Jubrán no ve las entregas de EHMO y viceversa: el
    filtro por fecha con `origen` solo trae remisiones cuya OC entró por ese
    carril (medido 21-sep-2026: sin esto, 21 remisiones de hospitales caían
    dentro de la hoja de Balles)."""
    from app.models.oc_recibida import OCRecibida

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])

    def rem(folio, origen):
        body = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
                "su_pedido": folio, "fecha_entrega": "2031-06-02",
                "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                            "cantidad_solicitada": "5", "precio_unitario": "5"}]}
        r = client.post("/api/v1/remisiones", headers=h, json=body).json()
        if origen:
            with SessionLocal() as s:
                s.add(OCRecibida(
                    tenant_id=env["admin_a"]["tenant_id"], canal="WHATSAPP",
                    origen_externo=f"{origen}{folio}", folio_externo=folio,
                    estado="ASIGNADA", remision_id=uuid.UUID(r["id"]),
                    payload={"lineas": []}))
                s.commit()
        return r

    rem("24700", "WA:grupo@g.us:")
    rem("24701", "EMAIL:compras@x.mx:")
    rem("HO-39", "EHMO:ehmo:")
    rem("R-MANUAL", None)                     # capturada a mano: sin OC

    r = client.get("/api/v1/remisiones/reporte-armado"
                   "?fechas=2031-06-02&origen=WA:,EMAIL:", headers=h)
    assert r.status_code == 200, r.text
    assert {x["folio"] for x in r.json()["remisiones"]} == {"24700", "24701"}

    r2 = client.get("/api/v1/remisiones/reporte-armado"
                    "?fechas=2031-06-02&origen=EHMO:ehmo:", headers=h)
    assert {x["folio"] for x in r2.json()["remisiones"]} == {"HO-39"}
    assert r2.json()["remisiones"][0]["hospital"] == ""

    # sin origen: todas (incluida la manual) — más de lo que ve un Master, no menos
    r3 = client.get("/api/v1/remisiones/reporte-armado?fechas=2031-06-02", headers=h)
    assert len(r3.json()["remisiones"]) == 4


def test_reporte_armado_avisa_las_oc_sin_remision(client, env, auth_as):
    """La OC que entró a la bandeja y nunca se cruzó no tiene remisión, así que
    no sale en ninguna consulta del reporte — y la hoja saldría sin ella sin
    decir nada (116 de 224 órdenes del Master estaban así al migrar). Viaja en
    `sin_remision`, acotada al mismo carril."""
    from app.models.oc_recibida import OCRecibida

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    with SessionLocal() as s:
        for folio, origen, estado, fecha in (
            ("25900", "WA:g@g.us:", "PENDIENTE", "2031-07-07"),
            ("25901", "EMAIL:c@x.mx:", "AMBIGUA", "2031-07-07"),
            ("25902", "WA:g@g.us:", "DESCARTADA", "2031-07-07"),   # descartada: no se arma
            ("25903", "WA:g@g.us:", "PENDIENTE", "2031-07-08"),    # otro día
            ("HO-91", "EHMO:ehmo:", "PENDIENTE", "2031-07-07"),    # otro carril
        ):
            s.add(OCRecibida(
                tenant_id=env["admin_a"]["tenant_id"], canal="WHATSAPP",
                origen_externo=f"{origen}{folio}", folio_externo=folio, estado=estado,
                payload={"fecha_entrega": fecha,
                         "lineas": [{"descripcion": "AJO", "cantidad": "3"}]}))
        s.commit()

    r = client.get("/api/v1/remisiones/reporte-armado"
                   "?fechas=2031-07-07&origen=WA:,EMAIL:", headers=h)
    assert r.status_code == 200, r.text
    sr = r.json()["sin_remision"]
    assert {x["folio"] for x in sr} == {"25900", "25901"}, sr
    assert sr[0]["partidas"] == 1 and sr[0]["bodega"] == "2031-07-07"

    # el otro carril ve la suya y no las de Balles
    r2 = client.get("/api/v1/remisiones/reporte-armado"
                    "?fechas=2031-07-07&origen=EHMO:ehmo:", headers=h)
    assert {x["folio"] for x in r2.json()["sin_remision"]} == {"HO-91"}

    # por lista de OC no hay aviso por fecha: lo que se pidió es explícito
    r3 = client.get("/api/v1/remisiones/reporte-armado?folios=25900", headers=h)
    assert r3.json()["sin_remision"] == []


def test_reporte_sin_precio_separa_los_cuatro_trabajos(client, env, auth_as):
    """Lo que hoy no se puede facturar bien, agrupado por TRABAJO: sin clave,
    clave que esa empresa de SAE no conoce, clave de baja y sin precio. Se
    separan porque el arreglo de cada uno lo hace una persona distinta."""
    from app.models import ClaveSae, ClienteExterno, Producto

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    # el producto de siempre: con clave que SAE sí conoce, y con precio
    with SessionLocal() as s:
        s.query(Producto).filter(Producto.id == uuid.UUID(env["prod_a"])).update(
            {"clave_sae": "AJOKG"})
        s.add(ClaveSae(tenant_id=env["admin_a"]["tenant_id"], empresa="02",
                       clave="AJOKG", activa=True))
        s.add(ClienteExterno(tenant_id=env["admin_a"]["tenant_id"],
                             cliente_id=uuid.UUID(env["cli_a"]), sistema="SAE",
                             clave="02:8", clave_normalizada="02 8",
                             confianza="CONFIRMADA"))
        s.commit()

    def rem(precio, *, por_cruzar=None, su_pedido="26000"):
        body = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
                "su_pedido": su_pedido, "fecha_entrega": "2031-08-04",
                "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                            "cantidad_solicitada": "4", "precio_unitario": precio}]}
        r = client.post("/api/v1/remisiones", headers=h, json=body)
        assert r.status_code == 201, r.text
        if por_cruzar:
            pc = client.patch(f"/api/v1/remisiones/{r.json()['id']}", headers=h,
                              json={"partidas_por_cruzar": por_cruzar})
            assert pc.status_code == 200, pc.text
        return r.json()

    rem("5")                                        # sana: no sale
    rem("0", su_pedido="26001")                     # sin precio
    rem("5", su_pedido="26002", por_cruzar=[
        {"numero": 1, "descripcion": "SAL DE GRANO", "cantidad": "2", "unidad": "KILO"}])

    r = client.get("/api/v1/remisiones/reporte-sin-precio?fechas=2031-08-04", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    por_motivo = {p["motivo"]: p for p in out["productos"]}
    assert "SIN PRECIO" in por_motivo, out
    assert por_motivo["SIN PRECIO"]["folios"] == ["26001"]
    assert "SIN CLAVE" in por_motivo, out
    assert por_motivo["SIN CLAVE"]["descripcion"] == "SAL DE GRANO"
    # el producto con clave viva y precio no aparece por ningún motivo
    assert not [p for p in out["productos"]
                if p["descripcion"] == "Prod R" and p["motivo"] != "SIN PRECIO"], out
    assert out["sin_precio"] == 1 and out["sin_clave"] == 1


def test_reporte_sin_precio_ve_la_clave_que_esa_empresa_no_conoce(client, env, auth_as):
    """La clave existe aquí y en la 02, pero la remisión factura por la 03: esa
    empresa no la conoce y el masivo moriría. Es un trabajo distinto de «no
    tiene clave» — hay que darla de alta ALLÁ."""
    from app.models import ClaveSae, ClienteExterno, Producto

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    with SessionLocal() as s:
        s.query(Producto).filter(Producto.id == uuid.UUID(env["prod_a"])).update(
            {"clave_sae": "LIMOKG"})
        # el espejo de la 03 existe (así el fail-open no aplica) pero sin esa clave
        s.add(ClaveSae(tenant_id=env["admin_a"]["tenant_id"], empresa="03",
                       clave="OTRACOSA", activa=True))
        s.add(ClienteExterno(tenant_id=env["admin_a"]["tenant_id"],
                             cliente_id=uuid.UUID(env["cli_a"]), sistema="SAE",
                             clave="03:9", clave_normalizada="03 9",
                             confianza="CONFIRMADA"))
        s.commit()
    body = {"cliente_facturacion_id": env["cli_a"], "almacen_id": env["alm_a"],
            "su_pedido": "26100", "fecha_entrega": "2031-08-05",
            "lineas": [{"producto_id": env["prod_a"], "presentacion": "KILO",
                        "cantidad_solicitada": "4", "precio_unitario": "5"}]}
    assert client.post("/api/v1/remisiones", headers=h, json=body).status_code == 201

    out = client.get("/api/v1/remisiones/reporte-sin-precio?fechas=2031-08-05",
                     headers=h).json()
    motivos = {p["motivo"] for p in out["productos"]}
    assert "CLAVE NO EN SAE" in motivos, out
    assert out["clave_fuera_de_sae"] == 1


def test_cancelar_deja_dicho_por_que(client, env, auth_as):
    """«Nada se elimina: se cancela y se deja nota». El motivo viaja en la
    cancelación y no en un PATCH previo, porque editar una remisión exportada
    devuelve 409 justo donde saber el porqué importa más."""
    from app.models.remision import Remision

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    rem = _create_rem(client, h, env, "2", "5").json()
    with SessionLocal() as s:
        r = s.query(Remision).filter(Remision.id == uuid.UUID(rem["id"])).one()
        r.export_pedido_at = datetime.now(timezone.utc)   # ya salió en un masivo
        s.commit()
    # el PATCH está cerrado para esta remisión…
    assert client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                        json={"notas": "x"}).status_code == 409
    # …y la cancelación con motivo sí pasa, y lo deja escrito
    r2 = client.post(f"/api/v1/remisiones/{rem['id']}/cancelar", headers=h,
                     json={"motivo": "el cliente ya no necesita el producto"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["estado"] == "CANCELADA"
    assert "el cliente ya no necesita el producto" in (r2.json()["notas"] or "")
    assert "Cancelada:" in (r2.json()["notas"] or "")

    # sin cuerpo sigue funcionando igual que siempre
    otra = _create_rem(client, h, env, "1", "5").json()
    assert client.post(f"/api/v1/remisiones/{otra['id']}/cancelar",
                       headers=h).status_code == 200
