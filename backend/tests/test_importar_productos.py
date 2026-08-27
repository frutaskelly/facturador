"""Importación masiva de productos + catálogo por cliente.

Cubre: la plantilla xlsx (descarga y round-trip determinista), el preview con
cruce contra el catálogo (vincular en vez de duplicar), el alta masiva (SKU
automático, alias del cliente, precios a su lista) y que el CFDI use el
código/nombre del cliente (NoIdentificacion / Descripcion).
"""
import io
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Almacen, CategoriaProducto, Cliente, EsquemaImpuesto, ListaPrecios,
    Membership, Precio, Producto, ProductoAlias, ProductoCliente, Role,
    Tenant, User,
)

_PURGE = (
    "movimientos_inventario", "lineas_factura", "facturas", "lotes_inventario",
    "producto_clientes", "producto_alias", "precios", "listas_precios",
    "productos", "categorias_producto", "esquemas_impuesto", "clientes", "almacenes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(slug=f"imp-{suffix}", legal_name="Imp SA", rfc=f"I{suffix.upper()}X"[:13],
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

        lista = ListaPrecios(tenant_id=tenant.id, codigo="LIMP", nombre="Lista Imp")
        db.add(lista); db.flush()
        cli = Cliente(tenant_id=tenant.id, codigo="CIMP", legal_name="Cliente Imp SA",
                      rfc="OBV191007BS1", regimen_fiscal="601", uso_cfdi_default="G01",
                      lista_precios_id=lista.id, domicilio_fiscal={"cp": "42110"})
        prod = Producto(tenant_id=tenant.id, sku="00000010", nombre="JITOMATE SALADETT",
                        clave_sat="50421800", unidad_sat="KGM")
        alm = Almacen(tenant_id=tenant.id, codigo="IMP-BG", nombre="Bodega Imp")
        db.add_all([cli, prod, alm]); db.flush()
        db.commit()
        yield {"admin": admin, "tomador": tomador, "tenant_id": tenant.id,
               "cli_id": str(cli.id), "prod_id": str(prod.id),
               "lista_id": str(lista.id), "alm_id": str(alm.id)}
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


def _xlsx(rows: list[list]) -> bytes:
    """Arma un xlsx en memoria (fila 0 = encabezados)."""
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    out = io.BytesIO(); wb.save(out)
    return out.getvalue()


# ─── plantilla ───────────────────────────────────────────────────────────────
def test_plantilla_descarga(client, env, auth_as):
    auth_as(env["admin"])
    r = client.get("/api/v1/productos/plantilla-importacion", headers=_hdr(env["admin"]))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"   # zip = xlsx

    auth_as(env["tomador"])
    r = client.get("/api/v1/productos/plantilla-importacion", headers=_hdr(env["tomador"]))
    assert r.status_code == 403     # solo quien gestiona productos


# ─── preview ─────────────────────────────────────────────────────────────────
def test_preview_plantilla_cruza_sin_duplicar(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["NOMBRE", "CODIGO", "UNIDAD", "PRECIO"],
        ["JITOMATE SALADETT", "JIT-SAD-001", "KILO", "28.50"],
        ["PRODUCTO INVENTADO XYZ", "", "PIEZA", "10"],
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("productos.xlsx", data,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"usar_ia": "false", "cliente_id": env["cli_id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["formato"] == "plantilla"
    f1, f2 = body["filas"]
    # Nombre exacto en catálogo → sugiere VINCULAR al existente (no duplicar).
    assert f1["producto_id"] == env["prod_id"]
    assert f1["candidatos"][0]["sku"] == "00000010"
    assert f1["ya_vinculado"] is False
    # Sin cruce → alta nueva (producto_id vacío).
    assert f2["producto_id"] is None


def test_preview_codigo_ya_vinculado(client, env, auth_as):
    db = SessionLocal()
    try:
        db.add(ProductoCliente(
            tenant_id=env["tenant_id"], cliente_id=uuid.UUID(env["cli_id"]),
            producto_id=uuid.UUID(env["prod_id"]),
            codigo_cliente="JIT-SAD-001", nombre_cliente="JITOMATE ROMA"))
        db.commit()
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([["NOMBRE", "CODIGO"], ["JITOMATE ROMA", "JIT-SAD-001"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false", "cliente_id": env["cli_id"]},
    )
    assert r.status_code == 200, r.text
    f1 = r.json()["filas"][0]
    # Su código ya está vinculado → mismo producto, marcado como vinculado.
    assert f1["producto_id"] == env["prod_id"]
    assert f1["ya_vinculado"] is True


def test_preview_archivo_sin_nombre_sin_ia(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([["COSA", "OTRA"], ["x", "y"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("raro.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 422
    assert "plantilla" in r.json()["detail"].lower()


# ─── importar ────────────────────────────────────────────────────────────────
def test_importar_crea_vincula_alias_y_precios(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "cliente_id": env["cli_id"],
        "guardar_precios": True,
        "filas": [
            {  # el cliente llama "JITOMATE ROMA" a nuestro saladett
                "accion": "vincular", "producto_id": env["prod_id"],
                "nombre": "JITOMATE ROMA", "codigo_cliente": "JIT-SAD-001",
                "nombre_cliente": "JITOMATE ROMA", "precio": "28.50",
            },
            {  # producto nuevo: SKU automático + alias con su código
                "accion": "crear", "nombre": "Tortilla Harina Tia Rosa 50",
                "unidad_base": "PIEZA", "clave_sat": "50221300", "unidad_sat": "H87",
                "codigo_cliente": "TORT-CERE-631", "nombre_cliente": "TORTILLAS DE HARINA TIA ROSA 50 PZA",
                "precio": "93.50",
            },
            {"accion": "omitir", "nombre": "FILA IGNORADA"},
        ],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["creados"], res["vinculados"], res["omitidos"]) == (1, 1, 1)
    assert res["alias_guardados"] == 2
    assert res["precios_guardados"] == 2
    assert res["errores"] == []

    db = SessionLocal()
    try:
        nuevo = db.query(Producto).filter(
            Producto.tenant_id == env["tenant_id"],
            Producto.nombre == "TORTILLA HARINA TIA ROSA 50").one()
        assert nuevo.sku.isdigit() and len(nuevo.sku) == 8   # SKU automático
        assert nuevo.unidad_base == "PIEZA"
        assert nuevo.presentaciones == {"PIEZA": 1}

        pcs = {pc.codigo_cliente: pc for pc in db.query(ProductoCliente).filter(
            ProductoCliente.cliente_id == uuid.UUID(env["cli_id"])).all()}
        assert str(pcs["JIT-SAD-001"].producto_id) == env["prod_id"]
        assert pcs["TORT-CERE-631"].producto_id == nuevo.id

        # El cruce aprendió el nombre del cliente.
        alias = db.query(ProductoAlias).filter(
            ProductoAlias.tenant_id == env["tenant_id"],
            ProductoAlias.alias_normalizado == "jitomate roma").one()
        assert str(alias.producto_id) == env["prod_id"]
        assert alias.origen == "IMPORT"

        # Precios a la lista del cliente.
        precios = db.query(Precio).filter(
            Precio.lista_id == uuid.UUID(env["lista_id"])).all()
        assert {str(p.precio_unitario) for p in precios} == {"28.5000", "93.5000"}
    finally:
        db.close()


def test_importar_precios_sin_lista_falla(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "guardar_precios": True,
        "filas": [{"accion": "crear", "nombre": "ALGO", "precio": "1"}],
    })
    assert r.status_code == 422
    assert "lista" in r.json()["detail"].lower()


def test_importar_fila_mala_no_tira_el_lote(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "filas": [
            {"accion": "crear", "nombre": "PRODUCTO BUENO A"},
            {"accion": "crear", "nombre": "PRODUCTO SKU DUP", "sku": "00000010"},  # choca
            {"accion": "crear", "nombre": "PRODUCTO BUENO B"},
        ],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["creados"] == 2
    assert len(res["errores"]) == 1 and res["errores"][0]["fila"] == 2


# ─── CFDI: el nombre/código del cliente viajan en el XML ─────────────────────
def test_cfdi_usa_nombre_y_codigo_del_cliente(client, env, auth_as):
    from app.models import Factura
    from app.services.cfdi import build_payload
    auth_as(env["admin"]); h = _hdr(env["admin"])

    # Stock para la factura directa.
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod_id"], "almacen_id": env["alm_id"],
        "cantidad": "100", "costo_unitario": "5"})
    fac = client.post("/api/v1/facturas/directa", headers=h, json={
        "cliente_id": env["cli_id"], "almacen_id": env["alm_id"],
        "lineas": [{"producto_id": env["prod_id"], "cantidad": "2", "precio_unitario": "28.50"}],
    }).json()

    db = SessionLocal()
    try:
        f = db.query(Factura).filter(Factura.id == uuid.UUID(fac["id"])).one()
        # Sin alias: nombre interno + SKU interno (estándar: siempre viaja).
        payload = build_payload(db, f)
        item = payload["Items"][0]
        assert item["Description"] == "JITOMATE SALADETT"
        assert item["IdentificationNumber"] == "00000010"

        # Con alias del cliente: SU nombre y SU código.
        db.add(ProductoCliente(
            tenant_id=env["tenant_id"], cliente_id=uuid.UUID(env["cli_id"]),
            producto_id=uuid.UUID(env["prod_id"]),
            codigo_cliente="JIT-SAD-001", nombre_cliente="JITOMATE ROMA"))
        db.flush()
        payload = build_payload(db, f)
        item = payload["Items"][0]
        assert item["Description"] == "JITOMATE ROMA"
        assert item["IdentificationNumber"] == "JIT-SAD-001"
        db.rollback()
    finally:
        db.close()


# ─── catálogo del cliente (CRUD) ─────────────────────────────────────────────
def test_catalogo_cliente_crud(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    base = f"/api/v1/clientes/{env['cli_id']}/catalogo"

    r = client.put(f"{base}/{env['prod_id']}", headers=h,
                   json={"codigo_cliente": "JIT-SAD-001", "nombre_cliente": "Jitomate Roma"})
    assert r.status_code == 200, r.text
    assert r.json()["codigo_cliente"] == "JIT-SAD-001"

    r = client.get(base, headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["producto_sku"] == "00000010"
    assert rows[0]["nombre_cliente"] == "Jitomate Roma"

    # Upsert: re-captura actualiza, no duplica.
    r = client.put(f"{base}/{env['prod_id']}", headers=h,
                   json={"codigo_cliente": "JIT-2", "nombre_cliente": None})
    assert r.status_code == 200 and r.json()["codigo_cliente"] == "JIT-2"
    assert len(client.get(base, headers=h).json()) == 1

    # Vacío total → 422.
    r = client.put(f"{base}/{env['prod_id']}", headers=h, json={})
    assert r.status_code == 422

    r = client.delete(f"{base}/{env['prod_id']}", headers=h)
    assert r.status_code == 204
    assert client.get(base, headers=h).json() == []

    # Escritura exige gestionar clientes.
    auth_as(env["tomador"])
    r = client.put(f"{base}/{env['prod_id']}", headers=_hdr(env["tomador"]),
                   json={"codigo_cliente": "X"})
    assert r.status_code == 403


def test_preview_formato_sae_sin_ia(client, env, auth_as):
    """Export SAE: Linea | Clave SAE | Descripcion | Unidad | Precio — la
    DESCRIPCION es el nombre y se parsea determinista (sin IA)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["Linea", "Clave SAE", "Descripcion", "Unidad", "Precio"],
        ["ABARR", "ACEI-ACEI-639", "ACEITE COMESTIBLE 20 LT CRISTAL", "PIEZA", "935.4"],
        ["SECOS", "TAMA-FRUT-437", "TAMARINDO", "KILOGRAMO", "94.5"],
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista_sae.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["formato"] == "plantilla"
    f1, f2 = body["filas"]
    assert f1["nombre"] == "ACEITE COMESTIBLE 20 LT CRISTAL"
    assert f1["codigo"] == "ACEI-ACEI-639"
    assert f2["unidad"] == "KILO"   # KILOGRAMO normalizado


def test_preview_marca_duplicados_del_archivo(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["NOMBRE", "UNIDAD", "PRECIO"],
        ["ESPARRAGOS", "KILO", "264"],
        ["ESPARRAGOS", "KILO", "264"],          # repetida → omitir por default
        ["PAPAYA MARADOL", "KILO", "43.5"],
        ["PAPAYA MARADOL", "PIEZA", "43.5"],    # otra unidad → NO es duplicado
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 200, r.text
    f = r.json()["filas"]
    assert f[0]["duplicada_de"] is None
    assert f[1]["duplicada_de"] == 1
    assert f[2]["duplicada_de"] is None
    assert f[3]["duplicada_de"] is None   # KG vs PZ = dos presentaciones


def test_preview_difuso_direccional(client, env, auth_as):
    """'AJO EN POLVO' NO se auto-vincula al 'AJO' del catálogo (más específico);
    'AJO DE PRIMERA' sí (solo calificativos genéricos de más)."""
    db = SessionLocal()
    try:
        db.add(Producto(tenant_id=env["tenant_id"], sku="00000020", nombre="AJO",
                        clave_sat="50403700", unidad_sat="KGM"))
        db.commit()
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["NOMBRE", "UNIDAD"],
        ["AJO EN POLVO", "KILO"],
        ["AJO DE PRIMERA", "KILO"],
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 200, r.text
    f1, f2 = r.json()["filas"]
    assert f1["producto_id"] is None            # más específico → crear
    assert any(c["nombre"] == "AJO" for c in f1["candidatos"])  # pero se ofrece
    assert f2["producto_id"] is not None        # calificativo genérico → vincular


def test_preview_repetida_con_otro_precio(client, env, auth_as):
    """Renglón repetido con OTRO precio = conflicto visible, no descarte mudo."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["NOMBRE", "UNIDAD", "PRECIO"],
        ["CACAHUATE JAPONES", "PIEZA", "130.00"],
        ["CACAHUATE JAPONES", "PIEZA", "110.50"],   # mismo producto, otro precio
        ["ESPARRAGOS", "KILO", "264"],
        ["ESPARRAGOS", "KILO", "264"],              # repetida idéntica
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 200, r.text
    f = r.json()["filas"]
    assert f[1]["duplicada_de"] == 1 and f[1]["precio_distinto"] is True
    assert f[3]["duplicada_de"] == 3 and f[3]["precio_distinto"] is False


def test_preview_no_liga_a_procesado(client, env, auth_as):
    """'CHILE JALAPEÑO' (fresco) no se auto-liga a la lata de picados."""
    db = SessionLocal()
    try:
        db.add(Producto(tenant_id=env["tenant_id"], sku="00000030",
                        nombre="CHILE JALAPENO PICADOS 215 GR SAN MARCOS",
                        clave_sat="50405600", unidad_sat="H87"))
        db.commit()
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([["NOMBRE", "UNIDAD"], ["CHILE JALAPEÑO", "KILO"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 200, r.text
    f1 = r.json()["filas"][0]
    assert f1["producto_id"] is None        # transformación solo en el candidato
    assert len(f1["candidatos"]) >= 1       # pero se ofrece en el desplegable


def test_preview_dos_filas_al_mismo_producto(client, env, auth_as):
    """'PIMIENTA' y 'PIMIENTA BLANCA' vinculadas al mismo producto: la segunda
    se marca — si se importan ambas, la última pisa el alias del cliente."""
    db = SessionLocal()
    try:
        db.add(Producto(tenant_id=env["tenant_id"], sku="00000040",
                        nombre="PIMIENTA BLANCA MEMBERS BOTE 500 GR",
                        clave_sat="50171800", unidad_sat="H87"))
        db.commit()
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["NOMBRE", "UNIDAD", "PRECIO"],
        ["PIMIENTA", "KILO", "224.50"],
        ["PIMIENTA BLANCA", "KILO", "270.00"],
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 200, r.text
    f1, f2 = r.json()["filas"]
    assert f1["producto_id"] == f2["producto_id"] and f1["producto_id"] is not None
    assert f1["mismo_producto_que"] is None
    assert f2["mismo_producto_que"] == 1


# ─── F1/F2: catálogo SAT en la base + plantilla v2 + preguntas en lote ───────
def test_sat_catalogo_busqueda(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.get("/api/v1/sat/claves", headers=h, params={"q": "cilantro"})
    assert r.status_code == 200, r.text
    claves = {c["clave"] for c in r.json()}
    assert "50404106" in claves          # "Cilantro" del catálogo oficial

    r = client.get("/api/v1/sat/claves", headers=h, params={"q": "504041"})
    assert any(c["clave"].startswith("504041") for c in r.json())   # por prefijo

    r = client.get("/api/v1/sat/unidades", headers=h, params={"q": "KGM"})
    assert r.status_code == 200
    assert any(u["clave"] == "KGM" for u in r.json())


def test_sugerir_sat_batch_solo_catalogo(client, env, auth_as):
    """Sin IA (tests corren sin API key): gana el mejor candidato del catálogo;
    sin candidatos, la genérica 01010101. Nunca claves inventadas."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/sugerir-sat-batch", headers=h, json={
        "productos": [
            {"nombre": "CILANTRO", "unidad": "KILO"},
            {"nombre": "ZZZKQJWX INEXISTENTE", "unidad": "PIEZA"},
        ],
    })
    assert r.status_code == 200, r.text
    s1, s2 = r.json()
    assert len(s1["clave_sat"]) == 8 and s1["clave_sat"] != "01010101"
    assert "cilantro" in s1["descripcion_sat"].lower()
    assert s1["unidad_sat"] == "KGM"
    assert s2["clave_sat"] == "01010101"
    assert s2["unidad_sat"] == "H87"


def test_preview_sae_completo_y_meta(client, env, auth_as):
    """Export SAE completo: CATEGORÍA/ESTATUS/UNIDAD DE SALIDA (SAT) mapean;
    claves validadas contra el catálogo oficial; meta para preguntas en lote."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["CLAVE", "DESCRIPCIÓN", "UNIDAD DE SALIDA", "CLAVE SAT", "UNIDAD DE SALIDA SAT",
         "PRECIO", "CATEGORÍA", "ESTATUS"],
        ["ACEI-1", "ACEITE CANOLA 946 ML", "PZ", "50151513", "H87", "47.6", "ABARROTE", "ALTA"],
        ["XX-2", "PRODUCTO CLAVE MALA", "PZ", "99999999", "ZZZ", "10", "ABARROTE", "ALTA"],
        ["XX-3", "PRODUCTO SIN CLAVES", "KILOGRAMO", "", "", "5", "— sin categoría —", "ALTA"],
        ["XX-4", "PRODUCTO DADO DE BAJA", "PZ", "", "", "1", "ABARROTE", "BAJA"],
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("sae.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    f1, f2, f3, f4 = body["filas"]
    assert f1["clave_sat_valida"] is True and f1["unidad_sat_valida"] is True
    assert f1["unidad"] == "PIEZA" and f1["categoria"] == "ABARROTE"
    assert f2["clave_sat_valida"] is False and f2["unidad_sat_valida"] is False
    assert f3["clave_sat_valida"] is None and f3["categoria"] == ""   # sin categoría
    assert f3["unidad"] == "KILO"
    assert f4["baja"] is True
    # Meta (la fila BAJA no cuenta): 1 sin clave, 1 sin unidad, sí hay precios.
    assert body["faltan_clave_sat"] == 1
    assert body["faltan_unidad_sat"] == 1
    assert body["categorias_nuevas"] == ["ABARROTE"]
    assert body["tiene_precios"] is True


def test_importar_categoria_esquema_y_baja(client, env, auth_as):
    db = SessionLocal()
    try:
        esq = EsquemaImpuesto(tenant_id=env["tenant_id"], codigo="IVA16X",
                              nombre="IVA 16 import", iva_tasa=Decimal("0.16"))
        db.add(esq); db.commit()
        esq_id = str(esq.id)
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "crear_categorias": True,
        "filas": [
            {"accion": "crear", "nombre": "REFRESCO COLA 600", "unidad_base": "PIEZA",
             "categoria": "BEBIDAS", "esquema": "IVA16X"},
            {"accion": "crear", "nombre": "PRODUCTO INACTIVO", "activo": False,
             "categoria": "BEBIDAS"},
        ],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["creados"] == 2 and res["categorias_creadas"] == 1
    db = SessionLocal()
    try:
        cat = db.query(CategoriaProducto).filter(
            CategoriaProducto.tenant_id == env["tenant_id"],
            CategoriaProducto.nombre == "BEBIDAS").one()
        p1 = db.query(Producto).filter(
            Producto.tenant_id == env["tenant_id"],
            Producto.nombre == "REFRESCO COLA 600").one()
        assert p1.categoria_id == cat.id
        assert str(p1.esquema_impuesto_id) == esq_id
        p2 = db.query(Producto).filter(
            Producto.tenant_id == env["tenant_id"],
            Producto.nombre == "PRODUCTO INACTIVO").one()
        assert p2.activo is False and p2.categoria_id == cat.id
    finally:
        db.close()


# ─── F3: un producto, varias presentaciones (Cilantro) ───────────────────────
def test_importar_variante_presentacion(client, env, auth_as):
    """'Cilantro por manojo 500 grms' del cliente = el MISMO producto CILANTRO
    con la presentación MANOJO agregada (factor 0.5 KILO) y su precio propio."""
    db = SessionLocal()
    try:
        cil = Producto(tenant_id=env["tenant_id"], sku="00000050", nombre="CILANTRO",
                       clave_sat="50404106", unidad_sat="KGM", unidad_base="KILO",
                       presentaciones={"KILO": 1}, presentacion_default="KILO")
        db.add(cil); db.commit()
        cil_id = str(cil.id)
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])

    # El preview detecta la variante nueva.
    data = _xlsx([["NOMBRE", "UNIDAD", "PRECIO"], ["CILANTRO", "MANOJO", "12.50"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false", "cliente_id": env["cli_id"]},
    )
    assert r.status_code == 200, r.text
    f1 = r.json()["filas"][0]
    assert f1["producto_id"] == cil_id and f1["nueva_presentacion"] is True

    # Importar con el factor confirma la variante.
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "cliente_id": env["cli_id"], "guardar_precios": True,
        "filas": [{
            "accion": "vincular", "producto_id": cil_id, "nombre": "CILANTRO",
            "unidad_base": "MANOJO", "presentacion_factor": "0.5",
            "nombre_cliente": "Cilantro por manojo 500 grms", "precio": "12.50",
        }],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["vinculados"] == 1 and res["presentaciones_agregadas"] == 1
    db = SessionLocal()
    try:
        cil = db.query(Producto).filter(Producto.id == uuid.UUID(cil_id)).one()
        assert cil.presentaciones["MANOJO"] == {"factor": 0.5, "sat": "H87"}
        pc = db.query(ProductoCliente).filter(
            ProductoCliente.producto_id == uuid.UUID(cil_id)).one()
        assert pc.presentacion == "MANOJO"
        assert pc.nombre_cliente == "Cilantro por manojo 500 grms"
        precio = db.query(Precio).filter(
            Precio.lista_id == uuid.UUID(env["lista_id"]),
            Precio.producto_id == uuid.UUID(cil_id)).one()
        assert precio.presentacion == "MANOJO"    # el precio es del MANOJO
    finally:
        db.close()


# ─── F4: crear lista al importar y asignarla (default / clientes) ────────────
def test_importar_crea_lista_y_se_asigna(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "guardar_precios": True, "lista_nombre": "Lista Mayoreo 2026",
        "filas": [{"accion": "crear", "nombre": "AZUCAR ESTANDAR 50 KG",
                   "unidad_base": "BULTO", "precio": "1150"}],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["lista_id"] and res["lista_nombre"] == "Lista Mayoreo 2026"
    lista_id = res["lista_id"]

    # Asignar: default del negocio + al cliente del fixture.
    r = client.post(f"/api/v1/listas-precios/{lista_id}/asignar", headers=h,
                    json={"default": True, "cliente_ids": [env["cli_id"]]})
    assert r.status_code == 200, r.text
    assert r.json() == {"default": True, "clientes_asignados": 1}
    db = SessionLocal()
    try:
        lista = db.query(ListaPrecios).filter(ListaPrecios.id == uuid.UUID(lista_id)).one()
        assert lista.es_default is True
        cli = db.query(Cliente).filter(Cliente.id == uuid.UUID(env["cli_id"])).one()
        assert str(cli.lista_precios_id) == lista_id
        # La resolución de precios la toma como lista base.
        from app.services.precios import _lista_default
        # (sesión owner ve todos los tenants; filtra por el nuestro)
        assert db.query(ListaPrecios).filter(
            ListaPrecios.tenant_id == env["tenant_id"],
            ListaPrecios.es_default.is_(True)).count() == 1
    finally:
        db.close()

    # Otra lista marcada default → la anterior se limpia (solo una default).
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "guardar_precios": True, "lista_nombre": "Lista Menudeo",
        "filas": [{"accion": "crear", "nombre": "SAL DE MESA 1 KG", "precio": "18"}],
    })
    lista2 = r.json()["lista_id"]
    client.post(f"/api/v1/listas-precios/{lista2}/asignar", headers=h,
                json={"default": True, "cliente_ids": []})
    db = SessionLocal()
    try:
        defaults = db.query(ListaPrecios).filter(
            ListaPrecios.tenant_id == env["tenant_id"],
            ListaPrecios.es_default.is_(True)).all()
        assert len(defaults) == 1 and str(defaults[0].id) == lista2
    finally:
        db.close()


def test_importar_varios_clientes(client, env, auth_as):
    """Una lista para VARIOS clientes: códigos/nombres se guardan para cada uno
    y la lista creada se asigna a los seleccionados sin lista propia."""
    db = SessionLocal()
    try:
        c2 = Cliente(tenant_id=env["tenant_id"], codigo="CIMP2", legal_name="Cliente Dos SA",
                     rfc="CDO900101AA1", regimen_fiscal="601")   # SIN lista
        db.add(c2); db.commit()
        c2_id = str(c2.id)
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])

    # Preview acepta varios clientes (marca lo ya vinculado de cualquiera).
    data = _xlsx([["NOMBRE", "CODIGO", "PRECIO"], ["JITOMATE SALADETT", "GRP-001", "28.50"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false", "cliente_ids": [env["cli_id"], c2_id]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["filas"][0]["producto_id"] == env["prod_id"]

    r = client.post("/api/v1/productos/importar", headers=h, json={
        "cliente_ids": [env["cli_id"], c2_id],
        "guardar_precios": True, "lista_nombre": "Lista Grupo",
        "filas": [{
            "accion": "vincular", "producto_id": env["prod_id"],
            "nombre": "JITOMATE SALADETT", "codigo_cliente": "GRP-001",
            "nombre_cliente": "JITOMATE ROMA GRUPO", "precio": "28.50",
        }],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["vinculados"] == 1
    assert res["alias_guardados"] == 2          # un upsert POR cliente
    assert res["lista_nombre"] == "Lista Grupo"

    db = SessionLocal()
    try:
        pcs = db.query(ProductoCliente).filter(
            ProductoCliente.producto_id == uuid.UUID(env["prod_id"])).all()
        assert {str(p.cliente_id) for p in pcs} == {env["cli_id"], c2_id}
        assert all(p.codigo_cliente == "GRP-001" for p in pcs)
        # cli del fixture YA tenía lista → no se pisa; c2 sin lista → se asigna.
        cli1 = db.query(Cliente).filter(Cliente.id == uuid.UUID(env["cli_id"])).one()
        assert str(cli1.lista_precios_id) == env["lista_id"]
        cli2 = db.query(Cliente).filter(Cliente.id == uuid.UUID(c2_id)).one()
        assert str(cli2.lista_precios_id) == str(res["lista_id"])
    finally:
        db.close()


# ─── Regresiones de la auditoría del incidente 2026-08-27 ────────────────────
def test_precio_con_coma_decimal():
    """'12,50' es doce con cincuenta, no mil doscientos cincuenta."""
    from app.services.importar_productos import _decimal
    casos = {
        "12,50": "12.50",        # coma decimal (formato europeo)
        "12.50": "12.50",        # punto decimal
        "1,234.56": "1234.56",   # coma de miles + punto decimal
        "1.234,56": "1234.56",   # punto de miles + coma decimal
        "1,234": "1234",         # coma de miles
        "$ 935.40": "935.40",
        "0": "0",
    }
    for entrada, esperado in casos.items():
        assert _decimal(entrada) == Decimal(esperado), f"{entrada} → {_decimal(entrada)}"
    assert _decimal("") is None
    assert _decimal("abc") is None


def test_preview_multicliente_codigo_ambiguo(client, env, auth_as):
    """Dos clientes con el MISMO código para productos distintos: no se
    auto-sugiere ninguno (antes ganaba el primero del SELECT y ligaba mal)."""
    db = SessionLocal()
    try:
        otro = Producto(tenant_id=env["tenant_id"], sku="00000060", nombre="SAL DE MESA 1 KG",
                        clave_sat="50171550", unidad_sat="KGM")
        c2 = Cliente(tenant_id=env["tenant_id"], codigo="CAMB", legal_name="Cliente Ambiguo SA",
                     rfc="CAM900101AA1", regimen_fiscal="601")
        db.add_all([otro, c2]); db.flush()
        # Mismo código "X-100" → productos DISTINTOS según el cliente.
        db.add(ProductoCliente(tenant_id=env["tenant_id"], cliente_id=uuid.UUID(env["cli_id"]),
                               producto_id=uuid.UUID(env["prod_id"]), codigo_cliente="X-100"))
        db.add(ProductoCliente(tenant_id=env["tenant_id"], cliente_id=c2.id,
                               producto_id=otro.id, codigo_cliente="X-100"))
        db.commit()
        c2_id, otro_id = str(c2.id), str(otro.id)
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([["NOMBRE", "CODIGO"], ["PRODUCTO CUALQUIERA ZZZ", "X-100"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false", "cliente_ids": [env["cli_id"], c2_id]},
    )
    assert r.status_code == 200, r.text
    f1 = r.json()["filas"][0]
    # Código ambiguo entre los clientes elegidos → sin sugerencia por código.
    assert f1["producto_id"] is None
    assert f1["ya_vinculado"] is False

    # Con UN solo cliente el código sí manda (no hay ambigüedad).
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false", "cliente_ids": [c2_id]},
    )
    f1 = r.json()["filas"][0]
    assert f1["producto_id"] == otro_id and f1["ya_vinculado"] is True


def test_cruce_masivo_no_hace_un_select_por_fila(client, env, auth_as):
    """El preview carga los alias UNA vez: sin esto eran 500+ viajes a la base
    (medio segundo en local, decenas de segundos contra la base en la nube)."""
    from sqlalchemy import event
    from app.core.db import engine

    auth_as(env["admin"]); h = _hdr(env["admin"])
    filas = [["NOMBRE", "UNIDAD"]] + [[f"PRODUCTO MASIVO {i}", "KILO"] for i in range(60)]
    data = _xlsx(filas)

    consultas: list[str] = []
    def _contar(conn, cursor, statement, params, context, executemany):
        consultas.append(statement)
    event.listen(engine, "before_cursor_execute", _contar)
    try:
        r = client.post(
            "/api/v1/productos/importar-preview", headers=h,
            files={"archivo": ("masivo.xlsx", data, "application/octet-stream")},
            data={"usar_ia": "false"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", _contar)
    assert r.status_code == 200 and len(r.json()["filas"]) == 60
    alias_queries = [q for q in consultas if "producto_alias" in q]
    assert len(alias_queries) <= 1, f"{len(alias_queries)} consultas a producto_alias (debe ser 1)"
