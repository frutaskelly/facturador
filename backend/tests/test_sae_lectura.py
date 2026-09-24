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


def test_el_tipo_de_documento_decide_la_tabla(monkeypatch):
    """Las facturas viven en FACTF y los pedidos en FACTP. El bot pregunta por
    los dos cuando va a mover una entrega de semana: el folio viaja dentro de
    la observación de ambos."""
    vistas = []
    monkeypatch.setattr(sae_lectura, "consultar",
                        lambda sql, params=(), timeout=None: vistas.append(sql) or [])
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    sae_lectura.documentos_de("03", "VH-36PAL-SAB", tipo="factura")
    sae_lectura.documentos_de("03", "VH-36PAL-SAB", tipo="pedido")
    assert "FACTF03" in vistas[0] and "CFDI03" in vistas[0]      # la factura se timbra
    assert "FACTP03" in vistas[1] and "CFDI03" not in vistas[1]  # el pedido no
    with pytest.raises(ValueError):
        sae_lectura.documentos_de("03", "x", tipo="remision")


def test_las_partidas_van_por_parametro_y_con_tope(monkeypatch):
    """La lista de documentos entra como parámetros, uno por marcador, nunca
    concatenada — y con tope, porque un IN gigantesco tumba la consulta."""
    visto = {}
    def _fake(sql, params=(), timeout=None):
        visto["sql"], visto["params"] = sql, params
        return []
    monkeypatch.setattr(sae_lectura, "consultar", _fake)
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    sae_lectura.partidas_de("03", ["ZEHMOVH 1442", "ZEHMOVH 1443"])
    assert visto["sql"].count("%s") == 2
    assert visto["params"] == ("ZEHMOVH1442", "ZEHMOVH1443")   # sin el relleno de SAE
    assert sae_lectura.partidas_de("03", []) == []
    with pytest.raises(ValueError):
        sae_lectura.partidas_de("03", [f"D{i}" for i in range(201)])


def test_las_rutas_estan_registradas_y_pedidas_con_permiso(client):
    """La puerta existe y cuelga del prefijo de siempre. Las pruebas de extremo
    a extremo de estas rutas viven con las del espejo, que ya montan el tenant;
    aquí lo que importa es que el conector no deje pasar nada que no sea leer."""
    from app.main import app
    rutas = {r.path for r in app.routes}
    assert "/api/v1/sae/salud" in rutas and "/api/v1/sae/facturas" in rutas
    assert "/api/v1/sae/partidas" in rutas
    # sin credencial no se lee SAE
    assert client.get("/api/v1/sae/salud").status_code in (401, 403)
    assert client.get("/api/v1/sae/facturas",
                      params={"empresa": "03", "q": "VH-36PAL-SAB"}).status_code in (401, 403)


def test_la_marca_de_agua_pide_solo_lo_nuevo(monkeypatch):
    """El espejo es su propia marca de agua: se piden a SAE los folios por
    encima del más alto que ya se tiene. Sin tabla de estado que se pueda
    desincronizar — y si alguien borra una factura del espejo, la siguiente
    pasada la vuelve a traer sola."""
    from app.services import espejo_sae
    visto = {}

    def _fake(sql, params=(), timeout=None):
        visto["sql"], visto["params"] = sql, params
        return []

    monkeypatch.setattr(sae_lectura, "consultar", _fake)
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    espejo_sae.leer_encabezados("03", "ZEHMOVH", desde_folio=1442)
    assert "F.FOLIO > %s" in visto["sql"]
    assert visto["params"] == ("ZEHMOVH", 1442)
    # sin marca, se pide todo lo de la serie
    espejo_sae.leer_encabezados("03", "ZEHMOVH")
    assert "F.FOLIO >" not in visto["sql"] and visto["params"] == ("ZEHMOVH",)


def test_una_factura_sin_uuid_no_se_refleja_como_timbrada(monkeypatch):
    """El documento existe en SAE pero el PAC no lo confirmó: eso es BORRADOR.
    El backend además rechaza TIMBRADA sin uuid_fiscal."""
    from app.services import espejo_sae
    fila = {"cve_doc": "ZEHMOVH 9", "serie": "ZEHMOVH", "folio": 9, "cliente_sae": "6",
            "fecha": "2026-09-24 00:00:00", "subtotal": "100", "total": "100",
            "iva": "0", "ieps": "0", "status": "", "uuid": "", "fecha_cancela": "",
            "observaciones": "OC X", "cancelacion_msj": "", "uuid_sustitucion": ""}
    monkeypatch.setattr(sae_lectura, "consultar", lambda *a, **k: [fila])
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    assert espejo_sae.leer_encabezados("03", "ZEHMOVH")[0]["estado"] == "BORRADOR"
    fila["uuid"] = "u-1"
    assert espejo_sae.leer_encabezados("03", "ZEHMOVH")[0]["estado"] == "TIMBRADA"
    fila["fecha_cancela"] = "2026-09-24"
    assert espejo_sae.leer_encabezados("03", "ZEHMOVH")[0]["estado"] == "CANCELADA"


def test_el_reloj_no_corre_sin_tenant(monkeypatch):
    """Sin `ESPEJO_SAE_TENANT_ID` la pasada no corre. No hay manera honesta de
    adivinar de quién es el espejo, y equivocarse sería escribir las facturas
    en el tenant que no es."""
    from app.services import espejo_sae
    from app.core.config import settings as s
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    monkeypatch.setattr(s, "ESPEJO_SAE_TENANT_ID", "")
    assert espejo_sae.pasada_programada()["corrio"] is False
    # y tampoco si no hay acceso a SAE, aunque el tenant esté puesto
    monkeypatch.setattr(s, "ESPEJO_SAE_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(sae_lectura, "disponible", lambda: False)
    assert espejo_sae.pasada_programada()["corrio"] is False


def test_el_saldo_sale_del_total_menos_lo_abonado(monkeypatch):
    """Los REP traen IMPORTE=0 —el importe real vive en el XML— pero CxC ya los
    tiene aplicados factura por factura. El saldo es total menos abonado, y
    nunca negativo."""
    from app.services import espejo_sae
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    monkeypatch.setattr(sae_lectura, "consultar",
                        lambda *a, **k: [{"doc": "ZEHMOVH 1442", "abonado": "1000.00"}])
    assert espejo_sae.leer_abonos("03", ["ZEHMOVH 1442"]) == {"ZEHMOVH 1442": 1000.0}
    p = espejo_sae.como_payload("03", {
        "serie": "ZEHMOVH", "folio": 1442, "cliente_sae": "6", "fecha": None,
        "estado": "TIMBRADA", "uuid": "u-1", "cancelacion_msj": None,
        "observaciones": None, "subtotal": "1500", "total": "1500",
        "iva": "0", "ieps": "0", "uuid_sustitucion": None}, [], saldo=500.0)
    assert float(p.saldo_insoluto) == 500.0
