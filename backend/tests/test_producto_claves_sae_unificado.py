"""Una clave de SAE por unidad, desde el editor de producto (2-oct-2026).

Reglas del dueño que se prueban aquí:

  · En el tenant dueño de SAE ningún producto ACTIVO se guarda sin la clave de
    cada unidad: la base con `clave_sae` y cada presentación con la suya.
  · La categoría se elige en el alta; ya no cae sola en «Sin categorizar».
  · «Crear clave nueva en SAE» viaja con el producto (`altas_sae`) y se encola
    en la MISMA transacción; donde la clave ya existe es una liga, no un alta.
  · «Así está en SAE» lee el artículo en vivo y nunca contesta un 5xx.
  · El estado de las claves del catálogo (chips) sale en pocas consultas.

SAE es falso en todas: lo que se prueba es el contrato y los candados.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.api.v1 import productos as productos_api
from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.models import ClaveSae, EsquemaImpuesto, Producto, SolicitudAltaSae
from app.services import sae_lectura

from .conftest import categoria_de_prueba
from .test_alta_sae_api import _hdr, auth_as, env  # noqa: F401


def _cuerpo(env, **extra):
    body = {
        "nombre": f"TORTILLA BURRERA {uuid.uuid4().hex[:6]}",
        "clave_sat": "50221200", "unidad_sat": "KGM",
        "esquema_impuesto_id": str(env["esq2"]),
        "categoria_id": categoria_de_prueba(env["tenant_id"]),
        "unidad_base": "KILO", "presentaciones": {"KILO": 1},
        "clave_sae": "TORTILLABURREKG",
        "forzar": True,
    }
    body.update(extra)
    return body


def _alta(clave, unidad="KILO", **extra):
    a = {"clave": clave, "unidad": unidad, "descripcion": "TORTILLA BURRERA",
         "linea": "ABARR"}
    a.update(extra)
    return a


def _crear(client, h, env, **extra):
    return client.post("/api/v1/productos", headers=h, json=_cuerpo(env, **extra))


def _solicitudes(tenant_id, clave=None):
    with SessionLocal() as s:
        q = s.query(SolicitudAltaSae).filter(SolicitudAltaSae.tenant_id == tenant_id)
        if clave:
            q = q.filter(SolicitudAltaSae.clave == clave)
        return q.all()


def _espejo(tenant_id, clave, **empresas):
    """empresas: e02=True (activa) / e04=False (de baja)."""
    with SessionLocal() as s:
        for k, activa in empresas.items():
            s.add(ClaveSae(tenant_id=tenant_id, empresa=k[1:], clave=clave,
                           descripcion=f"{clave} EN {k[1:]}", activa=activa))
        s.commit()


@pytest.fixture
def sin_sae(monkeypatch):
    """El tenant de la prueba deja de ser el dueño de SAE."""
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", str(uuid.uuid4()))


# ─── (D) sin clave SAE no se guarda ──────────────────────────────────────────

def test_alta_sin_clave_base_no_se_guarda(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, clave_sae=None)
    assert r.status_code == 422
    assert r.json()["detail"] == "Falta la clave SAE de KILO"
    r = _crear(client, h, env, clave_sae="   ")       # vacía con espacios = sin clave
    assert r.status_code == 422


def test_cada_presentacion_necesita_su_clave(client, env, auth_as):
    """KILO y PIEZA son dos artículos en SAE: la pieza no sale con la clave del
    kilo. Se listan todas las que faltan, en el orden del producto."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    nombre = f"TORTILLA {uuid.uuid4().hex[:6]}"
    pres = {"KILO": 1, "PIEZA": {"factor": 0.05}, "CAJA": {"factor": 20, "clave_sae": "  "}}
    r = _crear(client, h, env, nombre=nombre, presentaciones=pres)
    assert r.status_code == 422
    assert r.json()["detail"] == "Falta la clave SAE de PIEZA, CAJA"
    with SessionLocal() as s:
        assert s.query(Producto).filter(Producto.tenant_id == env["tenant_id"],
                                        Producto.nombre == nombre).count() == 0

    pres = {"KILO": 1, "PIEZA": {"factor": 0.05, "clave_sae": "tortillaburrepz"},
            "CAJA": {"factor": 20, "clave_sae": "TORTILLABURRECJ"}}
    r = _crear(client, h, env, presentaciones=pres)
    assert r.status_code == 201, r.text
    assert r.json()["presentaciones"]["PIEZA"]["clave_sae"] == "TORTILLABURREPZ"
    assert r.json()["altas_sae"] == []


