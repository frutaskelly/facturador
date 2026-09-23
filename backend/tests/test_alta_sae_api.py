"""Altas de producto en SAE — la cola entre el Facturador y el conector.

El backend no ve SAE, así que crear un artículo allá funciona por solicitud
(mismo reparto que el espejo de facturas). Lo que se prueba aquí es lo que hace
SEGURA esa cola, porque **nunca se reintenta una escritura a SAE**: una sola
alta viva por clave, reclamo excluyente, cierre único, y la clave estampada en
el producto SOLO cuando SAE la confirmó.
"""
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (ClaveSae, EsquemaImpuesto, Membership, Producto, Role,
                        SolicitudAltaSae, Tenant, User)

_PURGE = ("solicitudes_alta_sae", "claves_sae", "producto_clientes", "productos",
          "esquemas_impuesto")


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"alta-{suffix}", legal_name="Alta SA",
                   rfc=f"AL{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        tomador_role = db.query(Role).filter(Role.nombre == "TOMADOR", Role.es_preset.is_(True)).one()

        def _user(role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=t.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": t.id}

        admin = _user(admin_role, "admin")
        tomador = _user(tomador_role, "tomador")
        prod = Producto(tenant_id=t.id, sku="A-P", nombre="Ajo kilo",
                        clave_sat="01010101", unidad_sat="KGM")
        # el esquema "2" es el que el bot manda en el alta (los códigos de este
        # tenant son los números de SAE)
        esq2 = EsquemaImpuesto(tenant_id=t.id, codigo="2", nombre="0% IVA")
        db.add_all([prod, esq2]); db.flush()
        db.commit()
        yield {"admin": admin, "tomador": tomador, "tenant_id": t.id,
               "prod": str(prod.id), "esq2": esq2.id}
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
def conector(env):
    """El conector entra con alcance de conexión: `factura:espejo` no lo trae el
    ADMIN preset (igual que en el espejo de facturas), así que reclamar y
    reportar se prueban con un OWNER."""
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    try:
        owner_role = db.query(Role).filter(
            Role.nombre == "OWNER", Role.es_preset.is_(True)).one()
        u = User(email=f"con-{suffix}@t.test", auth_user_id=f"sub-con-{suffix}",
                 full_name="conector")
        db.add(u); db.flush()
        m = Membership(tenant_id=env["tenant_id"], user_id=u.id, role_id=owner_role.id)
        db.add(m); db.flush()
        db.commit()
        yield {"sub": u.auth_user_id, "email": u.email, "tenant_id": env["tenant_id"]}
    finally:
        db.query(Membership).filter(Membership.id == m.id).delete()
        db.query(User).filter(User.id == u.id).delete()
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


def _pedir(client, h, **extra):
    body = {"clave": "AJOKG", "descripcion": "AJO KILO", "unidad": "KILO",
            "linea": "FRUVE", "esquema": 2, "sat": "50161509", "sat_unidad": "KGM"}
    body.update(extra)
    return client.post("/api/v1/productos/alta-sae", headers=h, json=body)


def test_alta_se_pide_para_las_cuatro_empresas_por_defecto(client, env, auth_as):
    """Sin lista de empresas van las CUATRO: el estado que hoy duele es el
    producto que quedó creado en una sola."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _pedir(client, h, producto_id=env["prod"], origen="WHATSAPP")
    assert r.status_code == 201, r.text
    sol = r.json()
    assert sol["estado"] == "PENDIENTE" and sol["origen"] == "WHATSAPP"
    assert sol["empresas"] == ["02", "03", "04", "05"]
    assert sol["datos"]["descripcion"] == "AJO KILO" and sol["datos"]["linea"] == "FRUVE"

    # una empresa desconocida no se encola
    assert _pedir(client, h, clave="OTRA", empresas=["02", "99"],
                  ).status_code == 422


def test_alta_es_idempotente_por_clave(client, env, auth_as):
    """Dos «dale de alta AJOKG» seguidos devuelven LA MISMA solicitud: dos
    altas vivas insertarían dos veces el mismo artículo en SAE."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    a = _pedir(client, h).json()
    b = _pedir(client, h, descripcion="AJO KILO CORREGIDO")
    assert b.status_code == 201
    assert b.json()["id"] == a["id"]
    # y no se cuela por la caja de la clave ni por espacios
    c = _pedir(client, h, clave="  ajokg ")
    assert c.json()["id"] == a["id"]


def test_alta_rechaza_clave_que_ya_existe_en_sae(client, env, auth_as):
    """Si la clave ya está en el espejo de SAE, no hay que crearla: hay que
    ligarla. Encolar un INSERT aquí lo duplicaría."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    with SessionLocal() as s:
        s.add(ClaveSae(tenant_id=env["tenant_id"], empresa="02", clave="AJOKG",
                       descripcion="AJO", activa=True))
        s.commit()
    r = _pedir(client, h)
    assert r.status_code == 409
    assert "ya existe en SAE" in r.json()["detail"]


def test_reclamar_es_excluyente_y_reportar_cierra_una_sola_vez(client, env, auth_as, conector):
    """El conector reclama (EN_CURSO) y reporta una vez. Un segundo reporte es
    la puerta de atrás al reintento, así que se rechaza."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h, producto_id=env["prod"]).json()

    auth_as(conector)
    r = client.get("/api/v1/productos/alta-sae/pendiente", headers=h)
    assert r.status_code == 200 and r.json()["id"] == sol["id"]
    assert r.json()["estado"] == "EN_CURSO"
    # ya no hay pendientes: otro conector no la toma
    assert client.get("/api/v1/productos/alta-sae/pendiente", headers=h).json() is None

    rep = client.post(f"/api/v1/productos/alta-sae/{sol['id']}/reporte", headers=h,
                      json={"por_empresa": {e: {"ok": True, "clave": "AJOKG"}
                                            for e in ("02", "03", "04", "05")}})
    assert rep.status_code == 200, rep.text
    assert rep.json()["estado"] == "OK"
    # la clave se estampó en el producto porque SAE la confirmó
    auth_as(env["admin"])
    assert client.get(f"/api/v1/productos/{env['prod']}",
                      headers=h).json()["clave_sae"] == "AJOKG"
    # segundo reporte: no
    auth_as(conector)
    assert client.post(f"/api/v1/productos/alta-sae/{sol['id']}/reporte", headers=h,
                       json={"por_empresa": {}}).status_code == 409


def test_alta_parcial_no_es_ok_ni_error(client, env, auth_as, conector):
    """Una empresa creada y otra no: PARCIAL. No es OK (falta trabajo) ni ERROR
    (algo ya se creó y eso no se puede repetir)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h, producto_id=env["prod"], empresas=["02", "03"]).json()
    auth_as(conector)
    client.get("/api/v1/productos/alta-sae/pendiente", headers=h)
    rep = client.post(f"/api/v1/productos/alta-sae/{sol['id']}/reporte", headers=h,
                      json={"por_empresa": {"02": {"ok": True, "clave": "AJOKG"},
                                            "03": {"ok": False, "error": "clave ocupada"}},
                            "motivo": "en la 03 la clave ya era de otro artículo"})
    assert rep.status_code == 200
    out = rep.json()
    assert out["estado"] == "PARCIAL"
    assert out["resultado"]["03"]["error"] == "clave ocupada"
    assert "03" in out["motivo"]
    # con la 02 creada, la clave sí se estampa: allá ya existe
    auth_as(env["admin"])
    assert client.get(f"/api/v1/productos/{env['prod']}",
                      headers=h).json()["clave_sae"] == "AJOKG"


def test_alta_sin_confirmacion_no_estampa_la_clave(client, env, auth_as, conector):
    """Ninguna empresa creada = ERROR y el producto se queda SIN clave: el
    Facturador no se apunta una clave que SAE no confirmó."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h, producto_id=env["prod"], empresas=["02"]).json()
    auth_as(conector)
    client.get("/api/v1/productos/alta-sae/pendiente", headers=h)
    rep = client.post(f"/api/v1/productos/alta-sae/{sol['id']}/reporte", headers=h,
                      json={"por_empresa": {"02": {"ok": False, "error": "SAE apagado"}}})
    assert rep.json()["estado"] == "ERROR"
    auth_as(env["admin"])
    assert client.get(f"/api/v1/productos/{env['prod']}",
                      headers=h).json()["clave_sae"] in (None, "")
    # y como quedó cerrada, pedirla otra vez SÍ encola (nada se creó allá)
    assert _pedir(client, h, empresas=["02"]).status_code == 201


def test_alta_reclamada_sin_reporte_se_cierra_diciendo_que_hay_que_revisar(client, env, auth_as, conector):
    """Una alta que el conector tomó y nunca reportó NO se re-encola: pudo
    haber entrado a SAE. Se cierra como ERROR y el motivo manda a revisar."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h).json()
    auth_as(conector)
    client.get("/api/v1/productos/alta-sae/pendiente", headers=h)
    auth_as(env["admin"])
    with SessionLocal() as s:
        s.query(SolicitudAltaSae).filter(
            SolicitudAltaSae.id == uuid.UUID(sol["id"])
        ).update({"iniciada_at": datetime.now(timezone.utc) - timedelta(hours=2)})
        s.commit()

    lista = client.get("/api/v1/productos/alta-sae", headers=h).json()["items"]
    cerrada = next(x for x in lista if x["id"] == sol["id"])
    assert cerrada["estado"] == "ERROR"
    assert "REVISA EN SAE" in cerrada["motivo"]


def test_alta_exige_permiso_de_catalogo(client, env, auth_as):
    """Pedir el alta es trabajo de catálogo: un tomador no la pide."""
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    assert _pedir(client, h).status_code == 403


def test_alta_crea_el_producto_del_catalogo_o_reusa_el_del_mismo_nombre(client, env, auth_as):
    """El producto nace AQUÍ, no en quien pide: así la conexión del bot puede
    dar de alta sin que se le preste `producto:gestionar`. Y si ya hay uno con
    el mismo nombre exacto, se reusa — dos productos iguales son justo lo que el
    catálogo existe para evitar."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _pedir(client, h, clave="PERANUEVA", descripcion="PERA DE AGUA")
    assert r.status_code == 201, r.text
    pid = r.json()["producto_id"]
    assert pid, r.json()
    prod = client.get(f"/api/v1/productos/{pid}", headers=h).json()
    assert prod["nombre"] == "PERA DE AGUA" and prod["clave_sae"] == "PERANUEVA"
    assert prod["unidad_base"] == "KILO" and prod["clave_sat"] == "50161509"
    # …y CON su esquema de impuesto: sin él el producto contestaría 0% de IVA
    # por el respaldo, o sea un exento fabricado en silencio
    assert prod["esquema_impuesto_id"] == str(env["esq2"]), prod

    # el mismo nombre no crea otro producto: se reusa (aunque la clave difiera)
    r2 = _pedir(client, h, clave="PERAOTRA", descripcion="  pera de agua ")
    assert r2.status_code == 201
    assert r2.json()["producto_id"] == pid

    # crear_producto=False deja la solicitud suelta, para ligarla a mano
    r3 = _pedir(client, h, clave="SUELTA", descripcion="MANGO SUELTO",
                crear_producto=False)
    assert r3.status_code == 201 and r3.json()["producto_id"] is None


def test_el_permiso_angosto_alcanza_para_pedir_el_alta(client, env, auth_as):
    """`producto:alta_sae` es el permiso de la conexión del bot: encola y crea
    el producto nuevo, sin abrirle el resto del catálogo."""
    from app.models import Permission, Role, RolePermission

    auth_as(env["admin"]); h = _hdr(env["admin"])
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        assert db.query(Permission).filter(
            Permission.id == "producto:alta_sae").one_or_none() is not None, \
            "el permiso tiene que estar sembrado en el catálogo (migración 0082)"
        rol = Role(tenant_id=env["tenant_id"], nombre=f"SOLO-ALTA-{suffix}",
                   descripcion="solo pedir altas")
        db.add(rol); db.flush()
        for pid in ("menu:productos", "producto:alta_sae"):
            db.add(RolePermission(role_id=rol.id, permission_id=pid))
        u = User(email=f"alta-{suffix}@t.test", auth_user_id=f"sub-alta-{suffix}",
                 full_name="solo alta")
        db.add(u); db.flush()
        m = Membership(tenant_id=env["tenant_id"], user_id=u.id, role_id=rol.id)
        db.add(m); db.flush(); db.commit()
        quien = {"sub": u.auth_user_id, "email": u.email, "tenant_id": env["tenant_id"]}

        auth_as(quien)
        r = _pedir(client, _hdr(quien), clave="CHILEALTA", descripcion="CHILE NUEVO")
        assert r.status_code == 201, r.text
        assert r.json()["producto_id"], "con este permiso también nace el producto"
        # …y nada más: editar el catálogo sigue cerrado
        assert client.patch(f"/api/v1/productos/{r.json()['producto_id']}",
                            headers=_hdr(quien), json={"nombre": "OTRO"}).status_code == 403
    finally:
        db.query(Membership).filter(Membership.id == m.id).delete()
        db.query(User).filter(User.id == u.id).delete()
        db.query(RolePermission).filter(RolePermission.role_id == rol.id).delete()
        db.query(Role).filter(Role.id == rol.id).delete()
        db.commit(); db.close()


def test_impuestos_por_clave_contesta_por_lote_y_no_calla_lo_que_no_conoce(client, env, auth_as):
    """El bot resuelve impuestos contra SAE clave por clave desde seis
    funciones. Esto contesta lo mismo por lote — y una clave que no existe
    vuelve con `encontrado: false` en vez de un 0% silencioso: «no lleva IVA» y
    «no sé quién es» no son la misma respuesta."""
    from app.models import EsquemaImpuesto, Producto

    auth_as(env["admin"]); h = _hdr(env["admin"])
    with SessionLocal() as s:
        esq = EsquemaImpuesto(tenant_id=env["tenant_id"], codigo="IVA16",
                              nombre="IVA 16%", iva_tasa=Decimal("0.16"))
        s.add(esq); s.flush()
        s.query(Producto).filter(Producto.id == uuid.UUID(env["prod"])).update(
            {"clave_sae": "AJOKG", "esquema_impuesto_id": esq.id})
        s.commit()

    r = client.post("/api/v1/productos/impuestos", headers=h,
                    json={"claves": ["AJOKG", "  ajokg ", "NOEXISTE"]})
    assert r.status_code == 200, r.text
    por = {x["clave"]: x for x in r.json()}
    assert por["AJOKG"]["encontrado"] is True
    assert float(por["AJOKG"]["iva"]) == 0.16 and por["AJOKG"]["esquema"] == "IVA16"
    assert por["  ajokg "]["encontrado"] is True, "la clave casa sin importar caja ni espacios"
    assert por["NOEXISTE"]["encontrado"] is False
    assert float(por["NOEXISTE"]["iva"]) == 0.0

    # también casa por SKU: el bot llama con lo que traiga el documento
    sku = client.get(f"/api/v1/productos/{env['prod']}", headers=h).json()["sku"]
    r2 = client.post("/api/v1/productos/impuestos", headers=h, json={"claves": [sku]})
    assert r2.json()[0]["encontrado"] is True


def test_dos_productos_pueden_compartir_la_clave_sae(client, env, auth_as):
    """Regla del dueño (23-sep): una clave de SAE ampara varios productos. Antes
    esto era un 409 («ya es de CEBOLLA BLANCA») y dejaba partidas sin clave."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    otro = client.post("/api/v1/productos", headers=h, json={
        "sku": "", "nombre": "Ajo primera", "clave_sat": "01010101", "unidad_sat": "KGM",
        "clave_sae": " ajokg ", "esquema_impuesto_id": str(env["esq2"])})
    assert otro.status_code in (200, 201), otro.text
    otro = otro.json()
    assert otro["clave_sae"] == "AJOKG"
    r = client.patch(f"/api/v1/productos/{env['prod']}", headers=h,
                     json={"clave_sae": "ajokg"})
    assert r.status_code == 200, r.text
    assert r.json()["clave_sae"] == "AJOKG"
