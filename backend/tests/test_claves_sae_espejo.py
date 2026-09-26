"""El catálogo de artículos de SAE dentro del Facturador (26-sep-2026).

Dos cosas: que el Facturador lea INVE él mismo y lo deposite con LA MISMA
lógica que la ruta por la que lo mandaba el bot —sin vaciar nunca el catálogo
por una lectura fallida ni forzar el candado de «encoge a la mitad»—, y la
búsqueda de claves con la que el bot decide entre un alta y un cambio
(`GET /productos/claves-sae`).

SAE se sustituye por una lectura falsa: no hay SAE en CI, ni debe haberlo.
"""
import uuid

import pytest

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import ClaveSae, Producto, Tenant
from app.services import espejo_sae, sae_lectura

from tests.test_alta_sae_api import _hdr, auth_as, env  # noqa: F401


def _catalogo(env, empresa):
    with SessionLocal() as s:
        return {c.clave: (c.descripcion, c.activa) for c in s.query(ClaveSae).filter(
            ClaveSae.tenant_id == env["tenant_id"], ClaveSae.empresa == empresa).all()}


def _sembrar(env, filas):
    with SessionLocal() as s:
        for emp, clave, desc, activa in filas:
            s.add(ClaveSae(tenant_id=env["tenant_id"], empresa=emp, clave=clave,
                           descripcion=desc, activa=activa))
        s.commit()


# ── La lectura de INVE ───────────────────────────────────────────────────────

def test_la_lectura_de_inve_deja_las_descripciones_como_las_dejaba_el_bot(monkeypatch):
    """Mismo SELECT que `sync_claves_sae.py`. Los saltos de línea y tabuladores
    de DESCR se cambian por espacios —como el REPLACE del bot— para que la
    primera pasada del Facturador no «actualice» cientos de filas que no
    cambiaron. Y el timeout es amplio: INVE entero no cabe en 25 s."""
    visto = {}

    def _fake(sql, params=(), timeout=None):
        visto.update(sql=sql, timeout=timeout)
        return [{"clave": "AJOKG", "descripcion": "AJO\r\nKILO\t", "status": "A"},
                {"clave": "VIEJAKG", "descripcion": "VIEJA", "status": "B"},
                {"clave": "SINSTATUS", "descripcion": None, "status": ""},
                {"clave": "  ", "descripcion": "basura", "status": "A"}]

    monkeypatch.setattr(sae_lectura, "consultar", _fake)
    filas = sae_lectura.catalogo_inve("05")
    assert "FROM INVE05" in visto["sql"] and visto["timeout"] >= 120
    assert filas == [
        {"clave": "AJOKG", "descripcion": "AJO  KILO", "activa": True},
        {"clave": "VIEJAKG", "descripcion": "VIEJA", "activa": False},
        {"clave": "SINSTATUS", "descripcion": None, "activa": True},
    ]
    with pytest.raises(ValueError):
        sae_lectura.catalogo_inve("5")


# ── El depósito, desde el reloj ──────────────────────────────────────────────

def test_el_reloj_deposita_cada_empresa_con_la_misma_logica_que_la_ruta(env, monkeypatch):
    inve = {
        "02": [{"clave": "ajokg ", "descripcion": "AJO", "activa": True},
               {"clave": "PAPAKG", "descripcion": "PAPA", "activa": False}],
        "05": [{"clave": "AJOKG", "descripcion": "AJO CINCO", "activa": True}],
    }
    monkeypatch.setattr(sae_lectura, "catalogo_inve", lambda e: inve[e])
    errores = []
    r = espejo_sae.sincronizar_claves(env["tenant_id"], ["02", "05"], errores)
    assert errores == []
    assert r["02"] == {"recibidas": 2, "creadas": 2, "actualizadas": 0, "eliminadas": 0}
    assert _catalogo(env, "02") == {"AJOKG": ("AJO", True), "PAPAKG": ("PAPA", False)}
    assert _catalogo(env, "05") == {"AJOKG": ("AJO CINCO", True)}

    # segunda pasada: reemplaza (es espejo, no acumulado)
    inve["02"] = [{"clave": "AJOKG", "descripcion": "AJO BLANCO", "activa": True},
                  {"clave": "PAPAKG", "descripcion": "PAPA", "activa": True},
                  {"clave": "CEBOLLAKG", "descripcion": "CEBOLLA", "activa": True}]
    r = espejo_sae.sincronizar_claves(env["tenant_id"], ["02"], errores)
    assert r["02"] == {"recibidas": 3, "creadas": 1, "actualizadas": 2, "eliminadas": 0}
    assert _catalogo(env, "02")["AJOKG"] == ("AJO BLANCO", True)