def test_inactivo_no_exige_clave(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, clave_sae=None, activo=False)
    assert r.status_code == 201, r.text


def test_tenant_sin_sae_no_exige_clave(client, env, auth_as, sin_sae):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, clave_sae=None,
               presentaciones={"KILO": 1, "PIEZA": {"factor": 0.05}})
    assert r.status_code == 201, r.text


def test_editar_lo_que_define_las_claves_exige_el_producto_completo(client, env, auth_as):
    """Se mide el producto que QUEDA, y sólo si se tocó lo que define las
    claves: el producto viejo con huecos (el «Ajo kilo» del fixture no tiene
    clave) se sigue pudiendo renombrar."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.patch(f"/api/v1/productos/{env['prod']}", headers=h,
                     json={"nombre": "Ajo morado"})
    assert r.status_code == 200, r.text
    # pero sumarle una unidad sin clave, no; el aviso nombra sólo lo nuevo (el
    # KILO ya venía sin clave y no es lo que este PATCH rompe)
    r = client.patch(f"/api/v1/productos/{env['prod']}", headers=h,
                     json={"presentaciones": {"KILO": 1, "PIEZA": {"factor": 0.05}}})
    assert r.status_code == 422 and r.json()["detail"] == "Falta la clave SAE de PIEZA"

    p = _crear(client, h, env).json()
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h,
                     json={"presentaciones": {"KILO": 1, "PIEZA": {"factor": 0.05}}})
    assert r.status_code == 422 and r.json()["detail"] == "Falta la clave SAE de PIEZA"
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h, json={"clave_sae": None})
    assert r.status_code == 422 and r.json()["detail"] == "Falta la clave SAE de KILO"
    # cambiar la unidad base deja al KILO como presentación sin clave propia
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h,
                     json={"unidad_base": "PIEZA", "clave_sae": "TORTILLABURREPZ"})
    assert r.status_code == 422 and r.json()["detail"] == "Falta la clave SAE de KILO"
    # darlo de baja no exige nada; reactivarlo, sí
    with SessionLocal() as s:
        s.query(Producto).filter(Producto.id == uuid.UUID(p["id"])).update({"clave_sae": None})
        s.commit()
    assert client.patch(f"/api/v1/productos/{p['id']}", headers=h,
                        json={"activo": False}).status_code == 200
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h, json={"activo": True})
    assert r.status_code == 422


def test_llenar_un_hueco_no_se_bloquea_por_otro(client, env, auth_as):
    """El aviso de Remisiones (guardarClavesPendientes) llena la clave BASE con
    PATCH {clave_sae} para destrabar el masivo. Si el producto trae además una
    presentación sin clave (nació por «Nueva presentación» desde la remisión o
    por la importación, que no tienen candado), ese PATCH deja MENOS huecos y
    no se rechaza; sí se rechaza quitar una clave que ya estaba."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    p = _crear(client, h, env).json()
    with SessionLocal() as s:
        s.query(Producto).filter(Producto.id == uuid.UUID(p["id"])).update({
            "clave_sae": None,
            "presentaciones": {"KILO": 1, "PIEZA": {"factor": 8}},
        })
        s.commit()
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h,
                     json={"clave_sae": "chayotekg"})
    assert r.status_code == 200, r.text
    assert r.json()["clave_sae"] == "CHAYOTEKG"
    # corregir la base a otra clave (la que sí factura Tabasco) tampoco choca
    # con la PIEZA que sigue sin clave
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h,
                     json={"clave_sae": "CHAYOTEKG03"})
    assert r.status_code == 200, r.text
    # lo que sigue prohibido: quitarle la clave que ya tenía
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h, json={"clave_sae": None})
    assert r.status_code == 422 and r.json()["detail"] == "Falta la clave SAE de KILO"
    # y llenar el último hueco deja el producto completo
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h, json={
        "presentaciones": {"KILO": 1, "PIEZA": {"factor": 8, "clave_sae": "CHAYOTEPZ"}}})
    assert r.status_code == 200, r.text


