"""Ajustes › Empresas — la lista del grupo y editar sin cambiarte de empresa.

GET  /empresa/grupo      → las empresas del usuario + qué le falta a cada una.
PUT  /empresa/{tenant_id} → edita CUALQUIERA de ellas sin tocar la empresa activa.

La barrera de la edición NO es el permiso en la empresa activa (ese es de otra
empresa), sino la membresía en la empresa DESTINO: lo mismo que podría hacer si
se cambiara a ella con el switcher.
"""
import random
import uuid

import pytest

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Membership, Role, Tenant, User
from app.services.rfc import _digito_verificador


def _rfc_random() -> str:
    """RFC único y con dígito verificador correcto.

    tenants.rfc es UNIQUE global y la BD de test persiste entre corridas, así que
    tiene que ser aleatorio; y PUT /empresa valida el dígito verificador del SAT,
    así que el último carácter se calcula, no se inventa.
    """
    base = f"ZZY{random.randint(0, 999999):06d}{uuid.uuid4().hex[:2].upper()}"
    return base + _digito_verificador(base)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"m": [], "u": [], "t": []}
    try:
        roles = {
            r.nombre: r
            for r in db.query(Role)
            .filter(Role.es_preset.is_(True), Role.nombre.in_(("OWNER", "CAJERO")))
            .all()
        }

        def _tenant(slug, nombre, parent=None):
            t = Tenant(
                slug=f"{slug}-{suffix}",
                legal_name=nombre,
                rfc=_rfc_random(),
                regimen_fiscal_sat="601",
                domicilio_fiscal_cp="44100",
                tier="PRINCIPAL" if parent is None else "SUB",
                parent_tenant_id=parent,
                status="ACTIVE",
            )
            db.add(t)
            db.flush()
            created["t"].append(t.id)
            return t

        raiz = _tenant("grp", "Grupo Raiz SA")
        hija = _tenant("hija", "Empresa Hija SA", parent=raiz.id)
        # Empresa de OTRO dueño: el usuario no tiene membresía aquí.
        ajena = _tenant("ajena", "Empresa Ajena SA")

        def _user(label, membresias):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u)
            db.flush()
            created["u"].append(u.id)
            for tenant, role in membresias:
                m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
                db.add(m)
                db.flush()
                created["m"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": raiz.id}

        # El dueño manda en las DOS empresas del grupo.
        owner = _user("owner", [(raiz, roles["OWNER"]), (hija, roles["OWNER"])])
        # CAJERO no tiene `membership:gestionar`: ve su empresa, no la edita.
        cajero = _user("cajero", [(raiz, roles["CAJERO"])])
        db.commit()
        yield {
            "owner": owner,
            "cajero": cajero,
            "raiz": raiz.id,
            "hija": hija.id,
            "ajena": ajena.id,
        }
    finally:
        for mid in created["m"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["u"]:
            db.query(User).filter(User.id == uid).delete()
        # Las hijas primero: `tenants.parent_tenant_id` apunta a la raíz.
        for tid in reversed(created["t"]):
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit()
        db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"],
            email=user["email"],
            role="authenticated",
            claims={"sub": user["sub"]},
        )

    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _h(tenant_id):
    return {"X-Tenant-Id": str(tenant_id)}


def _payload(nombre="Empresa Hija Renombrada SA", rfc=None, cp="45010"):
    return {
        "legal_name": nombre,
        "rfc": rfc or _rfc_random(),
        "regimen_fiscal_sat": "612",
        "domicilio_fiscal_cp": cp,
        "domicilio_fiscal": {"calle": "Av. Vallarta 100", "estado": "JAL"},
    }


def test_grupo_lista_las_empresas_del_usuario(client, env, auth_as):
    auth_as(env["owner"])
    r = client.get("/api/v1/empresa/grupo", headers=_h(env["raiz"]))
    assert r.status_code == 200, r.text
    data = r.json()

    ids = [e["tenant_id"] for e in data["empresas"]]
    assert ids == [str(env["raiz"]), str(env["hija"])]   # principal primero
    assert str(env["ajena"]) not in ids                  # sin membresía = invisible
    assert data["grupo_total"] == 2
    assert data["puede_agregar"] is True                 # es OWNER y hay cupo

    raiz, hija = data["empresas"]
    assert raiz["es_actual"] is True and raiz["es_principal"] is True
    assert hija["es_actual"] is False and hija["es_principal"] is False
    assert hija["en_grupo"] is True
    # Datos sembrados completos, pero sin logo/series/correo todavía.
    assert raiz["datos_fiscales"] is True
    assert (raiz["logo"], raiz["series"], raiz["correo"]) == (False, False, False)
    assert raiz["puede_editar"] is True and raiz["rol"] == "OWNER"


def test_sin_permiso_ve_su_empresa_pero_no_la_edita(client, env, auth_as):
    auth_as(env["cajero"])
    data = client.get("/api/v1/empresa/grupo", headers=_h(env["raiz"])).json()
    assert [e["tenant_id"] for e in data["empresas"]] == [str(env["raiz"])]
    assert data["empresas"][0]["puede_editar"] is False
    assert data["puede_agregar"] is False                 # agregar sigue siendo del OWNER

    r = client.put(f"/api/v1/empresa/{env['raiz']}", headers=_h(env["raiz"]), json=_payload())
    assert r.status_code == 403


def test_owner_edita_otra_empresa_sin_cambiarse(client, env, auth_as):
    auth_as(env["owner"])
    rfc = _rfc_random()
    # Parado en la RAÍZ, edita la HIJA.
    r = client.put(
        f"/api/v1/empresa/{env['hija']}",
        headers=_h(env["raiz"]),
        json=_payload(rfc=rfc.lower()),
    )
    assert r.status_code == 200, r.text
    assert r.json()["rfc"] == rfc                        # normalizado a mayúsculas

    db = SessionLocal()
    try:
        hija = db.query(Tenant).filter(Tenant.id == env["hija"]).one()
        assert hija.legal_name == "Empresa Hija Renombrada SA"
        assert hija.regimen_fiscal_sat == "612"
        assert hija.domicilio_fiscal["calle"] == "Av. Vallarta 100"
        assert hija.domicilio_fiscal["pais"] == "México"  # forzado server-side
    finally:
        db.close()

    # Y la empresa ACTIVA del usuario no se movió.
    me = client.get("/api/v1/auth/me", headers=_h(env["raiz"])).json()
    assert me["active_tenant"]["tenant_id"] == str(env["raiz"])


def test_no_puede_editar_una_empresa_ajena(client, env, auth_as):
    auth_as(env["owner"])
    r = client.put(f"/api/v1/empresa/{env['ajena']}", headers=_h(env["raiz"]), json=_payload())
    assert r.status_code == 403
    # Ser OWNER en el grupo propio no alcanza para tocar una empresa de fuera.
    assert "administrar" in r.json()["detail"]


def test_rfc_invalido_no_pasa_por_la_puerta_de_atras(client, env, auth_as):
    auth_as(env["owner"])
    r = client.put(
        f"/api/v1/empresa/{env['hija']}",
        headers=_h(env["raiz"]),
        json=_payload(rfc="NO-ES-UN-RFC"),
    )
    assert r.status_code == 422


def test_el_sello_por_empresa_respeta_la_membresia(client, env, auth_as):
    """Subir/leer el CSD desde la lista pasa por la MISMA puerta que editar."""
    auth_as(env["owner"])
    ajena = client.get(f"/api/v1/empresa/{env['ajena']}/csd", headers=_h(env["raiz"]))
    assert ajena.status_code == 403

    # La hija sí pasa la puerta; después es cosa de Facturama (503 si no está
    # configurado en el entorno de tests).
    propia = client.get(f"/api/v1/empresa/{env['hija']}/csd", headers=_h(env["raiz"]))
    assert propia.status_code in (200, 503)

    auth_as(env["cajero"])
    sin_permiso = client.get(f"/api/v1/empresa/{env['raiz']}/csd", headers=_h(env["raiz"]))
    assert sin_permiso.status_code == 403


def _por_id(data, tenant_id):
    return next(e for e in data["empresas"] if e["tenant_id"] == str(tenant_id))


def test_color_automatico_y_elegido(client, env, auth_as):
    auth_as(env["owner"])
    h = _h(env["raiz"])

    # De arranque, automático: el backend no guarda color y el front lo deriva.
    data = client.get("/api/v1/empresa/grupo", headers=h).json()
    assert _por_id(data, env["hija"])["color"] is None

    r = client.put(f"/api/v1/empresa/{env['hija']}/color", headers=h, json={"color": "#0F7B6C"})
    assert r.status_code == 200, r.text
    assert r.json()["color"] == "#0f7b6c"                 # normalizado

    data = client.get("/api/v1/empresa/grupo", headers=h).json()
    assert _por_id(data, env["hija"])["color"] == "#0f7b6c"
    # Y el switcher del Topbar lo ve (viaja en /auth/me).
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert {t["tenant_id"]: t["color"] for t in me["tenants"]}[str(env["hija"])] == "#0f7b6c"

    # Volver a automático.
    r = client.put(f"/api/v1/empresa/{env['hija']}/color", headers=h, json={"color": None})
    assert r.json()["color"] is None
    assert _por_id(client.get("/api/v1/empresa/grupo", headers=h).json(), env["hija"])["color"] is None


def test_color_fuera_del_catalogo_y_empresa_ajena(client, env, auth_as):
    auth_as(env["owner"])
    h = _h(env["raiz"])
    # Un color libre dejaría la inicial blanca ilegible: solo el catálogo.
    assert client.put(f"/api/v1/empresa/{env['hija']}/color", headers=h,
                      json={"color": "#ffff00"}).status_code == 422
    # Y el color pasa por la misma puerta que editar.
    assert client.put(f"/api/v1/empresa/{env['ajena']}/color", headers=h,
                      json={"color": "#2c3e50"}).status_code == 403
