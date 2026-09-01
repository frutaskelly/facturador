"""End-to-end catalog CRUD through the real dependency chain.

Only JWT verification is stubbed (override `get_principal`); everything else is
real: `get_auth_context` resolves the tenant/role/permissions from the DB, and
`get_tenant_db` runs each request on an RLS-scoped `app_user` session. So these
tests cover authorization gating, CRUD, FK integrity, conflict handling, AND
tenant isolation through the full stack — not just unit-level checks.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import EsquemaImpuesto, Membership, Role, Tenant, User

# Tables to purge (FK order) for the two test tenants at teardown.
_CATALOG_TABLES = (
    "precios",
    "productos",
    "listas_precios",
    "clientes",
    "categorias_producto",
    "esquemas_impuesto",
)


def _esquema(tenant_id) -> str:
    """Un esquema de impuesto para el tenant, y devuelve su id.

    Dar de alta un producto sin esquema está prohibido (nacería sin IVA), así
    que toda alta de estas pruebas elige uno. Se crea bajo demanda y NO en el
    fixture: hay pruebas que CUENTAN los esquemas del tenant y sembrarlo ahí
    les cambiaba el total. Idempotente por si una prueba lo pide dos veces."""
    db = SessionLocal()
    try:
        esq = db.query(EsquemaImpuesto).filter(
            EsquemaImpuesto.tenant_id == tenant_id, EsquemaImpuesto.codigo == "FIXT").first()
        if esq is None:
            esq = EsquemaImpuesto(tenant_id=tenant_id, codigo="FIXT", nombre="IVA 0% de prueba")
            db.add(esq); db.commit()
        return str(esq.id)
    finally:
        db.close()


@pytest.fixture
def env(db_engine):
    """Two tenants; tenant A has an ADMIN + a TOMADOR, tenant B an ADMIN."""
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        def _tenant(slug_suffix):
            t = Tenant(
                slug=f"cat-{slug_suffix}-{suffix}",
                legal_name=f"Cat {slug_suffix} SA",
                rfc=f"C{slug_suffix.upper()}{suffix.upper()}"[:13],
                regimen_fiscal_sat="601",
                domicilio_fiscal_cp="44100",
                tier="PRINCIPAL",
                status="ACTIVE",
            )
            db.add(t)
            db.flush()
            created["tenants"].append(t.id)
            return t

        tenant_a = _tenant("a")
        tenant_b = _tenant("b")

        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        tomador_role = db.query(Role).filter(Role.nombre == "TOMADOR", Role.es_preset.is_(True)).one()

        def _user(tenant, role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u)
            db.flush()
            created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
            db.add(m)
            db.flush()
            created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        admin_a = _user(tenant_a, admin_role, "admin-a")
        tomador_a = _user(tenant_a, tomador_role, "tomador-a")
        admin_b = _user(tenant_b, admin_role, "admin-b")
        db.commit()

        yield {
            "tenant_a": tenant_a.id,
            "tenant_b": tenant_b.id,
            "admin_a": admin_a,
            "tomador_a": tomador_a,
            "admin_b": admin_b,
        }
    finally:
        # Purge catalog rows for both tenants (owner session bypasses RLS).
        for table in _CATALOG_TABLES:
            for tid in created["tenants"]:
                db.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :tid"),
                    {"tid": tid},
                )
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit()
        db.close()


@pytest.fixture
def auth_as():
    """Return a setter that makes the next requests authenticate as a user."""
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"],
            email=user["email"],
            role="authenticated",
            claims={"sub": user["sub"]},
        )
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(user):
    return {"X-Tenant-Id": str(user["tenant_id"])}


# ─── authorization gating ────────────────────────────────────────────────────
def test_tomador_can_read_productos_but_not_manage(client, env, auth_as):
    auth_as(env["tomador_a"])
    h = _hdr(env["tomador_a"])

    # menu:productos → list allowed
    assert client.get("/api/v1/productos", headers=h).status_code == 200

    # producto:gestionar → create forbidden
    r = client.post(
        "/api/v1/productos",
        headers=h,
        json={"sku": "91000001", "nombre": "X", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"},
    )
    assert r.status_code == 403


def test_tomador_lacks_categorias_menu(client, env, auth_as):
    auth_as(env["tomador_a"])
    # TOMADOR has no menu:productos.categorias → even reads are denied.
    assert client.get("/api/v1/categorias", headers=_hdr(env["tomador_a"])).status_code == 403


# ─── CRUD happy path ─────────────────────────────────────────────────────────
def test_admin_full_crud_categoria(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    r = client.post("/api/v1/categorias", headers=h, json={"codigo": "FRU", "nombre": "Frutas"})
    assert r.status_code == 201, r.text
    cat = r.json()
    assert cat["tenant_id"] == str(env["tenant_a"])
    cat_id = cat["id"]

    r = client.get(f"/api/v1/categorias/{cat_id}", headers=h)
    assert r.status_code == 200 and r.json()["nombre"] == "Frutas"

    r = client.patch(f"/api/v1/categorias/{cat_id}", headers=h, json={"nombre": "Frutas y Verduras"})
    assert r.status_code == 200 and r.json()["nombre"] == "Frutas y Verduras"

    r = client.get("/api/v1/categorias", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 1

    assert client.delete(f"/api/v1/categorias/{cat_id}", headers=h).status_code == 204
    # Soft-deleted → gone from reads.
    assert client.get(f"/api/v1/categorias/{cat_id}", headers=h).status_code == 404
    assert client.get("/api/v1/categorias", headers=h).json()["total"] == 0


def test_categoria_codigo_autogenerado(client, env, auth_as):
    # Convención del proyecto: el código se genera del nombre en el servidor
    # (services/categoria_codigo.py); si el cliente manda uno, se respeta.
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    # Sin código → se deriva del nombre ("Lácteos" → "LACTE").
    r0 = client.post("/api/v1/categorias", headers=h, json={"nombre": "Lácteos"})
    assert r0.status_code == 201, r0.text
    assert r0.json()["codigo"] == "LACTE"
    # Mismo prefijo → sufijo anticolisión, no 409.
    r1 = client.post("/api/v1/categorias", headers=h, json={"nombre": "Lacteos Premium"})
    assert r1.status_code == 201, r1.text
    assert r1.json()["codigo"] == "LACTE2"
    # Código explícito duplicado → 409.
    r2 = client.post("/api/v1/categorias", headers=h, json={"codigo": "LACTE", "nombre": "Otra"})
    assert r2.status_code == 409, r2.text


def test_producto_with_valid_and_invalid_fk(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    cat = client.post("/api/v1/categorias", headers=h, json={"codigo": "C1", "nombre": "Cat"}).json()

    # Valid in-tenant FK → 201.
    r = client.post(
        "/api/v1/productos",
        headers=h,
        json={
            "sku": "91000011", "nombre": "Prod 1", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101",
            "unidad_sat": "KGM", "categoria_id": cat["id"],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["categoria_id"] == cat["id"]

    # Bogus FK → 422 (re-validated under tenant scope).
    r = client.post(
        "/api/v1/productos",
        headers=h,
        json={
            "sku": "91000012", "nombre": "Prod 2", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101",
            "unidad_sat": "KGM", "categoria_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 422


def test_nested_precios_crud(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    prod = client.post(
        "/api/v1/productos", headers=h,
        json={"sku": "91000019", "nombre": "Prod 9", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"},
    ).json()
    lista = client.post("/api/v1/listas-precios", headers=h, json={"codigo": "L1", "nombre": "Lista 1"}).json()

    r = client.post(
        f"/api/v1/listas-precios/{lista['id']}/precios",
        headers=h,
        json={"producto_id": prod["id"], "precio_unitario": "123.45", "cantidad_minima": 1},
    )
    assert r.status_code == 201, r.text
    precio = r.json()
    assert precio["lista_id"] == lista["id"]
    assert precio["tenant_id"] == str(env["tenant_a"])

    r = client.get(f"/api/v1/listas-precios/{lista['id']}/precios", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 1

    assert client.delete(
        f"/api/v1/listas-precios/{lista['id']}/precios/{precio['id']}", headers=h
    ).status_code == 204
    assert client.get(f"/api/v1/listas-precios/{lista['id']}/precios", headers=h).json()["total"] == 0


# ─── tenant isolation through the full stack ─────────────────────────────────
def test_tenant_b_cannot_see_tenant_a_categoria(client, env, auth_as):
    auth_as(env["admin_a"])
    cat = client.post(
        "/api/v1/categorias", headers=_hdr(env["admin_a"]), json={"codigo": "SECRET", "nombre": "A only"}
    ).json()

    # Switch to tenant B's admin: the row is invisible (RLS), so 404.
    auth_as(env["admin_b"])
    assert client.get(f"/api/v1/categorias/{cat['id']}", headers=_hdr(env["admin_b"])).status_code == 404
    assert client.get("/api/v1/categorias", headers=_hdr(env["admin_b"])).json()["total"] == 0


def test_cross_tenant_fk_is_rejected(client, env, auth_as):
    # Tenant A creates a categoria.
    auth_as(env["admin_a"])
    cat = client.post(
        "/api/v1/categorias", headers=_hdr(env["admin_a"]), json={"codigo": "AX", "nombre": "A cat"}
    ).json()

    # Tenant B tries to reference it → not visible in B's scope → 422.
    auth_as(env["admin_b"])
    r = client.post(
        "/api/v1/productos",
        headers=_hdr(env["admin_b"]),
        json={
            "sku": "91000020", "nombre": "B prod", "esquema_impuesto_id": _esquema(env["tenant_b"]), "clave_sat": "01010101",
            "unidad_sat": "KGM", "categoria_id": cat["id"],
        },
    )
    assert r.status_code == 422


# ─── esquemas de impuesto ────────────────────────────────────────────────────
def test_admin_full_crud_esquema(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    r = client.post(
        "/api/v1/esquemas-impuesto",
        headers=h,
        json={"codigo": "IVA16", "nombre": "IVA 16%", "iva_tasa": "0.16"},
    )
    assert r.status_code == 201, r.text
    esq = r.json()
    assert esq["tenant_id"] == str(env["tenant_a"])
    assert float(esq["iva_tasa"]) == 0.16
    eid = esq["id"]

    assert client.get(f"/api/v1/esquemas-impuesto/{eid}", headers=h).status_code == 200

    r = client.patch(f"/api/v1/esquemas-impuesto/{eid}", headers=h, json={"nombre": "IVA dieciséis"})
    assert r.status_code == 200 and r.json()["nombre"] == "IVA dieciséis"

    assert client.get("/api/v1/esquemas-impuesto", headers=h).json()["total"] == 1

    assert client.delete(f"/api/v1/esquemas-impuesto/{eid}", headers=h).status_code == 204
    assert client.get(f"/api/v1/esquemas-impuesto/{eid}", headers=h).status_code == 404
    assert client.get("/api/v1/esquemas-impuesto", headers=h).json()["total"] == 0


def test_esquema_codigo_autogenerado(client, env, auth_as):
    # El código de esquema se autogenera (ESQ-NN) e ignora el del cliente, así
    # que dos altas con el mismo código de entrada NO chocan: cada una recibe
    # un código secuencial único.
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    r1 = client.post("/api/v1/esquemas-impuesto", headers=h, json={"codigo": "DUP", "nombre": "Uno"})
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/v1/esquemas-impuesto", headers=h, json={"codigo": "DUP", "nombre": "Dos"})
    assert r2.status_code == 201, r2.text
    c1, c2 = r1.json()["codigo"], r2.json()["codigo"]
    assert c1 != c2
    assert c1.startswith("ESQ-") and c2.startswith("ESQ-")


def test_esquema_rate_out_of_range_422(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    # iva_tasa is a fraction in [0, 1]; 1.5 (== 150%) must be rejected.
    r = client.post(
        "/api/v1/esquemas-impuesto", headers=h, json={"codigo": "BAD", "nombre": "x", "iva_tasa": "1.5"}
    )
    assert r.status_code == 422


def test_tomador_lacks_esquemas_menu(client, env, auth_as):
    auth_as(env["tomador_a"])
    # TOMADOR has no menu:esquemas_impuesto → even reads are denied.
    assert client.get("/api/v1/esquemas-impuesto", headers=_hdr(env["tomador_a"])).status_code == 403


def test_tenant_b_cannot_see_tenant_a_esquema(client, env, auth_as):
    auth_as(env["admin_a"])
    esq = client.post(
        "/api/v1/esquemas-impuesto",
        headers=_hdr(env["admin_a"]),
        json={"codigo": "AONLY", "nombre": "A only"},
    ).json()

    auth_as(env["admin_b"])
    assert client.get(
        f"/api/v1/esquemas-impuesto/{esq['id']}", headers=_hdr(env["admin_b"])
    ).status_code == 404
    assert client.get("/api/v1/esquemas-impuesto", headers=_hdr(env["admin_b"])).json()["total"] == 0


# ─── clientes ────────────────────────────────────────────────────────────────
def test_admin_full_crud_cliente(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    r = client.post(
        "/api/v1/clientes",
        headers=h,
        json={"codigo": "CLI1", "legal_name": "Cliente Uno SA", "rfc": "XAXX010101000"},
    )
    assert r.status_code == 201, r.text
    cli = r.json()
    assert cli["tenant_id"] == str(env["tenant_a"])
    # Accumulators default to zero and are never client-supplied.
    assert float(cli["saldo_actual"]) == 0
    cid = cli["id"]

    assert client.get(f"/api/v1/clientes/{cid}", headers=h).status_code == 200

    r = client.patch(f"/api/v1/clientes/{cid}", headers=h, json={"legal_name": "Cliente Uno SA de CV"})
    assert r.status_code == 200 and r.json()["legal_name"] == "Cliente Uno SA de CV"

    assert client.get("/api/v1/clientes", headers=h).json()["total"] == 1

    assert client.delete(f"/api/v1/clientes/{cid}", headers=h).status_code == 204
    assert client.get(f"/api/v1/clientes/{cid}", headers=h).status_code == 404
    assert client.get("/api/v1/clientes", headers=h).json()["total"] == 0


def test_cliente_codigo_autogenerado(client, env, auth_as):
    """El código de cliente se genera server-side (regla global); el código
    enviado por el cliente se ignora y cada alta obtiene uno único."""
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    a = client.post("/api/v1/clientes", headers=h,
                    json={"codigo": "DUP", "legal_name": "Uno", "rfc": "XAXX010101000"})
    b = client.post("/api/v1/clientes", headers=h,
                    json={"codigo": "DUP", "legal_name": "Dos", "rfc": "XEXX010101000"})
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    ca, cb = a.json()["codigo"], b.json()["codigo"]
    assert ca and cb and ca != cb  # auto-generados y únicos


def test_asignacion_de_lista_a_cliente(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    lista = client.post(
        "/api/v1/listas-precios", headers=h, json={"codigo": "LP", "nombre": "Lista P"}
    ).json()
    cli = client.post(
        "/api/v1/clientes", headers=h,
        json={"legal_name": "Con lista", "rfc": "XAXX010101000"},
    ).json()

    # La lista se le asigna al cliente por su renglón de asignación, no por una
    # columna del cliente: FK válida dentro del inquilino → 201.
    r = client.post(
        "/api/v1/asignaciones-precios", headers=h,
        json={"lista_id": lista["id"], "cliente_id": cli["id"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["especificidad"] == 1        # sólo cliente = global del cliente
    assert r.json()["cliente_nombre"] == "Con lista"

    # FK inventada → 422.
    r = client.post(
        "/api/v1/asignaciones-precios", headers=h,
        json={"lista_id": str(uuid.uuid4()), "cliente_id": cli["id"]},
    )
    assert r.status_code == 422

    # Sin ninguna dimensión → 422: eso sería "la lista base", que tiene su
    # propio lugar (es_default) y no se dice de dos maneras.
    r = client.post(
        "/api/v1/asignaciones-precios", headers=h, json={"lista_id": lista["id"]},
    )
    assert r.status_code == 422


def test_tomador_can_read_clientes_but_not_manage(client, env, auth_as):
    auth_as(env["tomador_a"])
    h = _hdr(env["tomador_a"])
    # TOMADOR has menu:clientes → list allowed.
    assert client.get("/api/v1/clientes", headers=h).status_code == 200
    # ...but no cliente:gestionar → create forbidden.
    r = client.post("/api/v1/clientes", headers=h, json={"legal_name": "X", "rfc": "XAXX010101000"})
    assert r.status_code == 403


def test_tenant_b_cannot_see_tenant_a_cliente(client, env, auth_as):
    auth_as(env["admin_a"])
    cli = client.post(
        "/api/v1/clientes",
        headers=_hdr(env["admin_a"]),
        json={"codigo": "SECRET", "legal_name": "A only", "rfc": "XAXX010101000"},
    ).json()

    auth_as(env["admin_b"])
    assert client.get(f"/api/v1/clientes/{cli['id']}", headers=_hdr(env["admin_b"])).status_code == 404
    assert client.get("/api/v1/clientes", headers=_hdr(env["admin_b"])).json()["total"] == 0


# ─── productos: update / delete / dup / filters ──────────────────────────────
def test_producto_update_swaps_and_clears_fk(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    cat1 = client.post("/api/v1/categorias", headers=h, json={"codigo": "C1", "nombre": "Uno"}).json()
    cat2 = client.post("/api/v1/categorias", headers=h, json={"codigo": "C2", "nombre": "Dos"}).json()
    prod = client.post(
        "/api/v1/productos", headers=h,
        json={"sku": "91000030", "nombre": "Prod U", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101",
              "unidad_sat": "KGM", "categoria_id": cat1["id"]},
    ).json()
    assert prod["categoria_id"] == cat1["id"]

    # Swap to a valid in-tenant categoria → 200.
    r = client.patch(f"/api/v1/productos/{prod['id']}", headers=h, json={"categoria_id": cat2["id"]})
    assert r.status_code == 200 and r.json()["categoria_id"] == cat2["id"]

    # Swap to a bogus categoria → 422 (re-validated under tenant scope).
    r = client.patch(f"/api/v1/productos/{prod['id']}", headers=h, json={"categoria_id": str(uuid.uuid4())})
    assert r.status_code == 422

    # Explicit null clears the optional FK → 200, categoria_id None.
    r = client.patch(f"/api/v1/productos/{prod['id']}", headers=h, json={"categoria_id": None})
    assert r.status_code == 200 and r.json()["categoria_id"] is None


def test_producto_duplicate_sku_conflicts(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    body = {"sku": "91000040", "nombre": "Uno", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"}
    assert client.post("/api/v1/productos", headers=h, json=body).status_code == 201
    # Mismo SKU con otro nombre: el 409 viene del UNIQUE de SKU, no del
    # detector de duplicados por nombre.
    body2 = dict(body, nombre="Dos")
    assert client.post("/api/v1/productos", headers=h, json=body2).status_code == 409


def test_producto_no_nace_sin_esquema_de_impuesto(client, env, auth_as):
    """Sin esquema el producto no lleva IVA y su CFDI sale mal. La regla vive en
    el endpoint, así que la cumplen TODAS las puertas: el alta manual y el alta
    rápida desde una remisión (por donde entraron 22 sin esquema)."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    r = client.post("/api/v1/productos", headers=h, json={
        "sku": "91000060", "nombre": "SIN ESQUEMA", "clave_sat": "01010101",
        "unidad_sat": "KGM"})
    assert r.status_code == 422
    assert "esquema de impuesto" in r.json()["detail"]
    assert client.get("/api/v1/productos", headers=h).json()["total"] == 0


