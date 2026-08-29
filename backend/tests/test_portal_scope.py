"""Portal de cliente: candado por cliente (memberships.cliente_scope), rol
preset PORTAL CLIENTE, split gestionar/eliminar y asignación de empresas.

El candado es independiente de los permisos: aunque el rol vea el menú de
remisiones/facturas, SOLO ve documentos de sus clientes — y lo ajeno responde
404 (ni siquiera confirma que existe).
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.core.rbac import invalidate_auth_cache
from app.main import app
from app.models import (
    Cliente, Factura, ListaAsignacion, ListaPrecios, Membership, Precio,
    Producto, Role, Tenant, User,
)

_PURGE = (
    "lineas_factura", "facturas", "lineas_remision", "remisiones",
    "lista_asignaciones", "precios", "listas_precios", "productos", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": [], "roles": []}
    try:
        tenant = Tenant(slug=f"portal-{suffix}", legal_name="Portal SA", rfc=f"P{suffix.upper()}Y"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        tid = tenant.id
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        portal_role = db.query(Role).filter(Role.nombre == "PORTAL CLIENTE", Role.es_preset.is_(True)).one()

        cli_a = Cliente(tenant_id=tid, codigo="CA", legal_name="Cliente A SA", rfc="XAXX010101000")
        cli_b = Cliente(tenant_id=tid, codigo="CB", legal_name="Cliente B SA", rfc="XEXX010101000")
        db.add_all([cli_a, cli_b]); db.flush()

        def _user(role, label, scope=None):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tid, user_id=u.id, role_id=role.id, cliente_scope=scope)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tid,
                    "membership_id": str(m.id), "user_id": u.id}

        admin = _user(admin_role, "admin")
        portal = _user(portal_role, "portal", scope=[cli_a.id])

        prod_a = Producto(tenant_id=tid, sku="MANZANA", nombre="Manzana", clave_sat="50300000",
                          unidad_sat="KGM", unidad_base="KILO", presentaciones={"KILO": {}})
        prod_x = Producto(tenant_id=tid, sku="PERA", nombre="Pera", clave_sat="50300000",
                          unidad_sat="KGM", unidad_base="KILO", presentaciones={"KILO": {}})
        lista = ListaPrecios(tenant_id=tid, codigo="NEG-A", nombre="Negociada A")
        ajena = ListaPrecios(tenant_id=tid, codigo="AJENA", nombre="Lista ajena")
        db.add_all([prod_a, prod_x, lista, ajena]); db.flush()
        db.add(Precio(tenant_id=tid, lista_id=lista.id, producto_id=prod_a.id, presentacion="KILO",
                      precio_unitario=Decimal("30"), cantidad_minima=1))
        db.add(ListaAsignacion(tenant_id=tid, lista_id=lista.id, cliente_id=cli_a.id))

        fa = Factura(tenant_id=tid, serie="AA", folio=1, cliente_id=cli_a.id,
                     estado="TIMBRADA", origen="ESPEJO_SAE", espejo_empresa="02",
                     subtotal=100, total=116)
        fb = Factura(tenant_id=tid, serie="AA", folio=2, cliente_id=cli_b.id,
                     estado="TIMBRADA", origen="ESPEJO_SAE", espejo_empresa="02",
                     subtotal=100, total=116)
        db.add_all([fa, fb])
        db.commit()
        invalidate_auth_cache()

        yield {"admin": admin, "portal": portal, "tenant": tid,
               "lista": str(lista.id), "ajena": str(ajena.id),
               "cli_a": str(cli_a.id), "cli_b": str(cli_b.id),
               "prod_a": str(prod_a.id), "prod_x": str(prod_x.id),
               "fact_a": str(fa.id), "fact_b": str(fb.id)}
    finally:
        for table in _PURGE:
            for t in created["tenants"]:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": t})
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for rid in created["roles"]:
            db.execute(text("DELETE FROM role_permissions WHERE role_id = :r"), {"r": rid})
            db.query(Role).filter(Role.id == rid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for t in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == t).delete()
        db.commit(); db.close()
        invalidate_auth_cache()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"], email=user["email"], role="authenticated", claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _rem(client, h, cliente_id, prod_id, su_pedido=None):
    r = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": cliente_id, "su_pedido": su_pedido,
        "lineas": [{"producto_id": prod_id, "cantidad_solicitada": 1, "precio_unitario": 10}]})
    assert r.status_code == 201, r.text
    return r.json()


def test_candado_filtra_clientes_remisiones_y_facturas(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem_a = _rem(client, h, env["cli_a"], env["prod_a"])
    rem_b = _rem(client, h, env["cli_b"], env["prod_a"])

    auth_as(env["portal"]); hp = _hdr(env["portal"])
    clientes = client.get("/api/v1/clientes", headers=hp).json()
    assert [c["id"] for c in clientes["items"]] == [env["cli_a"]]

    rems = client.get("/api/v1/remisiones", headers=hp).json()
    ids = {r["id"] for r in rems["items"]}
    assert rem_a["id"] in ids and rem_b["id"] not in ids
    assert client.get(f"/api/v1/remisiones/{rem_b['id']}", headers=hp).status_code == 404
    assert client.get(f"/api/v1/remisiones/{rem_a['id']}", headers=hp).status_code == 200

    facts = client.get("/api/v1/facturas", headers=hp).json()
    fids = {f["id"] for f in facts["items"]}
    assert env["fact_a"] in fids and env["fact_b"] not in fids
    assert client.get(f"/api/v1/facturas/{env['fact_b']}", headers=hp).status_code == 404


def test_portal_es_solo_lectura(client, env, auth_as):
    auth_as(env["portal"]); hp = _hdr(env["portal"])
    r = client.post("/api/v1/remisiones", headers=hp, json={
        "cliente_facturacion_id": env["cli_a"],
        "lineas": [{"producto_id": env["prod_a"], "cantidad_solicitada": 1, "precio_unitario": 1}]})
    assert r.status_code == 403
    assert client.get("/api/v1/productos", headers=hp).status_code == 403


def test_cotizables_respeta_lista_y_candado(client, env, auth_as):
    auth_as(env["portal"]); hp = _hdr(env["portal"])
    # cliente fuera del candado → 403
    r = client.get("/api/v1/precios/productos-cotizables", headers=hp,
                   params={"cliente_id": env["cli_b"]})
    assert r.status_code == 403
    # el suyo: SOLO lo que está en su lista (Manzana sí, Pera no)
    r = client.get("/api/v1/precios/productos-cotizables", headers=hp,
                   params={"cliente_id": env["cli_a"]})
    assert r.status_code == 200
    ids = {i["producto_id"] for i in r.json()["items"]}
    assert ids == {env["prod_a"]}
    assert r.json()["limitado"] is True
    # y el cotizar unitario también respeta el candado
    r = client.get("/api/v1/precios/cotizar", headers=hp,
                   params={"producto_id": env["prod_a"], "cliente_id": env["cli_b"]})
    assert r.status_code == 403


def test_eliminar_separado_de_gestionar(client, env, auth_as):
    """Un rol con producto:gestionar pero SIN producto:eliminar edita pero no
    borra; el ADMIN preset recibió :eliminar en la migración y sí puede."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rol = client.post("/api/v1/roles", headers=h, json={
        "nombre": f"EDITOR-{uuid.uuid4().hex[:6]}", "descripcion": None,
        "permissions": ["menu:productos", "producto:gestionar"]}).json()

    db = SessionLocal()
    try:
        sub = f"sub-editor-{uuid.uuid4().hex[:8]}"
        u = User(email=f"{sub}@t.test", auth_user_id=sub)
        db.add(u); db.flush()
        m = Membership(tenant_id=env["tenant"], user_id=u.id, role_id=uuid.UUID(rol["id"]))
        db.add(m); db.commit()
        editor = {"sub": sub, "email": u.email, "tenant_id": env["tenant"]}
        uid, mid = u.id, m.id
    finally:
        db.close()
    invalidate_auth_cache()

    try:
        auth_as(editor); he = _hdr(editor)
        assert client.patch(f"/api/v1/productos/{env['prod_x']}", headers=he,
                            json={"nombre": "Pera verde"}).status_code == 200
        assert client.delete(f"/api/v1/productos/{env['prod_x']}", headers=he).status_code == 403
        auth_as(env["admin"])
        assert client.delete(f"/api/v1/productos/{env['prod_x']}", headers=h).status_code == 204
    finally:
        db = SessionLocal()
        try:
            db.query(Membership).filter(Membership.id == mid).delete()
            db.query(User).filter(User.id == uid).delete()
            db.execute(text("DELETE FROM role_permissions WHERE role_id = :r"), {"r": rol["id"]})
            db.execute(text("DELETE FROM roles WHERE id = :r"), {"r": rol["id"]})
            db.commit()
        finally:
            db.close()
        invalidate_auth_cache()