def test_sae_caido_no_vacia_el_catalogo_y_las_demas_empresas_siguen(env, monkeypatch):
    """Una lectura fallida nunca reemplaza nada: el catálogo de la 02 se queda
    como estaba, la 03 sí se actualiza, y el error queda en la pasada."""
    _sembrar(env, [("02", "AJOKG", "AJO", True), ("02", "PAPAKG", "PAPA", True)])

    def _lee(e):
        if e == "02":
            raise sae_lectura.SAENoDisponible("sin red")
        return [{"clave": "TABKG", "descripcion": "TAB", "activa": True}]

    monkeypatch.setattr(sae_lectura, "catalogo_inve", _lee)
    errores = []
    r = espejo_sae.sincronizar_claves(env["tenant_id"], ["02", "03"], errores)
    assert "SAENoDisponible" in r["02"]["error"] and r["03"]["creadas"] == 1
    assert len(errores) == 1 and "[claves 02]" in errores[0]
    assert _catalogo(env, "02") == {"AJOKG": ("AJO", True), "PAPAKG": ("PAPA", True)}

    # un INVE vacío tampoco: es una lectura mala, no un inventario vacío
    monkeypatch.setattr(sae_lectura, "catalogo_inve", lambda e: [])
    errores = []
    espejo_sae.sincronizar_claves(env["tenant_id"], ["02"], errores)
    assert "CatalogoVacio" in errores[0]
    assert len(_catalogo(env, "02")) == 2


def test_el_reloj_no_fuerza_el_candado_de_la_mitad(env, monkeypatch):
    """Un catálogo que se parte a la mitad es casi siempre una lectura cortada.
    Forzarlo lo decide una persona, nunca la pasada automática."""
    _sembrar(env, [("02", f"C{i}", f"CLAVE {i}", True) for i in range(6)])
    monkeypatch.setattr(sae_lectura, "catalogo_inve",
                        lambda e: [{"clave": "C0", "descripcion": "CLAVE 0", "activa": True}])
    errores = []
    r = espejo_sae.sincronizar_claves(env["tenant_id"], ["02"], errores)
    assert "CatalogoEncogido" in r["02"]["error"] and "lectura incompleta" in errores[0]
    assert len(_catalogo(env, "02")) == 6