def test_producto_no_se_queda_sin_esquema_al_editar(client, env, auth_as):
    """La otra puerta: quitárselo a uno que ya lo tiene. Se puede CAMBIAR, no
    vaciar."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    prod = client.post("/api/v1/productos", headers=h, json={
        "sku": "91000061", "nombre": "CON ESQUEMA",
        "esquema_impuesto_id": _esquema(env["tenant_a"]),
        "clave_sat": "01010101", "unidad_sat": "KGM"}).json()

    r = client.patch(f"/api/v1/productos/{prod['id']}", headers=h,
                     json={"esquema_impuesto_id": None})
    assert r.status_code == 422
    assert "esquema de impuesto" in r.json()["detail"]

    # Cambiarlo por otro sí.
    otro = client.post("/api/v1/esquemas-impuesto", headers=h, json={
        "codigo": "OTRO", "nombre": "IVA 16%", "iva_tasa": "0.16"}).json()
    r = client.patch(f"/api/v1/productos/{prod['id']}", headers=h,
                     json={"esquema_impuesto_id": otro["id"]})
    assert r.status_code == 200 and r.json()["esquema_impuesto_id"] == otro["id"]


def test_producto_sin_categoria_cae_en_sin_categorizar(client, env, auth_as):
    """Un producto sin categoría no se queda en un hueco invisible: cae en la
    categoría por defecto del sistema, y así se puede listar y repartir desde
    la pantalla de Categorías."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    prod = client.post("/api/v1/productos", headers=h, json={
        "sku": "91000062", "nombre": "SIN CATEGORIA",
        "esquema_impuesto_id": _esquema(env["tenant_a"]),
        "clave_sat": "01010101", "unidad_sat": "KGM"}).json()
    assert prod["categoria_id"] is not None

    cats = client.get("/api/v1/categorias", headers=h).json()["items"]
    default = next(c for c in cats if c["id"] == prod["categoria_id"])
    assert default["nombre"] == "Sin categorizar"
    # Se crea UNA sola vez: el segundo producto reusa la misma. Nombre bien
    # distinto a propósito — parecido dispara el detector de duplicados (409).
    r = client.post("/api/v1/productos", headers=h, json={
        "sku": "91000063", "nombre": "MANGO ATAULFO",
        "esquema_impuesto_id": _esquema(env["tenant_a"]),
        "clave_sat": "01010101", "unidad_sat": "KGM"})
    assert r.status_code == 201, r.text
    assert r.json()["categoria_id"] == prod["categoria_id"]
    assert sum(1 for c in cats if c["nombre"] == "Sin categorizar") == 1