def test_empresas_del_usuario(client, env, auth_as):
    """Sin grupo, la lista trae solo la empresa actual; quitar el acceso borra
    la membresía (y nadie puede tocarse a sí mismo)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    mid = env["portal"]["membership_id"]
    r = client.get(f"/api/v1/memberships/{mid}/empresas", headers=h).json()
    assert len(r["empresas"]) == 1
    emp = r["empresas"][0]
    assert emp["es_actual"] is True and emp["tiene_acceso"] is True
    assert emp["role_nombre"] == "PORTAL CLIENTE"

    # nadie se toca a sí mismo
    propio = env["admin"]["membership_id"]
    r = client.put(f"/api/v1/memberships/{propio}/empresas", headers=h,
                   json={"tenant_id": str(env["tenant"]), "acceso": False})
    assert r.status_code == 409

    # quitar el acceso del portal a la empresa actual borra su membresía
    r = client.put(f"/api/v1/memberships/{mid}/empresas", headers=h,
                   json={"tenant_id": str(env["tenant"]), "acceso": False})
    assert r.status_code == 200
    quedan = client.get("/api/v1/memberships", headers=h).json()
    assert mid not in {m["id"] for m in quedan}


def test_scope_editable_por_patch(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    mid = env["portal"]["membership_id"]
    # ampliar el candado a los dos clientes
    r = client.patch(f"/api/v1/memberships/{mid}", headers=h,
                     json={"cliente_scope": [env["cli_a"], env["cli_b"]]})
    assert r.status_code == 200
    assert set(r.json()["cliente_scope"]) == {env["cli_a"], env["cli_b"]}
    # cliente inexistente → 422
    r = client.patch(f"/api/v1/memberships/{mid}", headers=h,
                     json={"cliente_scope": [str(uuid.uuid4())]})
    assert r.status_code == 422
    # [] = sin candado
    r = client.patch(f"/api/v1/memberships/{mid}", headers=h, json={"cliente_scope": []})
    assert r.json()["cliente_scope"] is None


def test_descarga_de_listas_del_portal(client, env, auth_as):
    """El usuario del portal baja el PDF/Excel de las listas de SUS clientes;
    una lista no asignada a ellos responde 403."""
    auth_as(env["portal"]); hp = _hdr(env["portal"])
    r = client.get(f"/api/v1/listas-precios/{env['lista']}/pdf", headers=hp)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = client.get(f"/api/v1/listas-precios/{env['lista']}/export", headers=hp)
    assert r.status_code == 200
    assert client.get(f"/api/v1/listas-precios/{env['ajena']}/pdf", headers=hp).status_code == 403
    # y el CRUD de listas le sigue cerrado (no tiene menu:listas_precios)
    assert client.get("/api/v1/listas-precios", headers=hp).status_code == 403


def test_fugas_cerradas_cobranza_y_directorio(client, env, auth_as):
    """Los hoyos que encontró la revisión adversarial (29-ago): estado de
    cuenta, REPs, equivalencias, catálogo por IDOR, resolutor, validar-rfc,
    conexiones y preview-totales — todos bajo el candado."""
    auth_as(env["portal"]); hp = _hdr(env["portal"])

    # estado de cuenta: el suyo sí, el ajeno 404
    assert client.get(f"/api/v1/cobranza/estado-cuenta/{env['cli_a']}", headers=hp).status_code == 200
    assert client.get(f"/api/v1/cobranza/estado-cuenta/{env['cli_b']}", headers=hp).status_code == 404

    # catálogo del cliente por IDOR
    assert client.get(f"/api/v1/clientes/{env['cli_a']}/catalogo", headers=hp).status_code == 200
    assert client.get(f"/api/v1/clientes/{env['cli_b']}/catalogo", headers=hp).status_code == 404

    # recibos: la lista viene filtrada (vacía aquí) y no truena
    r = client.get("/api/v1/cobranza/recibos-pago", headers=hp)
    assert r.status_code == 200 and r.json()["total"] == 0

    # sondas cerradas para el portal
    assert client.post("/api/v1/clientes/resolver", headers=hp, json={}).status_code == 403
    assert client.get("/api/v1/clientes/validar-rfc", headers=hp,
                      params={"rfc": "XAXX010101000"}).status_code == 403
    assert client.get("/api/v1/conexiones/probar", headers=hp).status_code == 403
    assert client.post("/api/v1/conexiones/grupos", headers=hp,
                       json={"grupos": []}).status_code == 403
    assert client.post("/api/v1/remisiones/preview-totales", headers=hp,
                       json={"lineas": [{"producto_id": env["prod_a"], "cantidad": "1",
                                          "precio_unitario": "1"}]}).status_code == 403

    # externos: solo las claves de SUS clientes
    r = client.get("/api/v1/clientes/externos", headers=hp)
    assert r.status_code == 200
    cuerpo = r.json()
    filas = cuerpo["items"] if isinstance(cuerpo, dict) else cuerpo
    ajenos = [x for x in filas if x["cliente_id"] != env["cli_a"]]
    assert ajenos == []