def test_editar_en_tenant_sin_sae_no_exige_claves(client, env, auth_as, sin_sae):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.patch(f"/api/v1/productos/{env['prod']}", headers=h,
                     json={"presentaciones": {"KILO": 1, "PIEZA": {"factor": 0.05}}})
    assert r.status_code == 200, r.text


# ─── (E) la categoría es obligatoria ─────────────────────────────────────────

def test_la_categoria_se_elige_y_no_se_vacia(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, categoria_id=None)
    assert r.status_code == 422 and r.json()["detail"] == "Elige la categoría del producto"
    p = _crear(client, h, env).json()
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h, json={"categoria_id": None})
    assert r.status_code == 422 and r.json()["detail"] == "Elige la categoría del producto"


# ─── (C) altas_sae: la clave nueva viaja con el producto ─────────────────────

def test_las_altas_se_encolan_con_el_producto(client, env, auth_as):
    """Base y presentación en una sola llamada: el producto ya guarda las dos
    claves y las dos altas quedan PENDIENTES en la cola, con el esquema y la
    clave SAT DEL PRODUCTO (no de quien pide)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    pres = {"KILO": 1, "PIEZA": {"factor": 0.05, "clave_sae": "TORTILLABURREPZ"}}
    r = _crear(client, h, env, presentaciones=pres, clave_sat="50221200", altas_sae=[
        _alta("tortillaburrekg"),
        _alta("TORTILLABURREPZ", unidad="PIEZA", sat_unidad="H87"),
    ])
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["clave_sae"] == "TORTILLABURREKG"
    altas = {a["clave"]: a for a in p["altas_sae"]}
    assert set(altas) == {"TORTILLABURREKG", "TORTILLABURREPZ"}
    for a in altas.values():
        assert a["estado"] == "PENDIENTE" and a["tipo"] == "ALTA" and a["origen"] == "UI"
        assert a["producto_id"] == p["id"]
        assert a["empresas"] == ["02", "03", "04", "05"]
        assert a["datos"]["esquema"] == 2 and a["datos"]["sat"] == "50221200"
        assert a["datos"]["linea"] == "ABARR" and a["datos"]["descripcion"] == "TORTILLA BURRERA"
    assert altas["TORTILLABURREKG"]["datos"]["unidad"] == "KILO"
    assert altas["TORTILLABURREPZ"]["datos"]["unidad"] == "PIEZA"
    assert altas["TORTILLABURREPZ"]["datos"]["sat_unidad"] == "H87"
    assert len(_solicitudes(env["tenant_id"])) == 2
    # GET no repite las altas: sólo las trae la llamada que las encoló
    assert client.get(f"/api/v1/productos/{p['id']}", headers=h).json()["altas_sae"] == []


def test_una_alta_que_no_se_puede_encolar_no_deja_el_producto(client, env, auth_as):
    """Misma transacción: si una clave no sirve, ni el producto ni la otra alta
    quedan guardados."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    nombre = f"TORTILLA {uuid.uuid4().hex[:6]}"
    r = _crear(client, h, env, nombre=nombre,
               altas_sae=[_alta("TORTILLABURREKG"), _alta("OTRACLAVEKG")])
    assert r.status_code == 422
    assert "no es de este producto" in r.json()["detail"]
    assert _solicitudes(env["tenant_id"]) == []
    with SessionLocal() as s:
        assert s.query(Producto).filter(Producto.tenant_id == env["tenant_id"],
                                        Producto.nombre == nombre).count() == 0


