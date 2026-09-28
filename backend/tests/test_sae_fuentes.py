"""Dos SAE a la vez: el registro de fuentes y el reloj que las recorre.

El SAE 10 tiene que seguir exactamente como estaba; el SAE 9 entra con su
propio código (01 → 91), en SU tenant, con su piso de fecha y su paso. SAE se
sustituye por lecturas falsas: no hay SAE en CI, ni debe haberlo. La prueba
contra un Firebird de verdad vive en `test_sae9_firebird.py`.
"""
import datetime as dt
import json

import pytest

from app.core.config import settings
from app.services import espejo_sae, sae_fuentes, sae_lectura

T10 = "11111111-1111-1111-1111-111111111111"
T_GERARDO = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def dos_sae(monkeypatch):
    """SAE 10 con 02/03 (+05 de catálogo) en T10; SAE 9 con 01/02 en T10 y 04 en Gerardo."""
    monkeypatch.setattr(settings, "SAE_SERVER", "1.2.3.4,1433")
    monkeypatch.setattr(settings, "SAE_USER", "u")
    monkeypatch.setattr(settings, "SAE_PASSWORD", "p")
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", T10)
    monkeypatch.setattr(settings, "ESPEJO_SAE_EMPRESAS", "02,03")
    monkeypatch.setattr(settings, "ESPEJO_SAE_CLAVES_EMPRESAS", "02,03,05")
    monkeypatch.setattr(settings, "ESPEJO_SAE_CLAVES_CADA_SEG", 14400)
    monkeypatch.setattr(settings, "SAE_FB_HOST", "100.95.166.85")
    monkeypatch.setattr(settings, "SAE_FB_USER", "SYSDBA")
    monkeypatch.setattr(settings, "SAE_FB_PASSWORD", "x")
    monkeypatch.setattr(settings, "SAE_FB_DESDE", "2026-01-01")
    monkeypatch.setattr(settings, "SAE_FB_EMPRESAS", json.dumps([
        {"numero": "01", "tenant": T10},
        {"numero": "02", "tenant": T10, "series": ["KELLYSLP"]},
        {"numero": "04", "tenant": T_GERARDO},
    ]))
    monkeypatch.setattr(settings, "SAE_FB_CADA_SEG", 300)


# ── El registro ──────────────────────────────────────────────────────────────

def test_el_sae10_se_arma_igual_que_ayer(dos_sae):
    emps = sae_fuentes.empresas_sae10()
    assert [(e.codigo, e.numero, e.facturas, e.claves) for e in emps] == [
        ("02", "02", True, True), ("03", "03", True, True), ("05", "05", False, True)]
    assert emps[0].series == sae_fuentes.SERIES_SAE10["02"]
    assert emps[0].placeholders == frozenset({"1", "2", "3"})
    assert all(e.desde is None and e.servidor.motor == "mssql" for e in emps)


def test_el_sae9_lleva_codigo_propio_tenant_y_piso(dos_sae):
    emps = sae_fuentes.empresas_sae9()
    assert [(e.numero, e.codigo, e.tenant_id) for e in emps] == [
        ("01", "91", T10), ("02", "92", T10), ("04", "94", T_GERARDO)]
    assert emps[1].series == ("KELLYSLP",) and emps[0].series == ()
    assert all(e.desde == dt.date(2026, 1, 1) and e.servidor.es_firebird for e in emps)
    assert emps[0].servidor.ruta_de("01").endswith(r"Empresa01\Datos\SAE90EMPRE01.FDB")
    # ni el usuario ni la contraseña salen en un repr (logs, errores)
    r = repr(emps[0].servidor)
    assert "password=" not in r and "usuario=" not in r and "SYSDBA" not in r