def test_la_pasada_lee_el_catalogo_con_su_paso_y_con_el_boton(env, monkeypatch):
    """Cada 4 h (ESPEJO_SAE_CLAVES_CADA_SEG), en su propia lista de empresas
    —que trae la 05 aunque no tenga facturas—, y siempre que alguien presiona
    «Sincronizar SAE». En 0 no se lee desde aquí."""
    leidas, reportes = [], []
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    monkeypatch.setattr(sae_lectura, "catalogo_inve",
                        lambda e: leidas.append(e) or [{"clave": f"K{e}", "descripcion": e,
                                                        "activa": True}])
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", str(env["tenant_id"]))
    monkeypatch.setattr(settings, "ESPEJO_SAE_EMPRESAS", "")          # sin facturas
    monkeypatch.setattr(settings, "ESPEJO_SAE_CLAVES_EMPRESAS", "02,05")
    monkeypatch.setattr(settings, "ESPEJO_SAE_CLAVES_CADA_SEG", 14400)
    monkeypatch.setattr(espejo_sae, "_ultimas_claves", None)   # proceso recién arrancado
    monkeypatch.setattr(espejo_sae, "_ultimo_cuadre", None)
    monkeypatch.setattr(espejo_sae, "_ultima_cobranza", 0.0)
    solicitud = {"id": None}
    monkeypatch.setattr(espejo_sae, "_reclamar_solicitud", lambda t: solicitud["id"])
    monkeypatch.setattr(espejo_sae, "_reportar", lambda t, s, total: reportes.append(total))

    r = espejo_sae.pasada_programada()
    assert leidas == ["02", "05"] and r["claves"]["05"]["creadas"] == 1
    assert _catalogo(env, "05") == {"K05": ("05", True)}
    assert reportes[-1]["claves"] == r["claves"]           # va en el reporte de la pasada

    # la siguiente vuelta, a los 30 s, no vuelve a leer INVE
    assert espejo_sae.pasada_programada()["claves"] is None
    assert leidas == ["02", "05"]

    # el botón sí
    solicitud["id"] = uuid.uuid4()
    assert espejo_sae.pasada_programada()["claves"] is not None
    assert leidas == ["02", "05", "02", "05"]

    # en 0 no se lee, ni con el botón
    monkeypatch.setattr(settings, "ESPEJO_SAE_CLAVES_CADA_SEG", 0)
    assert espejo_sae.pasada_programada()["corrio"] is False     # sin nada que hacer
    assert leidas == ["02", "05", "02", "05"]


# ── La búsqueda del bot: GET /productos/claves-sae ───────────────────────────

@pytest.fixture
def catalogo(env):
    """AJOKG en tres empresas (viva en la 02 y la 05, de baja en la 03),
    AJOKGMORADO y AAJOKG en la 02, PAPAKG en la 04; y productos que las llevan."""
    _sembrar(env, [
        ("02", "AAJOKG", "AJO DOBLE A", True),
        ("02", "AJOKG", "AJO KILO", True),
        ("03", "AJOKG", "AJO VIEJO", False),
        ("05", "AJOKG", "AJO KILO CINCO", True),
        ("02", "AJOKGMORADO", "AJO MORADO", True),
        ("04", "PAPAKG", "PAPA BLANCA", True),
    ])
    with SessionLocal() as s:
        prod = s.query(Producto).filter(Producto.id == env["prod"]).one()
        prod.clave_sae = " ajokg "          # el cruce normaliza los dos lados
        s.add(Producto(tenant_id=env["tenant_id"], sku="A-OTRO", nombre="Ajo inactivo",
                       clave_sat="01010101", unidad_sat="KGM", clave_sae="AJOKG",
                       activo=False))
        borrado = Producto(tenant_id=env["tenant_id"], sku="P-B", nombre="Papa borrada",
                           clave_sat="01010101", unidad_sat="KGM", clave_sae="PAPAKG")
        s.add(borrado); s.flush()
        from datetime import datetime, timezone
        borrado.deleted_at = datetime.now(timezone.utc)
        s.commit()
    return env


def _buscar(client, h, **params):
    return client.get("/api/v1/productos/claves-sae", headers=h, params=params)


def test_la_clave_exacta_trae_todas_sus_empresas_y_su_producto(client, catalogo, auth_as):
    env = catalogo
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _buscar(client, h, clave=" ajo kg")
    assert r.status_code == 200, r.text
    [uno] = r.json()
    assert uno["clave"] == "AJOKG"
    assert uno["empresas"] == {
        "02": {"activa": True, "descripcion": "AJO KILO"},
        "03": {"activa": False, "descripcion": "AJO VIEJO"},
        "05": {"activa": True, "descripcion": "AJO KILO CINCO"},
    }
    assert uno["descripcion"] == "AJO KILO"          # la de la primera activa
    # dos productos la llevan: gana el activo
    assert uno["producto_id"] == env["prod"] and uno["producto_nombre"] == "Ajo kilo"