@pytest.mark.parametrize("clave", ["ACEITE.OLI750", "TORTILLABURRERAKG", "PIÑA KG!"])
def test_una_clave_que_no_se_puede_escribir_en_sae_no_se_pide(client, env, auth_as, clave):
    """Claves nuevas: sólo [A-Z0-9-], hasta 16 (el regex del escritor). Una
    vieja con punto se puede ligar —el producto la guarda—, no crear."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, clave_sae=clave, altas_sae=[_alta(clave)])
    assert r.status_code == 422
    assert "no se puede crear en SAE" in r.json()["detail"]
    # la misma clave, sin pedir alta (una liga), sí se guarda
    assert _crear(client, h, env, clave_sae="ACEITE.OLI750").status_code == 201


def test_donde_ya_existe_es_liga_y_se_quita(client, env, auth_as):
    """Activa en 02 y 03: el alta va sólo a 04 y 05. De baja en 04 cuenta como
    que falta (el escritor la reporta `ya_existia` sin reactivarla)."""
    _espejo(env["tenant_id"], "TORTILLABURREKG", e02=True, e03=True, e04=False)
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG")])
    assert r.status_code == 201, r.text
    assert [a["empresas"] for a in r.json()["altas_sae"]] == [["04", "05"]]
    # pedir sólo empresas donde ya existe = nada que encolar, aunque haya viva
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG", empresas=["02", "03"])])
    assert r.status_code == 201 and r.json()["altas_sae"] == []
    # y en las que faltan se reusa la viva de arriba
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG", empresas=["05"])])
    assert r.json()["altas_sae"][0]["empresas"] == ["04", "05"]
    assert len(_solicitudes(env["tenant_id"], "TORTILLABURREKG")) == 1


def test_si_existe_en_todas_no_se_encola_nada(client, env, auth_as):
    _espejo(env["tenant_id"], "TORTILLABURREKG", e02=True, e03=True, e04=True, e05=True)
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG")])
    assert r.status_code == 201, r.text
    assert r.json()["altas_sae"] == [] and r.json()["clave_sae"] == "TORTILLABURREKG"
    assert _solicitudes(env["tenant_id"]) == []


def test_una_alta_viva_se_reusa(client, env, auth_as):
    """Idempotente por clave, igual que POST /alta-sae: dos altas vivas
    insertarían dos veces el mismo artículo."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    viva = client.post("/api/v1/productos/alta-sae", headers=h, json={
        "clave": "TORTILLABURREKG", "descripcion": "TORTILLA DEL BOT", "unidad": "KILO",
        "crear_producto": False}).json()
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG"), _alta("TORTILLABURREKG")])
    assert r.status_code == 201, r.text
    assert [a["id"] for a in r.json()["altas_sae"]] == [viva["id"]]
    assert len(_solicitudes(env["tenant_id"], "TORTILLABURREKG")) == 1