def test_producto_con_categoria_no_la_pierde(client, env, auth_as):
    """El default es solo para el hueco: la categoría elegida se respeta."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    cat = client.post("/api/v1/categorias", headers=h,
                      json={"codigo": "CX", "nombre": "Frutas"}).json()
    prod = client.post("/api/v1/productos", headers=h, json={
        "sku": "91000064", "nombre": "CON CATEGORIA", "categoria_id": cat["id"],
        "esquema_impuesto_id": _esquema(env["tenant_a"]),
        "clave_sat": "01010101", "unidad_sat": "KGM"}).json()
    assert prod["categoria_id"] == cat["id"]


def test_producto_soft_delete(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    prod = client.post(
        "/api/v1/productos", headers=h,
        json={"sku": "91000050", "nombre": "Borrar", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"},
    ).json()
    assert client.get("/api/v1/productos", headers=h).json()["total"] == 1
    assert client.delete(f"/api/v1/productos/{prod['id']}", headers=h).status_code == 204
    assert client.get(f"/api/v1/productos/{prod['id']}", headers=h).status_code == 404
    assert client.get("/api/v1/productos", headers=h).json()["total"] == 0


def test_producto_list_filters(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    cat = client.post("/api/v1/categorias", headers=h, json={"codigo": "FIL", "nombre": "Fil"}).json()
    client.post("/api/v1/productos", headers=h, json={
        "sku": "91000061", "nombre": "Manzana roja", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101",
        "unidad_sat": "KGM", "categoria_id": cat["id"], "activo": True})
    client.post("/api/v1/productos", headers=h, json={
        "sku": "91000062", "nombre": "Pera verde", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101",
        "unidad_sat": "KGM", "activo": False})

    # q matches name OR sku.
    r = client.get("/api/v1/productos", headers=h, params={"q": "manzana"})
    assert r.json()["total"] == 1 and r.json()["items"][0]["sku"] == "91000061"

    # categoria_id filter.
    r = client.get("/api/v1/productos", headers=h, params={"categoria_id": cat["id"]})
    assert r.json()["total"] == 1 and r.json()["items"][0]["sku"] == "91000061"

    # activo filter.
    assert client.get("/api/v1/productos", headers=h, params={"activo": "false"}).json()["total"] == 1
    assert client.get("/api/v1/productos", headers=h, params={"activo": "true"}).json()["total"] == 1


def test_producto_missing_required_field_422(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    # Missing clave_sat / unidad_sat (both required for CFDI 4.0).
    r = client.post("/api/v1/productos", headers=h, json={"sku": "91000070", "nombre": "X"})
    assert r.status_code == 422


# ─── listas de precios: list CRUD + filters ──────────────────────────────────
def test_admin_full_crud_lista(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])

    r = client.post("/api/v1/listas-precios", headers=h, json={"codigo": "L1", "nombre": "Lista 1"})
    assert r.status_code == 201, r.text
    lista = r.json()
    assert lista["tenant_id"] == str(env["tenant_a"])
    assert lista["status"] == "ACTIVO" and lista["moneda"] == "MXN"
    lid = lista["id"]

    assert client.get(f"/api/v1/listas-precios/{lid}", headers=h).status_code == 200

    r = client.patch(f"/api/v1/listas-precios/{lid}", headers=h, json={"nombre": "Lista renombrada"})
    assert r.status_code == 200 and r.json()["nombre"] == "Lista renombrada"

    assert client.get("/api/v1/listas-precios", headers=h).json()["total"] == 1

    assert client.delete(f"/api/v1/listas-precios/{lid}", headers=h).status_code == 204
    assert client.get(f"/api/v1/listas-precios/{lid}", headers=h).status_code == 404
    assert client.get("/api/v1/listas-precios", headers=h).json()["total"] == 0


def test_lista_duplicate_codigo_conflicts(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    body = {"codigo": "DUP", "nombre": "Uno"}
    assert client.post("/api/v1/listas-precios", headers=h, json=body).status_code == 201
    assert client.post("/api/v1/listas-precios", headers=h, json=body).status_code == 409


def test_lista_status_filter(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    client.post("/api/v1/listas-precios", headers=h, json={"codigo": "ACT", "nombre": "Activa", "status": "ACTIVO"})
    client.post("/api/v1/listas-precios", headers=h, json={"codigo": "INA", "nombre": "Inactiva", "status": "INACTIVO"})
    assert client.get("/api/v1/listas-precios", headers=h, params={"status": "ACTIVO"}).json()["total"] == 1
    assert client.get("/api/v1/listas-precios", headers=h, params={"status": "INACTIVO"}).json()["total"] == 1
    assert client.get("/api/v1/listas-precios", headers=h).json()["total"] == 2


# ─── precios: update + conflict + scoping ────────────────────────────────────
def test_precio_update_and_tier_conflict(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    prod = client.post(
        "/api/v1/productos", headers=h,
        json={"sku": "91000080", "nombre": "Prod P", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"},
    ).json()
    lista = client.post("/api/v1/listas-precios", headers=h, json={"codigo": "L1", "nombre": "Lista 1"}).json()

    # Two tiers for the same product: menudeo (qty 1) and mayoreo (qty 10).
    p1 = client.post(f"/api/v1/listas-precios/{lista['id']}/precios", headers=h, json={
        "producto_id": prod["id"], "precio_unitario": "100.00", "cantidad_minima": 1}).json()
    p10 = client.post(f"/api/v1/listas-precios/{lista['id']}/precios", headers=h, json={
        "producto_id": prod["id"], "precio_unitario": "90.00", "cantidad_minima": 10}).json()

    # Update a price value → 200.
    r = client.patch(
        f"/api/v1/listas-precios/{lista['id']}/precios/{p1['id']}", headers=h,
        json={"precio_unitario": "95.50"})
    assert r.status_code == 200 and float(r.json()["precio_unitario"]) == 95.5

    # Collapsing the mayoreo tier onto qty 1 collides with p1 → 409.
    r = client.patch(
        f"/api/v1/listas-precios/{lista['id']}/precios/{p10['id']}", headers=h,
        json={"cantidad_minima": 1})
    assert r.status_code == 409


def test_precio_wrong_lista_is_404(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    prod = client.post(
        "/api/v1/productos", headers=h,
        json={"sku": "91000090", "nombre": "Prod W", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"},
    ).json()
    l1 = client.post("/api/v1/listas-precios", headers=h, json={"codigo": "L1", "nombre": "L1"}).json()
    l2 = client.post("/api/v1/listas-precios", headers=h, json={"codigo": "L2", "nombre": "L2"}).json()
    precio = client.post(f"/api/v1/listas-precios/{l1['id']}/precios", headers=h, json={
        "producto_id": prod["id"], "precio_unitario": "10.00"}).json()

    # The precio exists, but not under l2 → 404 (path scoping).
    r = client.patch(
        f"/api/v1/listas-precios/{l2['id']}/precios/{precio['id']}", headers=h,
        json={"precio_unitario": "11.00"})
    assert r.status_code == 404
    r = client.delete(f"/api/v1/listas-precios/{l2['id']}/precios/{precio['id']}", headers=h)
    assert r.status_code == 404


# ─── pagination ──────────────────────────────────────────────────────────────
def test_categoria_pagination(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    for code in ("C1", "C2", "C3"):
        client.post("/api/v1/categorias", headers=h, json={"codigo": code, "nombre": code})

    r = client.get("/api/v1/categorias", headers=h, params={"limit": 2, "offset": 0}).json()
    assert r["total"] == 3 and len(r["items"]) == 2 and r["limit"] == 2 and r["offset"] == 0
    assert [i["codigo"] for i in r["items"]] == ["C1", "C2"]

    r = client.get("/api/v1/categorias", headers=h, params={"limit": 2, "offset": 2}).json()
    assert r["total"] == 3 and len(r["items"]) == 1 and r["items"][0]["codigo"] == "C3"


# ─── catch-weight + fiscal fields (migration 0015) ───────────────────────────
def test_producto_peso_variable_and_fiscal_fields(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    r = client.post("/api/v1/productos", headers=h, json={
        "sku": "91000101", "nombre": "Sandía", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "50360000", "unidad_sat": "KGM",
        "unidad_base": "KILO", "presentaciones": {"KILO": 1, "PIEZA": 8},
        "peso_variable": True, "codigo_barras": "7501234567890", "contenido_litros": "0.6"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["peso_variable"] is True
    assert p["codigo_barras"] == "7501234567890"
    assert float(p["contenido_litros"]) == 0.6

    # defaults: omitting them → peso_variable False, the rest null
    r2 = client.post("/api/v1/productos", headers=h, json={
        "sku": "91000102", "nombre": "Arroz", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "50100000", "unidad_sat": "H87"})
    body = r2.json()
    assert body["peso_variable"] is False
    assert body["codigo_barras"] is None and body["contenido_litros"] is None


def test_esquema_ieps_cuota_and_tasa(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    # bebida saborizada: IVA 16% + IEPS cuota $3.0818/L
    r = client.post("/api/v1/esquemas-impuesto", headers=h, json={
        "codigo": "BEBAZ", "nombre": "Bebida azucarada", "iva_tasa": "0.16",
        "tipo_ieps": "CUOTA", "ieps_cuota": "3.0818"})
    assert r.status_code == 201, r.text
    esq = r.json()
    assert esq["tipo_ieps"] == "CUOTA" and float(esq["ieps_cuota"]) == 3.0818

    # botana: IEPS 8% como tasa; default tipo_ieps = TASA cuando se omite
    r2 = client.post("/api/v1/esquemas-impuesto", headers=h, json={
        "codigo": "BOT8", "nombre": "Botana", "ieps_tasa": "0.08"})
    assert r2.json()["tipo_ieps"] == "TASA" and float(r2.json()["ieps_cuota"]) == 0.0


def test_esquema_invalid_tipo_ieps_422(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    r = client.post("/api/v1/esquemas-impuesto", headers=h, json={
        "codigo": "BAD2", "nombre": "x", "tipo_ieps": "PORCENTAJE"})
    assert r.status_code == 422


# ─── SKU autogen + búsqueda por sinónimos + similares (A2) ───────────────────
def test_sku_autogenerated_when_blank(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    r = client.post("/api/v1/productos", headers=h, json={
        "nombre": "Sin SKU", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"})
    assert r.status_code == 201, r.text
    sku1 = r.json()["sku"]
    assert sku1.isdigit() and len(sku1) == 8
    # next one increments
    r2 = client.post("/api/v1/productos", headers=h, json={
        "nombre": "Papel aluminio", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "01010101", "unidad_sat": "KGM"})
    assert int(r2.json()["sku"]) == int(sku1) + 1


def test_search_matches_sinonimos(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    client.post("/api/v1/productos", headers=h, json={
        "sku": "91000110", "nombre": "Jitomate", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "50420000", "unidad_sat": "KGM",
        "sinonimos": ["tomate saladette", "guaje"]})
    # q matches a synonym, not just nombre/sku
    r = client.get("/api/v1/productos", headers=h, params={"q": "saladette"})
    assert r.json()["total"] == 1 and r.json()["items"][0]["sku"] == "91000110"


def test_similares_endpoint(client, env, auth_as):
    auth_as(env["admin_a"])
    h = _hdr(env["admin_a"])
    client.post("/api/v1/productos", headers=h, json={
        "sku": "91000120", "nombre": "Lechuga romana", "esquema_impuesto_id": _esquema(env["tenant_a"]), "clave_sat": "50430000", "unidad_sat": "H87",
        "sinonimos": ["lechuga orejona"]})
    r = client.get("/api/v1/productos/similares", headers=h, params={"nombre": "lechuga"})
    assert r.status_code == 200
    assert any(p["sku"] == "91000120" for p in r.json())
