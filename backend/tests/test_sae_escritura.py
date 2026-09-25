"""El Facturador como ÚNICO escritor de SAE (altas y cambios de producto).

SAE se sustituye por uno falso en memoria: lo que se prueba es la disciplina
del escritor, no pymssql. Que escriba en cada empresa por separado, que una
clave existente no se vuelva a insertar, que un fallo no se reintente, que sin
SAE no se toque nada, y sobre todo que con el escritor encendido el bot ya no
pueda reclamar: dos escritores no se coordinan con un comentario.
"""
import re

import pytest

from app.core.config import settings
from app.services import sae_escritura

from tests.test_alta_sae_api import _hdr, _pedir, auth_as, conector, env  # noqa: F401


class _SAEFalso:
    """INVE por empresa como conjuntos de claves, con transacción de a de veras:
    lo que no se confirma no queda."""

    def __init__(self, claves=None, fallan=(), contesta=True):
        self.inve = {e: set(v) for e, v in (claves or {}).items()}
        self.fallan = set(fallan)
        self.contesta = contesta
        self.sql = []          # (sql, params) de todo lo que se mandó
        self.lineas = {"FRUVE", "ABARR"}

    def conectar(self):
        if not self.contesta:
            raise OSError("sin red")
        return _Con(self)

    def escrituras(self, verbo):
        return [(q, p) for q, p in self.sql if q.lstrip().upper().startswith(verbo)]


class _Con:
    def __init__(self, sae):
        self.sae, self.pend = sae, []

    def cursor(self):
        return _Cur(self)

    def commit(self):
        for emp, clave in self.pend:
            self.sae.inve.setdefault(emp, set()).add(clave)
        self.pend = []

    def rollback(self):
        self.pend = []

    def close(self):
        pass


class _Cur:
    def __init__(self, con):
        self.con, self.sae, self.rows, self.rowcount = con, con.sae, [], -1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.sae.sql.append((sql, tuple(params)))
        emp = (re.search(r"(?:INVE|CLIN|IMPU|PRECIO_X_PROD)(\d\d)", sql) or [None, None])[1]
        up = sql.lstrip().upper()
        self.rows, self.rowcount = [], -1
        if up == "SELECT 1":
            self.rows = [(1,)]
        elif up.startswith("SELECT 1 FROM INVE"):
            self.rows = [(1,)] if params[0] in self.sae.inve.get(emp, set()) else []
        elif up.startswith("SELECT 1 FROM CLIN"):
            self.rows = [(1,)] if params[0] in self.sae.lineas else []
        elif up.startswith("SELECT 1 FROM IMPU"):
            self.rows = [(1,)]
        elif up.startswith("INSERT INTO INVE"):
            if emp in self.sae.fallan:
                raise RuntimeError("Msg 2627 violation")
            self.con.pend.append((emp, params[0]))
        elif up.startswith("UPDATE INVE"):
            self.rowcount = 1 if params[-1] in self.sae.inve.get(emp, set()) else 0

    def fetchall(self):
        return self.rows


@pytest.fixture
def escritor(monkeypatch, env):
    """Enciende la escritura del Facturador contra un SAE falso."""
    monkeypatch.setattr(settings, "SAE_SERVER", "sae.test,1433")
    monkeypatch.setattr(settings, "SAE_ESCRITURA_USER", "escritor")
    monkeypatch.setattr(settings, "SAE_ESCRITURA_PASSWORD", "x")
    monkeypatch.setattr(settings, "SAE_ESCRITURA_INTERVALO_SEG", 30)
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", str(env["tenant_id"]))

    def _con(sae):
        monkeypatch.setattr(sae_escritura, "_conectar", sae.conectar)
        return sae
    return _con


def _cambio(client, h, **body):
    return client.post("/api/v1/productos/cambio-sae", headers=h,
                       json={"clave": "AJOKG", **body})


def _get(client, h, sol_id):
    r = client.get("/api/v1/productos/alta-sae", headers=h, params={"tipo": "TODAS", "limit": 200})
    return next(s for s in r.json()["items"] if s["id"] == sol_id)


def test_el_facturador_escribe_el_alta_en_cada_empresa(client, env, auth_as, escritor):
    sae = escritor(_SAEFalso())
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h, producto_id=env["prod"], empresas=["02", "03"]).json()

    r = sae_escritura.pasada()
    assert [x["estado"] for x in r["hechas"]] == ["OK"]
    assert sae.inve == {"02": {"AJOKG"}, "03": {"AJOKG"}}
    # todo dato viaja como parámetro: la descripción no aparece dentro del SQL
    ins = sae.escrituras("INSERT INTO INVE")
    assert len(ins) == 2 and all("AJO KILO" not in q for q, _ in ins)
    assert ins[0][1][:4] == ("AJOKG", "AJO KILO", "FRUVE", "KG")

    out = _get(client, h, sol["id"])
    assert out["estado"] == "OK" and out["resultado"]["03"]["ok"] is True
    assert client.get(f"/api/v1/productos/{env['prod']}", headers=h).json()["clave_sae"] == "AJOKG"
    # la cola quedó vacía: la siguiente pasada no escribe nada
    assert sae_escritura.pasada()["hechas"] == []
    assert len(sae.escrituras("INSERT INTO INVE")) == 2


