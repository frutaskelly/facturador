"""GET /sae/catalogos: líneas y esquemas de SAE en vivo, para dar de alta o
cambiar un artículo (26-sep-2026).

SAE es falso: lo que se prueba es la forma de la respuesta, que un SAE caído
se diga (503) en vez de contestar una lista vacía como si fuera la buena, y
la caché de quince minutos — que no guarda los errores.
"""
import pytest

from app.api.v1 import sae as sae_api
from app.services import sae_lectura

from tests.test_alta_sae_api import _hdr, auth_as, conector, env  # noqa: F401


class _SAE:
    """CLIN e IMPU por empresa, contando cuántas veces se le pregunta."""

    def __init__(self, falla=None):
        self.llamadas = []
        self.falla = falla

    def __call__(self, sql, params=(), timeout=None):
        self.llamadas.append(sql)
        if self.falla:
            raise self.falla
        if "FROM CLIN" in sql:
            return [{"codigo": "FRUVE     ", "nombre": "FRUTAS Y VERDURAS"},
                    {"codigo": "ABARR", "nombre": "ABARROTES "}]
        if "FROM IMPU" in sql:
            return [{"codigo": 2, "descripcion": "IVA 0 ", "iva": 0, "ieps": 0},
                    {"codigo": 4, "descripcion": "IVA 16", "iva": 16.0, "ieps": 0},
                    {"codigo": 7, "descripcion": "IEPS 8", "iva": 16, "ieps": 8}]
        return []


@pytest.fixture
def sae(monkeypatch, env):
    sae_api._catalogos_cache.clear()
    falso = _SAE()
    # `env` hace al tenant de la prueba dueño de SAE; los ajenos: test_sae_solo_su_tenant
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    monkeypatch.setattr(sae_lectura, "consultar", falso)
    yield falso
    sae_api._catalogos_cache.clear()


def _pedir(client, h, **params):
    return client.get("/api/v1/sae/catalogos", headers=h, params=params)


def test_lineas_esquemas_y_unidades(client, conector, auth_as, sae):
    auth_as(conector); h = _hdr(conector)
    r = _pedir(client, h)                               # empresa 02 por omisión
    assert r.status_code == 200, r.text
    assert r.json() == {
        "empresa": "02",
        "lineas": [{"codigo": "FRUVE", "nombre": "FRUTAS Y VERDURAS"},
                   {"codigo": "ABARR", "nombre": "ABARROTES"}],
        "esquemas": [{"codigo": 2, "descripcion": "IVA 0", "iva": 0.0, "ieps": 0.0},
                     {"codigo": 4, "descripcion": "IVA 16", "iva": 16.0, "ieps": 0.0},
                     {"codigo": 7, "descripcion": "IEPS 8", "iva": 16.0, "ieps": 8.0}],
        "unidades": ["CAJA", "KILO", "LITRO", "PAQUETE", "PIEZA"],
    }
    clin, impu = sae.llamadas
    assert "CLIN02" in clin and "STATUS" in clin and "'B'" in clin   # sin las de baja
    assert "IMPU02" in impu and "IMPUESTO4 AS iva" in impu.replace("ISNULL(IMPUESTO4,0)", "IMPUESTO4")


def test_la_cache_es_por_empresa_y_dura_quince_minutos(client, conector, auth_as, sae, monkeypatch):
    auth_as(conector); h = _hdr(conector)
    assert _pedir(client, h, empresa="03").status_code == 200
    assert _pedir(client, h, empresa="03").status_code == 200
    assert len(sae.llamadas) == 2                        # la segunda no fue a SAE
    assert _pedir(client, h, empresa="04").status_code == 200
    assert len(sae.llamadas) == 4                        # otra empresa, otra lectura
    # pasados los quince minutos se vuelve a leer
    guardado, datos = sae_api._catalogos_cache["03"]
    sae_api._catalogos_cache["03"] = (guardado - sae_api._CATALOGOS_TTL_SEG - 1, datos)
    assert _pedir(client, h, empresa="03").status_code == 200
    assert len(sae.llamadas) == 6


@pytest.mark.parametrize("falla", [
    sae_lectura.SAENoDisponible("sin red"),
    OSError("Connection refused"),
    type("OperationalError", (Exception,), {})("(20009, b'Unable to connect')"),
])
def test_sae_caido_es_503_y_no_se_guarda(client, conector, auth_as, sae, falla):
    auth_as(conector); h = _hdr(conector)
    sae.falla = falla
    r = _pedir(client, h, empresa="05")
    assert r.status_code == 503 and "SAE no contestó" in r.json()["detail"]
    # el error no se guardó: en cuanto SAE vuelve, contesta
    sae.falla = None
    assert _pedir(client, h, empresa="05").status_code == 200


def test_sin_configuracion_es_503(client, conector, auth_as, sae, monkeypatch):
    monkeypatch.setattr(sae_lectura, "disponible", lambda: False)
    auth_as(conector); h = _hdr(conector)
    r = _pedir(client, h)
    assert r.status_code == 503 and "no tiene acceso a SAE" in r.json()["detail"]
    assert sae.llamadas == []


def test_la_empresa_se_valida(client, conector, auth_as, sae):
    auth_as(conector); h = _hdr(conector)
    for mala in ("2", "002", "0x", "02;--"):
        assert _pedir(client, h, empresa=mala).status_code == 422, mala
    assert sae.llamadas == []


def test_sin_permiso_de_espejo_no_se_lee(client, env, auth_as, sae):
    """El mismo permiso que el resto de /sae: el ADMIN preset no lo trae."""
    auth_as(env["admin"])
    assert _pedir(client, _hdr(env["admin"])).status_code == 403
    assert sae.llamadas == []