def test_la_02_del_sae9_no_es_la_02_del_sae10(dos_sae):
    """Mismo tenant, mismo número de Aspel, empresas distintas: cada una con su
    código, y ninguna se confunde al buscarla."""
    e10 = sae_fuentes.empresa_de(T10, "02")
    e9 = sae_fuentes.empresa_de(T10, "92")
    assert e10.servidor.motor == "mssql" and e9.servidor.es_firebird
    assert e10.numero == e9.numero == "02"
    assert sae_fuentes.empresa_de(T_GERARDO, "91") is None       # la de otro tenant no existe
    assert sae_fuentes.empresa_de(T_GERARDO, "94").numero == "04"


def test_un_codigo_que_choca_con_el_sae10_se_descarta(dos_sae, monkeypatch):
    monkeypatch.setattr(settings, "SAE_FB_EMPRESAS", json.dumps([
        {"numero": "01", "codigo": "02", "tenant": T10},        # choca con la 02 del SAE 10
        {"numero": "01", "codigo": "02", "tenant": T_GERARDO},  # en otro tenant no choca
    ]))
    codigos = [(e.tenant_id, e.codigo, e.servidor.clave) for e in sae_fuentes.empresas()]
    # La primera choca con el SAE 10 y se descarta; la segunda repite un código
    # del SAE 9 (los códigos del SAE 9 son únicos aunque cambie el tenant).
    assert [c for c in codigos if c[2] == "SAE9"] == []
    assert codigos.count((T10, "02", "SAE10")) == 1


def test_una_configuracion_mala_no_tumba_al_sae10(dos_sae, monkeypatch):
    monkeypatch.setattr(settings, "SAE_FB_EMPRESAS", "{esto no es json")
    assert sae_fuentes.empresas_sae9() == []
    assert [e.codigo for e in sae_fuentes.empresas()] == ["02", "03", "05"]
    monkeypatch.setattr(settings, "SAE_FB_EMPRESAS", json.dumps([
        {"numero": "1", "tenant": T10}, {"numero": "03"}, "basura", {"numero": "03", "tenant": T10}]))
    assert [e.codigo for e in sae_fuentes.empresas_sae9()] == ["93"]


def test_la_serie_se_guarda_sin_espacios():
    assert sae_fuentes.serie_facturador("FOR K") == "FORK"
    assert sae_fuentes.serie_facturador(" qro  ak ") == "QROAK"
    assert sae_fuentes.serie_facturador("ZHGO") == "ZHGO"        # el SAE 10 no cambia


# ── La lectura dentro de una empresa ─────────────────────────────────────────

def test_en_empresa_cambia_codigo_por_numero_solo_para_el_sae9(dos_sae):
    e9 = sae_fuentes.empresa_de(T10, "91")
    e10 = sae_fuentes.empresa_de(T10, "02")
    with sae_lectura.en_empresa(e10):                            # el SAE 10: sin cambios
        assert sae_lectura.motor() == "mssql" and sae_lectura.tabla("FACTF", "02") == "FACTF02"
    with sae_lectura.en_empresa(e9):
        assert sae_lectura.motor() == "firebird"
        assert sae_lectura.tabla("FACTF", "91") == "FACTF01"
        with pytest.raises(ValueError):
            sae_lectura.tabla("FACTF", "92")                    # otra empresa, otro archivo
        with pytest.raises(ValueError):
            sae_lectura.consultar("DELETE FROM FACTF01")         # sólo SELECT, también aquí
    assert sae_lectura.motor() == "mssql"


def test_el_documento_pegado_se_parte_solo_contra_series_conocidas():
    p = sae_lectura.partir_con_series
    assert p("FOR K     10", None) == ("FORK", 10)               # con espacio, como siempre
    assert p("KELLYSLP0000000031", None) is None                # sin series: no se adivina
    assert p("KELLYSLP0000000031", ["KELLYSLP", "KELLYQRO"]) == ("KELLYSLP", 31)
    assert p("ZCH512", ["ZCH", "ZCH5"]) is None                 # dos cortes posibles: no decide
    assert p("ZCH5C12", ["ZCH5C"]) == ("ZCH5C", 12)