def test_clave_que_ya_existe_no_se_vuelve_a_insertar(client, env, auth_as, escritor):
    sae = escritor(_SAEFalso(claves={"02": {"AJOKG"}}))
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h, empresas=["02", "03"]).json()
    sae_escritura.pasada()
    out = _get(client, h, sol["id"])
    assert out["estado"] == "OK"
    assert out["resultado"]["02"] == {"ok": True, "clave": "AJOKG", "ya_existia": True}
    assert [p[0] for _, p in sae.escrituras("INSERT INTO INVE")] == ["AJOKG"]   # sólo la 03


def test_una_empresa_que_falla_queda_parcial_y_no_se_reintenta(client, env, auth_as, escritor):
    sae = escritor(_SAEFalso(fallan={"03"}))
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h, empresas=["02", "03"]).json()
    sae_escritura.pasada()
    out = _get(client, h, sol["id"])
    assert out["estado"] == "PARCIAL"
    assert "Msg 2627" in out["resultado"]["03"]["error"] and "03" in out["motivo"]
    antes = len(sae.escrituras("INSERT"))
    sae_escritura.pasada()
    assert len(sae.escrituras("INSERT")) == antes


def test_sin_sae_no_se_escribe_nada_y_se_puede_volver_a_pedir(client, env, auth_as, escritor):
    sae = escritor(_SAEFalso(contesta=False))
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h).json()
    sae_escritura.pasada()
    out = _get(client, h, sol["id"])
    assert out["estado"] == "ERROR" and "volver a pedir" in out["motivo"]
    assert sae.sql == []
    # cerrada como ERROR ya no está viva: pedirla de nuevo crea otra
    assert _pedir(client, h).json()["id"] != sol["id"]


def test_con_el_escritor_encendido_el_bot_no_puede_reclamar(client, env, auth_as, conector, escritor):
    """UN SOLO ESCRITOR: la puerta del bot deja de entregar."""
    escritor(_SAEFalso())
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _pedir(client, h).json()
    auth_as(conector)
    assert client.get("/api/v1/productos/alta-sae/pendiente", headers=h).json() is None
    auth_as(env["admin"])
    assert _get(client, h, sol["id"])["estado"] == "PENDIENTE"


def test_con_el_escritor_apagado_el_bot_solo_recibe_altas(client, env, auth_as, conector):
    """El aplicador del bot no sabe hacer cambios: entregarle uno sería
    cerrarlo como hecho sin haberlo escrito."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert _cambio(client, h, linea="FRUVE").status_code == 201
    auth_as(conector)
    assert client.get("/api/v1/productos/alta-sae/pendiente", headers=h).json() is None
    auth_as(env["admin"])
    alta = _pedir(client, h, clave="OTRA").json()
    auth_as(conector)
    assert client.get("/api/v1/productos/alta-sae/pendiente", headers=h).json()["id"] == alta["id"]


def test_cambio_valida_al_pedirlo(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    assert _cambio(client, h).status_code == 422                        # nada que cambiar
    assert _cambio(client, h, unidad="BULTO").status_code == 422        # unidad desconocida
    assert _cambio(client, h, sat="123").status_code == 422             # SAT de 8 dígitos
    assert _cambio(client, h, clave="AJO KG!", linea="FRUVE").status_code == 422
    assert _cambio(client, h, linea="FRUVE", empresas=["99"]).status_code == 422
    # el precio no es un cambio de producto: se ignora y no queda nada que cambiar
    assert _cambio(client, h, precio=10).status_code == 422


def test_dos_cambios_pendientes_a_la_misma_clave_son_uno(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    a = _cambio(client, h, linea="fruve", empresas=["02"]).json()
    b = _cambio(client, h, unidad="kilo", empresas=["03"]).json()
    assert a["id"] == b["id"] and b["tipo"] == "CAMBIO"
    assert b["datos"] == {"linea": "FRUVE", "unidad": "KILO"}
    assert b["empresas"] == ["02", "03"]
    # la lista de siempre (la del acuse del bot) sigue siendo sólo de altas
    r = client.get("/api/v1/productos/alta-sae", headers=h).json()
    assert all(s["tipo"] == "ALTA" for s in r["items"])


def test_el_cambio_se_escribe_y_una_clave_ausente_se_dice(client, env, auth_as, escritor):
    sae = escritor(_SAEFalso(claves={"02": {"AJOKG"}}))
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _cambio(client, h, descripcion="Ajo morado", unidad="KILO", reactivar=True,
                  producto_id=env["prod"], empresas=["02", "03"]).json()
    sae_escritura.pasada()
    out = _get(client, h, sol["id"])
    assert out["estado"] == "PARCIAL"
    assert out["resultado"]["03"]["error"] == "la clave AJOKG no existe en la empresa 03"
    upd = sae.escrituras("UPDATE")
    q, p = upd[0]
    assert "STATUS = 'A'" in q and "AJO MORADO" not in q
    assert p == ("AJO MORADO", "KG", "KGM", "AJOKG")
    # un cambio no estampa claves en el producto
    assert client.get(f"/api/v1/productos/{env['prod']}", headers=h).json()["clave_sae"] in (None, "")


def test_linea_que_no_existe_en_sae_no_se_escribe(client, env, auth_as, escritor):
    sae = escritor(_SAEFalso(claves={"02": {"AJOKG"}}))
    auth_as(env["admin"]); h = _hdr(env["admin"])
    sol = _cambio(client, h, linea="NUEVA", empresas=["02"]).json()
    sae_escritura.pasada()
    out = _get(client, h, sol["id"])
    assert out["estado"] == "ERROR" and "NUEVA no existe" in out["resultado"]["02"]["error"]
    assert sae.escrituras("UPDATE") == []