def test_el_texto_busca_en_clave_y_descripcion_con_la_exacta_primero(client, catalogo, auth_as):
    env = catalogo
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert [c["clave"] for c in _buscar(client, h, q="ajo").json()] == [
        "AAJOKG", "AJOKG", "AJOKGMORADO"]
    assert [c["clave"] for c in _buscar(client, h, q="MORADO").json()] == ["AJOKGMORADO"]
    # el texto elige la clave, pero el mapa trae TODAS sus empresas
    [viejo] = _buscar(client, h, q="viejo").json()
    assert viejo["clave"] == "AJOKG" and sorted(viejo["empresas"]) == ["02", "03", "05"]
    # la exacta primero aunque el orden alfabético diga otra cosa
    assert [c["clave"] for c in _buscar(client, h, q="ajokg").json()] == [
        "AJOKG", "AAJOKG", "AJOKGMORADO"]
    # el tope cuenta claves, no filas: AJOKG está en tres empresas y es UNA
    assert [c["clave"] for c in _buscar(client, h, q="ajokg", limit=1).json()] == ["AJOKG"]
    assert len(_buscar(client, h, q="ajokg", limit=2).json()) == 2
    # un producto borrado no ampara nada
    [papa] = _buscar(client, h, clave="PAPAKG").json()
    assert papa["producto_id"] is None and papa["empresas"] == {
        "04": {"activa": True, "descripcion": "PAPA BLANCA"}}


def test_filtros_de_empresa_y_de_activas(client, catalogo, auth_as):
    env = catalogo
    auth_as(env["admin"]); h = _hdr(env["admin"])
    [solo] = _buscar(client, h, clave="AJOKG", solo_activas=True).json()
    assert sorted(solo["empresas"]) == ["02", "05"]
    [tres] = _buscar(client, h, clave="AJOKG", empresa="03").json()
    assert tres["empresas"] == {"03": {"activa": False, "descripcion": "AJO VIEJO"}}
    assert tres["descripcion"] == "AJO VIEJO"        # sin activa, cualquiera
    assert _buscar(client, h, clave="AJOKG", empresa="04").json() == []
    assert _buscar(client, h, clave="NOEXISTE").json() == []


def test_sin_clave_ni_texto_no_se_busca(client, catalogo, auth_as):
    env = catalogo
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert _buscar(client, h).status_code == 422
    assert _buscar(client, h, clave="   ").status_code == 422
    assert _buscar(client, h, clave="AJOKG", empresa="2").status_code == 422
    assert _buscar(client, h, q="ajo", limit=0).status_code == 422
    assert _buscar(client, h, q="ajo", limit=101).status_code == 422


def test_la_busqueda_no_ve_el_catalogo_de_otro_tenant(client, catalogo, auth_as):
    """Las pruebas corren sin RLS: el filtro por tenant tiene que ser explícito."""
    env = catalogo
    with SessionLocal() as s:
        suf = uuid.uuid4().hex[:8]
        otro = Tenant(slug=f"otro-{suf}", legal_name="Otro SA", rfc=f"OT{suf.upper()}"[:13],
                      regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
                      tier="PRINCIPAL", status="ACTIVE")
        s.add(otro); s.flush()
        otro_id = otro.id
        s.add(ClaveSae(tenant_id=otro_id, empresa="04", clave="AJOKG",
                       descripcion="AJO AJENO", activa=True))
        s.commit()
    try:
        auth_as(env["admin"]); h = _hdr(env["admin"])
        [uno] = _buscar(client, h, clave="AJOKG").json()
        assert "04" not in uno["empresas"]
    finally:
        with SessionLocal() as s:
            s.query(ClaveSae).filter(ClaveSae.tenant_id == otro_id).delete()
            s.query(Tenant).filter(Tenant.id == otro_id).delete()
            s.commit()


def test_la_ruta_de_claves_va_antes_que_la_del_producto(client):
    """«claves-sae» parsearía como UUID (422) si `/{producto_id}` la atrapara
    primero: FastAPI casa en orden de declaración."""
    from app.main import app
    rutas = [r.path for r in app.routes if "GET" in getattr(r, "methods", set())]
    assert rutas.index("/api/v1/productos/claves-sae") < rutas.index("/api/v1/productos/{producto_id}")