def test_el_formato_del_sae9_imita_al_del_10():
    assert sae_lectura.dinero(1160.005) == "1160.01"
    assert sae_lectura.dinero(None) == "0.00"
    assert sae_lectura.dinero(10, 4) == "10.0000"
    assert sae_lectura.fecha_hora(dt.datetime(2026, 3, 18, 9, 30, 5, 123)) == "2026-03-18 09:30:05"
    assert sae_lectura.fecha_hora("2026-09-10T08:00:00.123") == "2026-09-10T08:00:00"
    assert sae_lectura.fecha_hora(None) == ""
    assert sae_lectura._valor_firebird(b"A\xd1O", "ISO8859_1") == "AÑO"


# ── El reloj ─────────────────────────────────────────────────────────────────

@pytest.fixture
def reloj_falso(dos_sae, monkeypatch):
    """El reloj con SAE y la base sustituidos: registra quién se leyó, con qué
    motor, en qué tenant y con qué piso."""
    from contextlib import contextmanager
    from app.core import rbac
    from app.services import cobranza_sae

    visto = {"sync": [], "cobranza": [], "claves": [], "reportes": [], "reclamos": []}

    @contextmanager
    def _sesion(t):
        yield object()

    monkeypatch.setattr(rbac, "tenant_session", _sesion)
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)

    def _sync(db, ctx, empresa, series, dias_cancelaciones=3, limite=500, desde=None):
        visto["sync"].append((str(ctx.tenant_id), empresa, sae_lectura.motor(), tuple(series), desde))
        return {"nuevas": 1, "actualizadas": 0, "errores": []}

    def _cuadre(db, ctx, empresa, series, reparar=True, tope=25, desde=None, parcial=False):
        visto.setdefault("cuadre", []).append((str(ctx.tenant_id), empresa, parcial))
        return {"faltantes": 0, "reparadas": 0, "omitidas": 0, "errores": []}

    def _cob(db, ctx, empresa, dias=3, desde_minimo=None, series=None, completa=False):
        visto["cobranza"].append((empresa, desde_minimo, completa))
        return {"pagos": {"enviados": 0}, "notas_credito": {"enviados": 0}, "errores": []}

    monkeypatch.setattr(espejo_sae, "sincronizar", _sync)
    monkeypatch.setattr(espejo_sae, "cuadre", _cuadre)
    monkeypatch.setattr(cobranza_sae, "sincronizar", _cob)
    monkeypatch.setattr(espejo_sae, "descubrir_series",
                        lambda emp, desde: ["FOR K"] if sae_lectura.motor() == "firebird" else [])
    monkeypatch.setattr(espejo_sae, "sincronizar_claves",
                        lambda t, emps, errores: visto["claves"].append((t, tuple(emps))) or {})
    monkeypatch.setattr(espejo_sae, "_reclamar_solicitud",
                        lambda t: visto["reclamos"].append(t) or None)
    monkeypatch.setattr(espejo_sae, "_reportar",
                        lambda t, s, tot: visto["reportes"].append((t, tot)))
    monkeypatch.setattr(espejo_sae, "_ultimo_cuadre", None)
    monkeypatch.setattr(espejo_sae, "_ultima_cobranza", 0.0)
    monkeypatch.setattr(espejo_sae, "_ultimas_claves", None)
    monkeypatch.setattr(espejo_sae, "_ultima_fb", None)
    monkeypatch.setattr(espejo_sae, "_ultimo_cuadre_fb", {})
    monkeypatch.setattr(espejo_sae, "_errores_fb", {})
    monkeypatch.setattr(espejo_sae, "_cobranza_completa", {})
    monkeypatch.setattr(espejo_sae, "_ultimas_claves_fb", {})
    espejo_sae._series_cache.clear()
    return visto


