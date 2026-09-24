"""El acceso propio del Facturador a SAE: que sea de solo lectura y que su
ausencia se diga, no se caiga.

No hay SAE en CI —ni debe haberlo—, así que lo que se prueba es la puerta: los
candados del conector y cómo contesta la ruta cuando no hay acceso configurado.
"""
import pytest

from app.services import sae_lectura


def test_solo_pasa_un_select(monkeypatch):
    """Lo único que este conector sabe hacer es leer. Las escrituras a SAE
    siguen siendo del bot y de su cola: un INSERT repetido duplica una factura,
    y esa garantía se sostiene teniendo un solo escritor."""
    monkeypatch.setattr(sae_lectura.settings, "SAE_SERVER", "1.2.3.4,1433")
    monkeypatch.setattr(sae_lectura.settings, "SAE_USER", "u")
    monkeypatch.setattr(sae_lectura.settings, "SAE_PASSWORD", "p")
    for sql in ("UPDATE INVE02 SET PRECIO=0",
                "DELETE FROM FACTF02",
                "INSERT INTO INVE02 (CVE_ART) VALUES ('X')",
                "SELECT 1; DROP TABLE INVE02",
                "  exec sp_who"):
        with pytest.raises(ValueError):
            sae_lectura.consultar(sql)


def test_la_empresa_no_entra_cruda_al_sql():
    """La empresa es lo ÚNICO que se concatena al nombre de la tabla, así que es
    lo único que hay que blindar."""
    assert sae_lectura.tabla("FACTF", "03") == "FACTF03"
    for mala in ("3", "003", "0x", "02;--", "", None):
        with pytest.raises(ValueError):
            sae_lectura.tabla("FACTF", mala)
    with pytest.raises(ValueError):
        sae_lectura.tabla("FACTF; DROP", "02")


def test_sin_configuracion_no_hay_puerta(monkeypatch):
    monkeypatch.setattr(sae_lectura.settings, "SAE_SERVER", "")
    assert sae_lectura.disponible() is False
    with pytest.raises(sae_lectura.SAENoDisponible):
        sae_lectura.consultar("SELECT 1")


def test_las_rutas_estan_registradas_y_pedidas_con_permiso(client):
    """La puerta existe y cuelga del prefijo de siempre. Las pruebas de extremo
    a extremo de estas rutas viven con las del espejo, que ya montan el tenant;
    aquí lo que importa es que el conector no deje pasar nada que no sea leer."""
    from app.main import app
    rutas = {r.path for r in app.routes}
    assert "/api/v1/sae/salud" in rutas and "/api/v1/sae/facturas" in rutas
    # sin credencial no se lee SAE
    assert client.get("/api/v1/sae/salud").status_code in (401, 403)
    assert client.get("/api/v1/sae/facturas",
                      params={"empresa": "03", "q": "VH-36PAL-SAB"}).status_code in (401, 403)
