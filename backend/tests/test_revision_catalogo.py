"""Revisión del catálogo — qué productos son el mismo y cómo quedarían unidos.

Reglas del dueño (2-oct-2026), con la espinaca como caso: el nombre no lleva la
unidad, cada unidad trae su clave de SAE, un empaque con gramaje no se vende por
kilo, y una variedad o un gramaje distinto es otro producto.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import ClaveSae, Membership, Producto, Role, Tenant, User
from app.services.revision_catalogo import (Catalogo, ProductoRev, armar_grupos, es_empaque, proponer,
                                            raiz_y_unidad)


# ── el motor, sin base de datos ──

def _p(sku, nombre, base, clave, pres=None, ventas=0, borradores=None, alta=date(2026, 8, 27)):
    return ProductoRev(id=sku, sku=sku, nombre=nombre, unidad_base=base, presentaciones=pres or {base: 1},
                       clave_sae=clave, ventas=ventas, borradores=borradores or {}, alta=alta)


CAT = Catalogo(
    en_sae={c: ["02"] for c in ("ESPINACASKG", "ESPINACAPZA", "ESPI-FRUT-193", "ESPI-FRUT-2608",
                                "TELIMONMJO100G", "CHAYOTESINESPIKG", "CHAY-FRUT-119", "PEREJILKG", "PEREJILMJ")},
    facturas={"ESPINACASKG": 1052, "ESPI-FRUT-193": 128, "ESPINACAPZA": 449},
)


@pytest.mark.parametrize("nombre,base,raiz,unidad", [
    ("SANDIA PZA", "KILO", "SANDIA", "PIEZA"),
    ("ESPINACA MANOJO DE 1 KG", "KILO", "ESPINACA", "MANOJO"),
    ("PAPA 25 KG", "KILO", "PAPA 25 KG", None),          # número + unidad es tamaño
    ("SANDIA (11 - 12 KG)", "KILO", "SANDIA", None),     # el paréntesis con peso es calibre
    ("PASTA (CODO)", "PIEZA", "PASTA CODO", None),       # el paréntesis sin número es otro producto
    ("SALCHICHA DE PAVO 1 KG", "PIEZA", "SALCHICHA DE PAVO 1 KG", None),  # sólo en KILO sobra el «1 KG»
])
def test_raiz_y_unidad(nombre, base, raiz, unidad):
    assert raiz_y_unidad(nombre, base) == (raiz, unidad)


@pytest.mark.parametrize("nombre,esperado", [
    ("ESPINACA BABY ORG PAQ 454GR", True),
    ("GRANOLA BOLSA 500 GR", True),
    ("NUEZ SIN CASCARA (1000G)", False),      # un empaque de un kilo sí se vende por kilo
    ("CANELA RAJA PAQ 1 KG", False),
    ("ESPINACA", False),
])
def test_es_empaque(nombre, esperado):
    assert es_empaque(nombre) is esperado


def _espinacas():
    return [
        _p("00000323", "ESPINACA MANOJO DE 1 KG", "KILO", "ESPINACASKG",
           {"KILO": 1, "PIEZA": {"factor": 1, "clave_sae": "ESPINACAPZA"}}, ventas=173),
        _p("00010963", "ESPINACA MANOJO DE 1 KG", "KILO", "ESPI-FRUT-193", alta=date(2026, 9, 22)),
        _p("00010761", "ESPINACA PZA", "PIEZA", "ESPINACAPZA", ventas=7, borradores={"PIEZA": ["RZ1"]}),
        _p("00010965", "ESPINACA PZA", "PIEZA", "ESPINACAPZA", alta=date(2026, 9, 22)),
        _p("00000106", "ESPINACA BABY ORG PAQ 454GR", "PIEZA", "ESPINACAPZA",
           {"PIEZA": 1, "KILO": {"factor": 1, "clave_sae": "ESPINACABABYKG"}}, ventas=38,
           borradores={"KILO": ["RZEHMOVH10", "RZEHMOVH14"]}),
        _p("00010964", "ESPINACA BABY ORG PAQ 454GR", "PIEZA", "ESPI-FRUT-2608", alta=date(2026, 9, 22)),
    ]


def _grupo(grupos, clave):
    return next(g for g in grupos if g["clave"] == clave)


def test_la_espinaca_queda_en_un_producto_con_dos_unidades():
    g = _grupo(armar_grupos(_espinacas(), CAT), "R:ESPINACA")
    assert g["tipo"] == "UNIDADES" and len(g["miembros"]) == 4
    pr = proponer(g["miembros"], CAT, tipo=g["tipo"], raiz=g["raiz"])
    assert pr["queda_sku"] == "00000323"          # el que más vende
    assert pr["nombre_final"] == "ESPINACA"       # sin «MANOJO DE 1 KG»
    assert {u["unidad"]: u["clave"] for u in pr["unidades"]} == {"KILO": "ESPINACASKG", "PIEZA": "ESPINACAPZA"}
    # KILO: gana la de formato nuevo y más facturada; la vieja queda como alternativa
    kilo = pr["unidades"][0]
    assert kilo["unidad"] == "KILO" and [a["clave"] for a in kilo["alternativas"]] == ["ESPI-FRUT-193"]
    assert sorted(pr["se_unen"]) == ["00010761", "00010963", "00010965"]
    assert not pr["bloqueos"]
    assert any("00010761" in a for a in pr["alertas"])   # sus borradores no se mueven


def test_la_espinaca_baby_es_otro_producto_y_deja_de_venderse_por_kilo():
    g = _grupo(armar_grupos(_espinacas(), CAT), "R:ESPINACA BABY ORG PAQ 454GR")
    pr = proponer(g["miembros"], CAT, tipo=g["tipo"], raiz=g["raiz"])
    assert pr["quitar"] == ["KILO"] and pr["unidad_base"] == "PIEZA"
    assert [u["unidad"] for u in pr["unidades"]] == ["PIEZA"]
    # no se puede quitar KILO con remisiones en borrador que lo venden
    assert pr["bloqueos"] and "RZEHMOVH10" in pr["bloqueos"][0]


def test_gemelos_identicos_se_queda_el_que_vende():
    ms = [_p("00010916", "CHAYOTE", "KILO", "CHAY-FRUT-119", alta=date(2026, 9, 22)),
          _p("00000300", "CHAYOTE", "KILO", "CHAYOTESINESPIKG", ventas=401)]
    g = armar_grupos(ms, CAT)[0]
    assert g["tipo"] == "GEMELOS"
    pr = proponer(g["miembros"], CAT, tipo=g["tipo"], raiz=g["raiz"])
    assert pr["queda_sku"] == "00000300" and pr["unidades"][0]["clave"] == "CHAYOTESINESPIKG"


def test_gramaje_distinto_no_se_agrupa():
    ms = [_p("1", "FRIJOL", "KILO", "FRIJOLKG"), _p("2", "FRIJOL 900 G", "PIEZA", "FRIJOL900PZ")]
    assert not [g for g in armar_grupos(ms, CAT) if g["tipo"] != "EMPAQUE"]


def test_empaque_por_kilo_pasa_a_la_unidad_de_su_nombre_y_conserva_su_clave():
    ms = [_p("00010164", "TE LIMON MANOJO DE 100G", "KILO", "TELIMONMJO100G", borradores={"KILO": ["RFMAFAN22"]})]
    g = armar_grupos(ms, CAT)[0]
    assert g["tipo"] == "EMPAQUE"
    pr = proponer(g["miembros"], CAT, tipo=g["tipo"], raiz=g["raiz"])
    assert pr["unidad_base"] == "MANOJO" and pr["quitar"] == ["KILO"]
    assert pr["unidades"][0]["clave"] == "TELIMONMJO100G"
    assert "RFMAFAN22" in pr["bloqueos"][0]


def test_lo_que_cambia_la_persona_manda():
    g = _grupo(armar_grupos(_espinacas(), CAT), "R:ESPINACA")
    pr = proponer(g["miembros"], CAT, tipo=g["tipo"], raiz=g["raiz"], queda_sku="00010761",
                  claves={"KILO": "ESPI-FRUT-193"}, quitar=["PIEZA"], nombre_final="Espinaca fresca")
    assert pr["queda_sku"] == "00010761" and pr["nombre_final"] == "ESPINACA FRESCA"
    assert {u["unidad"]: u["clave"] for u in pr["unidades"]} == {"KILO": "ESPI-FRUT-193"}
    assert pr["unidad_base"] == "KILO"


def test_dos_unidades_con_la_misma_clave_se_avisan():
    ms = [_p("00000378", "PEREJIL", "KILO", "PEREJILKG", {"KILO": 1, "MANOJO": {"factor": 1, "clave_sae": "PEREJILMJ"}}),
          _p("00010771", "PEREJIL PZ", "PIEZA", "PEREJILMJ")]
    g = armar_grupos(ms, CAT)[0]
    pr = proponer(g["miembros"], CAT, tipo=g["tipo"], raiz=g["raiz"])
    assert any("misma clave PEREJILMJ" in a for a in pr["alertas"])


# ── la API ──

@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"rev-{suffix}", legal_name="Revision SA",
                   rfc=f"RV{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)

        def _user(nombre_rol, label):
            role = db.query(Role).filter(Role.nombre == nombre_rol, Role.es_preset.is_(True)).one()
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=t.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": t.id}

        admin = _user("ADMIN", "admin")
        tomador = _user("TOMADOR", "tomador")      # ve productos pero no gestiona el catálogo
        for sku, nombre, base, clave, pres in (
            ("00000323", "ESPINACA MANOJO DE 1 KG", "KILO", "ESPINACASKG",
             {"KILO": 1, "PIEZA": {"factor": 1, "clave_sae": "ESPINACAPZA"}}),
            ("00010963", "ESPINACA MANOJO DE 1 KG", "KILO", "ESPI-FRUT-193", {"KILO": 1}),
            ("00010965", "ESPINACA PZA", "PIEZA", "ESPINACAPZA", {"PIEZA": 1}),
        ):
            db.add(Producto(tenant_id=t.id, sku=sku, nombre=nombre, unidad_base=base, presentaciones=pres,
                            clave_sae=clave, clave_sat="50406200", unidad_sat="KGM"))
        for clave in ("ESPINACASKG", "ESPINACAPZA", "ESPI-FRUT-193"):
            db.add(ClaveSae(tenant_id=t.id, empresa="02", clave=clave, descripcion=clave, activa=True))
        db.commit()
        yield {"admin": admin, "tomador": tomador, "tenant_id": t.id}
    finally:
        for table in ("revision_catalogo", "claves_sae", "productos"):
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


URL = "/api/v1/revision-catalogo"


def _espinaca(client, h):
    r = client.get(URL, headers=h)
    assert r.status_code == 200, r.text
    return next(g for g in r.json()["grupos"] if g["clave"] == "R:ESPINACA")


def test_lista_el_grupo_con_su_propuesta(client, env, auth_as):
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    g = _espinaca(client, h)
    assert g["estado"] == "PENDIENTE" and g["tipo"] == "UNIDADES"
    assert g["propuesta"]["nombre_final"] == "ESPINACA"
    assert {u["unidad"]: u["clave"] for u in g["propuesta"]["unidades"]} == {
        "KILO": "ESPINACASKG", "PIEZA": "ESPINACAPZA"}
    assert g["miembros"][0]["sku"] == g["propuesta"]["queda_sku"]   # el que se queda va primero


def test_decidir_exige_gestionar_el_catalogo(client, env, auth_as):
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    r = client.post(f"{URL}/decision", headers=h, json={"grupo": "R:ESPINACA", "estado": "APROBADO"})
    assert r.status_code == 403


def test_aprobar_guarda_lo_ajustado_y_se_puede_regresar(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    # vista previa con otra clave para KILO: no guarda nada
    r = client.post(f"{URL}/propuesta", headers=h, json={"grupo": "R:ESPINACA", "claves": {"KILO": "ESPI-FRUT-193"}})
    assert r.status_code == 200, r.text
    assert r.json()["propuesta"]["unidades"][0]["clave"] == "ESPI-FRUT-193"
    assert _espinaca(client, h)["estado"] == "PENDIENTE"

    r = client.post(f"{URL}/decision", headers=h, json={
        "grupo": "R:ESPINACA", "estado": "APROBADO", "claves": {"KILO": "ESPI-FRUT-193"}, "nota": "ok dueño"})
    assert r.status_code == 200, r.text
    g = _espinaca(client, h)
    assert g["estado"] == "APROBADO" and g["decision"]["nota"] == "ok dueño"
    assert g["decision"]["por"] == env["admin"]["email"]
    assert g["propuesta"]["unidades"][0]["clave"] == "ESPI-FRUT-193"   # se recuerda el ajuste

    r = client.post(f"{URL}/decision", headers=h, json={"grupo": "R:ESPINACA", "estado": "PENDIENTE"})
    assert r.status_code == 200
    g = _espinaca(client, h)
    assert g["estado"] == "PENDIENTE" and g["decision"] is None


def test_clave_que_no_existe_en_sae_no_se_acepta(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post(f"{URL}/decision", headers=h, json={
        "grupo": "R:ESPINACA", "estado": "APROBADO", "claves": {"KILO": "INVENTADAKG"}})
    assert r.status_code == 422 and "INVENTADAKG" in r.text


def test_si_el_grupo_cambia_la_decision_queda_desactualizada(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert client.post(f"{URL}/decision", headers=h,
                       json={"grupo": "R:ESPINACA", "estado": "APROBADO"}).status_code == 200
    with SessionLocal() as s:   # alguien da de alta otra espinaca
        s.add(Producto(tenant_id=env["tenant_id"], sku="00011999", nombre="ESPINACA", unidad_base="KILO",
                       presentaciones={"KILO": 1}, clave_sae="ESPINACASKG", clave_sat="50406200",
                       unidad_sat="KGM"))
        s.commit()
    g = _espinaca(client, h)
    assert g["estado"] == "PENDIENTE" and g["decision"]["desactualizada"] is True
    assert g["propuesta"]["queda_sku"] == "00011999"     # el de nombre limpio


def test_grupo_que_ya_no_existe_es_404(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post(f"{URL}/decision", headers=h, json={"grupo": "R:NO EXISTE", "estado": "RECHAZADO"})
    assert r.status_code == 404