def test_el_reloj_recorre_los_dos_sae_cada_empresa_en_su_tenant(reloj_falso):
    r = espejo_sae.pasada_programada()
    assert r["corrio"] and r["nuevas"] == 5
    sync = reloj_falso["sync"]
    # El SAE 10: sus series de siempre, sin piso, por el camino de SQL Server.
    assert (T10, "02", "mssql", sae_fuentes.SERIES_SAE10["02"], None) in sync
    # El SAE 9: descubiertas o dadas, desde el 1-ene, por Firebird y en SU tenant.
    assert (T10, "91", "firebird", ("FOR K",), dt.date(2026, 1, 1)) in sync
    assert (T10, "92", "firebird", ("KELLYSLP",), dt.date(2026, 1, 1)) in sync
    assert (T_GERARDO, "94", "firebird", ("FOR K",), dt.date(2026, 1, 1)) in sync
    # Cada tenant reclama su botón y reporta su pasada.
    assert reloj_falso["reclamos"] == [T10, T_GERARDO]
    assert [t for t, _ in reloj_falso["reportes"]] == [T10, T_GERARDO]
    # El catálogo: el de cada tenant con sus empresas (la 05 del 10 incluida).
    assert reloj_falso["claves"] == [(T10, ("02", "03", "05")), (T10, ("91", "92")),
                                     (T_GERARDO, ("94",))]
    # La cobranza del SAE 9 arranca en su piso, completa la primera vez del día.
    assert ("94", dt.date(2026, 1, 1), True) in reloj_falso["cobranza"]
    # El cuadre del SAE 9 repara de a poco; el del 10 sigue todo o nada.
    assert (T10, "91", True) in reloj_falso["cuadre"] and (T10, "02", False) in reloj_falso["cuadre"]


def test_el_sae9_lleva_su_propio_paso_y_en_cero_no_corre(reloj_falso, monkeypatch):
    espejo_sae.pasada_programada()
    reloj_falso["sync"].clear()
    espejo_sae.pasada_programada()          # a los 30 s: el SAE 10 sí, el 9 no
    assert {m for _, _, m, _, _ in reloj_falso["sync"]} == {"mssql"}
    monkeypatch.setattr(settings, "SAE_FB_CADA_SEG", 0)
    monkeypatch.setattr(espejo_sae, "_ultima_fb", None)
    reloj_falso["sync"].clear()
    espejo_sae.pasada_programada()
    assert {m for _, _, m, _, _ in reloj_falso["sync"]} == {"mssql"}
    assert T_GERARDO not in reloj_falso["reclamos"][-1:]


def test_sin_el_sae9_configurado_el_reloj_es_el_de_ayer(reloj_falso, monkeypatch):
    monkeypatch.setattr(settings, "SAE_FB_PASSWORD", "")
    espejo_sae.pasada_programada()
    assert {m for _, _, m, _, _ in reloj_falso["sync"]} == {"mssql"}
    assert reloj_falso["reclamos"] == [T10]


# ── Las rutas ────────────────────────────────────────────────────────────────

def test_las_rutas_nuevas_estan_registradas_y_pedidas_con_permiso(client):
    rutas = {r.path for r in client.app.routes}
    assert "/api/v1/sae/fuentes" in rutas
    assert "/api/v1/sae/fuentes/{codigo}/clientes" in rutas
    assert client.get("/api/v1/sae/fuentes").status_code in (401, 403)
    assert client.post("/api/v1/sae/fuentes/91/clientes").status_code in (401, 403)


def test_el_boton_de_gerardo_no_mueve_los_relojes_del_sae10(reloj_falso, monkeypatch):
    """Revisión del 26-sep: con relojes globales, el botón de un tenant de puro
    SAE 9 reiniciaba el paso de 4 h del catálogo del SAE 10."""
    import time as _t

    espejo_sae.pasada_programada()                      # primera vuelta: todo toca
    marca10 = espejo_sae._ultimas_claves[T10]
    monkeypatch.setattr(espejo_sae, "_reclamar_solicitud",
                        lambda t: "sol-g" if t == T_GERARDO else None)
    reloj_falso["claves"].clear()
    espejo_sae.pasada_programada()
    assert reloj_falso["claves"] == [(T_GERARDO, ("94",))]     # sólo el suyo
    assert espejo_sae._ultimas_claves[T10] == marca10           # el del 10, intacto
    assert espejo_sae._ultimas_claves_fb[T_GERARDO] >= marca10