def test_una_alta_muerta_no_se_reusa(client, env, auth_as):
    """Una PENDIENTE de hace más de un día se cierra como ERROR antes de mirar
    si hay viva: reusarla sería esperar a un conector que no corrió."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    vieja = client.post("/api/v1/productos/alta-sae", headers=h, json={
        "clave": "TORTILLABURREKG", "descripcion": "X", "crear_producto": False}).json()
    with SessionLocal() as s:
        s.query(SolicitudAltaSae).filter(SolicitudAltaSae.id == uuid.UUID(vieja["id"])).update(
            {"solicitada_at": datetime.now(timezone.utc) - timedelta(days=2)})
        s.commit()
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG")])
    assert r.status_code == 201, r.text
    assert r.json()["altas_sae"][0]["id"] != vieja["id"]


def test_las_empresas_se_validan(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG", empresas=["02", "91"])])
    assert r.status_code == 422 and "Empresa desconocida" in r.json()["detail"]
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG", empresas=["03", "03"])])
    assert r.status_code == 201 and r.json()["altas_sae"][0]["empresas"] == ["03"]


def test_el_esquema_necesita_numero_de_sae(client, env, auth_as):
    with SessionLocal() as s:
        esq = EsquemaImpuesto(tenant_id=env["tenant_id"], codigo="IVA16", nombre="IVA 16%")
        s.add(esq); s.commit()
        esq_id = str(esq.id)
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, esquema_impuesto_id=esq_id,
               altas_sae=[_alta("TORTILLABURREKG")])
    assert r.status_code == 422 and "número de SAE" in r.json()["detail"]
    # sin pedir alta, el mismo producto sí se guarda: el esquema sólo importa al escribir en SAE
    assert _crear(client, h, env, esquema_impuesto_id=esq_id).status_code == 201


def test_la_linea_es_obligatoria(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sin_linea = _alta("TORTILLABURREKG"); sin_linea.pop("linea")
    assert _crear(client, h, env, altas_sae=[sin_linea]).status_code == 422
    assert _crear(client, h, env,
                  altas_sae=[_alta("TORTILLABURREKG", linea="fruve")]).status_code == 422
    assert _crear(client, h, env,
                  altas_sae=[_alta("TORTILLABURREKG", unidad="MANOJO")]).status_code == 422


def test_editar_tambien_encola(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    p = _crear(client, h, env).json()
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h, json={
        "presentaciones": {"KILO": 1, "PIEZA": {"factor": 0.05, "clave_sae": "TORTILLABURREPZ"}},
        "altas_sae": [_alta("TORTILLABURREPZ", unidad="PIEZA")],
    })
    assert r.status_code == 200, r.text
    assert [a["clave"] for a in r.json()["altas_sae"]] == ["TORTILLABURREPZ"]
    assert r.json()["altas_sae"][0]["producto_id"] == p["id"]
    # una edición sin altas contesta la lista vacía
    r = client.patch(f"/api/v1/productos/{p['id']}", headers=h, json={"descripcion": "x"})
    assert r.status_code == 200 and r.json()["altas_sae"] == []


def test_tenant_sin_sae_no_pide_altas(client, env, auth_as, sin_sae):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG")])
    assert r.status_code == 403 and r.json()["detail"] == "Esta empresa no tiene SAE conectado"
    r = client.patch(f"/api/v1/productos/{env['prod']}", headers=h,
                     json={"altas_sae": [_alta("TORTILLABURREKG")]})
    assert r.status_code == 403
    # vacío no es pedir: el editor manda la lista siempre
    assert _crear(client, h, env, altas_sae=[]).status_code == 201
    assert _solicitudes(env["tenant_id"]) == []


def test_pedir_altas_exige_el_permiso_de_catalogo(client, env, auth_as):
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    assert _crear(client, h, env, altas_sae=[_alta("TORTILLABURREKG")]).status_code == 403


# ─── (A) «Así está en SAE» ───────────────────────────────────────────────────

class _SAE:
    """INVE e IMPU falsos. `inve` = {empresa: fila}; lo demás no existe."""

    def __init__(self, inve=None, falla=None):
        self.inve = inve or {}
        self.falla = falla
        self.llamadas = []

    def __call__(self, sql, params=(), timeout=None):
        self.llamadas.append((sql, params))
        if self.falla:
            raise self.falla
        for emp, fila in self.inve.items():
            if f"FROM INVE{emp} " in sql:
                return [fila] if fila else []
        if "FROM IMPU" in sql:
            return [{"codigo": 2, "descripcion": "IVA 0 ", "iva": 0, "ieps": 0},
                    {"codigo": 4, "descripcion": "IVA 16", "iva": 16, "ieps": 0}]
        return []


_RUCULA = {"clave": "RUCULAKG  ", "descripcion": "RUCULA\tKILO ", "unidad": "KG ",
           "linea": "FRUVE", "esquema": 2, "sat": "50407006", "sat_unidad": "KGM",
           "status": "A"}


@pytest.fixture
def sae(monkeypatch):
    productos_api._esquemas_sae_cache.clear()
    falso = _SAE()
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    monkeypatch.setattr(sae_lectura, "consultar", falso)
    yield falso
    productos_api._esquemas_sae_cache.clear()


def _en_sae(client, h, clave):
    return client.get(f"/api/v1/productos/claves-sae/{clave}/en-sae", headers=h)


def test_asi_esta_en_sae(client, env, auth_as, sae):
    """Se leen las empresas donde el espejo tiene la clave. Lo que SAE dice en
    vivo manda: en 03 el espejo la tiene, pero SAE ya no."""
    _espejo(env["tenant_id"], "RUCULAKG", e02=True, e03=True)
    sae.inve = {"02": _RUCULA, "03": None}
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _en_sae(client, h, "%20ruculakg%20")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["clave"] == "RUCULAKG" and out["disponible"] is True and out["motivo"] is None
    assert set(out["empresas"]) == {"02", "03", "04", "05"}
    assert out["empresas"]["02"] == {
        "existe": True, "activa": True, "descripcion": "RUCULA KILO",
        "unidad": "KG", "unidad_canonica": "KILO", "linea": "FRUVE",
        "esquema": 2, "esquema_descripcion": "IVA 0",
        "sat": "50407006", "sat_descripcion": "Rúcula",
        "sat_unidad": "KGM", "sat_unidad_descripcion": "Kilogramo",
    }
    for e in ("03", "04", "05"):
        assert out["empresas"][e]["existe"] is False
    leidas = [sql for sql, _ in sae.llamadas if "FROM INVE" in sql]
    assert len(leidas) == 2 and not any("INVE04" in s or "INVE05" in s for s in leidas)
    # la clave viaja como parámetro, nunca dentro del SQL
    assert all("RUCULAKG" not in sql for sql, _ in sae.llamadas)
    assert ("RUCULAKG",) in [p for _, p in sae.llamadas]


def test_el_espacio_de_adentro_es_parte_de_la_clave(client, env, auth_as, sae):
    """Sólo trim + mayúsculas, como el espejo: una clave vieja «ABC 12» existe
    así en INVE; si se le quitara el espacio no casaría con nada."""
    _espejo(env["tenant_id"], "ABC 12", e02=True)
    sae.inve = {"02": dict(_RUCULA, clave="ABC 12")}
    auth_as(env["admin"]); h = _hdr(env["admin"])
    out = _en_sae(client, h, "%20abc%2012%20").json()
    assert out["clave"] == "ABC 12"
    assert out["empresas"]["02"]["existe"] is True
    assert ("ABC 12",) in [p for _, p in sae.llamadas]


def test_sin_espejo_se_pregunta_a_la_02(client, env, auth_as, sae):
    sae.inve = {"02": dict(_RUCULA, unidad="PZ", status="B", esquema=9, sat="99999999")}
    auth_as(env["admin"]); h = _hdr(env["admin"])
    out = _en_sae(client, h, "RUCULAKG").json()
    e02 = out["empresas"]["02"]
    assert e02["existe"] is True and e02["activa"] is False
    assert e02["unidad_canonica"] == "PIEZA"
    # lo que no está en los catálogos se queda sin descripción, no se inventa
    assert e02["esquema_descripcion"] is None and e02["sat_descripcion"] is None
    leidas = [sql for sql, _ in sae.llamadas if "FROM INVE" in sql]
    assert len(leidas) == 1 and "INVE02" in leidas[0]


def test_los_esquemas_se_guardan_quince_minutos(client, env, auth_as, sae):
    sae.inve = {"02": _RUCULA}
    auth_as(env["admin"]); h = _hdr(env["admin"])
    _en_sae(client, h, "RUCULAKG"); _en_sae(client, h, "RUCULAKG")
    assert sum(1 for sql, _ in sae.llamadas if "FROM IMPU" in sql) == 1


def test_sin_sae_configurado_contesta_lo_del_espejo(client, env, auth_as, sae, monkeypatch):
    _espejo(env["tenant_id"], "RUCULAKG", e03=False)
    monkeypatch.setattr(sae_lectura, "disponible", lambda: False)
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _en_sae(client, h, "RUCULAKG")
    assert r.status_code == 200
    out = r.json()
    assert out["disponible"] is False and "no tiene configurado" in out["motivo"]
    assert out["empresas"]["03"]["existe"] is True and out["empresas"]["03"]["activa"] is False
    assert out["empresas"]["02"]["existe"] is False
    assert sae.llamadas == []


@pytest.mark.parametrize("falla", [
    sae_lectura.SAENoDisponible("sin red"),
    OSError("Connection refused"),
    type("OperationalError", (Exception,), {})("(20009, b'Unable to connect')"),
    ValueError("una consulta mala también se degrada"),
])
def test_sae_caido_nunca_es_5xx(client, env, auth_as, sae, falla):
    sae.falla = falla
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _en_sae(client, h, "RUCULAKG")
    assert r.status_code == 200
    assert r.json()["disponible"] is False and "SAE no contestó" in r.json()["motivo"]


def test_la_clave_vacia_es_422(client, env, auth_as, sae):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert _en_sae(client, h, "%20%20").status_code == 422
    assert sae.llamadas == []


def test_asi_esta_en_sae_es_solo_del_dueno(client, env, auth_as, sae, sin_sae):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _en_sae(client, h, "RUCULAKG")
    assert r.status_code == 403 and sae.llamadas == []


def test_articulo_lee_una_fila_por_parametro(monkeypatch):
    vistas = []

    def falso(sql, params=(), timeout=None):
        vistas.append((sql, params))
        return []

    monkeypatch.setattr(sae_lectura, "consultar", falso)
    assert sae_lectura.articulo("04", " ajokg ") is None
    sql, params = vistas[0]
    assert "FROM INVE04 " in sql and "LTRIM(RTRIM(CVE_ART)) = %s" in sql
    assert params == ("ajokg",)
    with pytest.raises(ValueError):
        sae_lectura.articulo("4", "AJOKG")          # la empresa se valida
    monkeypatch.setattr(sae_lectura, "motor", lambda: "firebird")
    with pytest.raises(ValueError):
        sae_lectura.articulo("02", "AJOKG")         # sólo el SAE 10


# ─── (B) estado de las claves del catálogo ───────────────────────────────────

def _producto(tenant_id, nombre, clave=None, presentaciones=None, borrado=False):
    with SessionLocal() as s:
        p = Producto(tenant_id=tenant_id, sku=uuid.uuid4().hex[:8], nombre=nombre,
                     clave_sat="01010101", unidad_sat="KGM", clave_sae=clave,
                     presentaciones=presentaciones or {"KILO": 1},
                     deleted_at=datetime.now(timezone.utc) if borrado else None)
        s.add(p); s.commit()
        return p.id


def _solicitud(tenant_id, clave, estado, hace_min, tipo="ALTA", motivo=None):
    with SessionLocal() as s:
        sol = SolicitudAltaSae(tenant_id=tenant_id, clave=clave, estado=estado, tipo=tipo,
                               empresas=["02", "03"], motivo=motivo,
                               solicitada_at=datetime.now(timezone.utc) - timedelta(minutes=hace_min))
        s.add(sol); s.commit()
        return str(sol.id)


def test_estado_de_las_claves(client, env, auth_as):
    tid = env["tenant_id"]
    _producto(tid, "SANDIA", clave=" sandiakg ",
              presentaciones={"KILO": 1, "PIEZA": {"factor": 8, "clave_sae": "SANDIAPZ"}})
    _producto(tid, "SANDIA BORRADA", clave="BORRADAKG", borrado=True)
    _espejo(tid, "SANDIAKG", e02=True, e03=False)
    _espejo(tid, "SANDIAPZ", e91=True)                     # el SAE 9 no cuenta
    _solicitud(tid, "SANDIAPZ", "ERROR", 60, motivo="SAE rechazó")
    nueva = _solicitud(tid, "SANDIAPZ", "PENDIENTE", 5)
    _solicitud(tid, "SANDIAKG", "PENDIENTE", 1, tipo="CAMBIO")   # un cambio no es alta

    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.get("/api/v1/productos/claves-sae/estado", headers=h)
    assert r.status_code == 200, r.text
    por = {x["clave"]: x for x in r.json()}
    assert list(por) == ["SANDIAKG", "SANDIAPZ"]           # ordenadas; la borrada no
    assert por["SANDIAKG"]["empresas"] == {"02": {"activa": True}, "03": {"activa": False}}
    assert por["SANDIAKG"]["solicitud"] is None
    assert por["SANDIAPZ"]["empresas"] == {}
    sol = por["SANDIAPZ"]["solicitud"]
    assert sol["id"] == nueva and sol["estado"] == "PENDIENTE" and sol["tipo"] == "ALTA"
    assert sol["empresas"] == ["02", "03"] and sol["solicitada_at"]


def test_estado_en_pocas_consultas(client, env, auth_as):
    """Las mismas consultas con una clave que con ocho: nada por clave."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sentencias = []

    def contar(conn, cursor, statement, *a):
        sentencias.append(statement)

    def medir():
        sentencias.clear()
        event.listen(engine, "before_cursor_execute", contar)
        try:
            assert client.get("/api/v1/productos/claves-sae/estado", headers=h).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", contar)
        return len(sentencias)

    _producto(env["tenant_id"], "UNO", clave="UNOKG")
    _solicitud(env["tenant_id"], "UNOKG", "ERROR", 3)
    una = medir()
    for i in range(7):
        clave = f"OTRO{i}KG"
        _producto(env["tenant_id"], f"OTRO {i}", clave=clave)
        _espejo(env["tenant_id"], clave, e02=True)
        _solicitud(env["tenant_id"], clave, "PENDIENTE", 2)
    assert una >= 4, "el contador no está viendo las consultas"
    assert medir() == una


def test_estado_en_tenant_sin_sae_es_lista_vacia(client, env, auth_as, sin_sae):
    _producto(env["tenant_id"], "UNO", clave="UNOKG")
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.get("/api/v1/productos/claves-sae/estado", headers=h)
    assert r.status_code == 200 and r.json() == []