def test_el_cuadre_del_sae9_tiene_su_propio_dia(reloj_falso, monkeypatch):
    """El SAE 10 cuadra en la primera vuelta del día; si el SAE 9 no corría en
    esa vuelta, ya no cuadraba ese día. Ahora cada uno lleva su fecha."""
    monkeypatch.setattr(espejo_sae, "_ultima_fb",
                        {T10: _ahora_menos(10), T_GERARDO: _ahora_menos(10)})
    espejo_sae.pasada_programada()                      # el 10 cuadra, el 9 no corre
    assert {e for _, e, _ in reloj_falso["cuadre"]} == {"02", "03"}
    monkeypatch.setattr(espejo_sae, "_ultima_fb", {})   # ya le toca al 9
    reloj_falso["cuadre"].clear()
    espejo_sae.pasada_programada()
    assert {e for _, e, _ in reloj_falso["cuadre"]} == {"91", "92", "94"}


def _ahora_menos(seg):
    import time as _t
    return _t.monotonic() - seg


def test_una_empresa_del_sae10_sin_series_se_salta_como_siempre(reloj_falso, monkeypatch):
    monkeypatch.setattr(settings, "ESPEJO_SAE_EMPRESAS", "02,06")
    monkeypatch.setattr(settings, "SAE_FB_CADA_SEG", 0)
    espejo_sae.pasada_programada()
    assert [e for _, e, _, _, _ in reloj_falso["sync"]] == ["02"]   # la 06 ni se consulta


def test_un_sae9_caido_no_se_reintenta_con_sus_otras_empresas(reloj_falso, monkeypatch):
    """Revisión del 26-sep: un SAE 9 caído costaba dos timeouts por empresa y
    el SAE 10 esperaba detrás. Ahora la primera que no contesta corta al resto
    de ese servidor en esa vuelta, y el error se queda a la vista."""
    intentos = []

    def _cae(db, ctx, empresa, series, dias_cancelaciones=3, limite=500, desde=None):
        if sae_lectura.motor() == "firebird":
            intentos.append(empresa)
            raise sae_lectura.SAENoDisponible("SAE9 no contesta")
        reloj_falso["sync"].append((str(ctx.tenant_id), empresa, "mssql", tuple(series), desde))
        return {"nuevas": 1, "actualizadas": 0, "errores": []}

    monkeypatch.setattr(espejo_sae, "sincronizar", _cae)
    monkeypatch.setattr(espejo_sae, "descubrir_series", lambda emp, desde: ["FOR K"])
    r = espejo_sae.pasada_programada()
    assert intentos == ["91"]                           # la 92 y la 94 ya no esperan
    assert r["nuevas"] == 2                             # el SAE 10 sí corrió
    reporte_g = dict(reloj_falso["reportes"])[T_GERARDO]
    assert any("no contestó" in e for e in reporte_g["errores"])
    # a los 30 s el SAE 9 no corre, pero su error sigue en el reporte de T10
    reloj_falso["reportes"].clear()
    espejo_sae.pasada_programada()
    reporte_10 = dict(reloj_falso["reportes"])[T10]
    assert any("SAE9" in e for e in reporte_10["errores"])
    assert T_GERARDO not in dict(reloj_falso["reportes"])   # no leyó nada: no reporta


def test_una_series_mala_o_sin_piso_no_entra(dos_sae, monkeypatch):
    monkeypatch.setattr(settings, "SAE_FB_EMPRESAS", json.dumps([
        {"numero": "01", "tenant": T10, "series": 7},
        {"numero": "02", "tenant": T10, "series": "KELLYSLP", "claves": "false"},
    ]))
    emps = sae_fuentes.empresas_sae9()
    assert [(e.codigo, e.series, e.claves) for e in emps] == [("92", ("KELLYSLP",), False)]
    monkeypatch.setattr(settings, "SAE_FB_DESDE", "")
    assert sae_fuentes.empresas_sae9() == []            # sin piso, no entra
    assert [e.codigo for e in sae_fuentes.empresas()] == ["02", "03", "05"]
