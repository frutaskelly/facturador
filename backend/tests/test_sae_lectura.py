"""El acceso propio del Facturador a SAE: que sea de solo lectura y que su
ausencia se diga, no se caiga.

No hay SAE en CI —ni debe haberlo—, así que lo que se prueba es la puerta: los
candados del conector y cómo contesta la ruta cuando no hay acceso configurado.
"""
import pytest

from app.services import sae_lectura


def test_solo_pasa_un_select(monkeypatch):
    """Lo único que este conector sabe hacer es leer. Escribir en SAE va por
    OTRA puerta (`sae_escritura`, con su usuario y su cola): un INSERT repetido
    duplica un artículo o una factura, y esa garantía se sostiene teniendo un
    solo escritor."""
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


def test_no_se_reescribe_una_factura_cuyo_saldo_no_cambio():
    """Tener un abono reciente no es razón para volver a depositar la factura.

    Con «tiene abono» como único criterio, cada pasada reescribía las mismas:
    a 30 segundos son 2,880 escrituras al día para no cambiar nada, con el
    backend rehaciendo partidas y bloqueando remisiones en cada una. Un
    centavo de diferencia sí; el mismo número, no. Y `None` es «CxC no reporta
    abonos», que no es cero y no pisa lo guardado.
    """
    from app.services.espejo_sae import _saldo_cambio
    assert _saldo_cambio(500.0, 500.0) is False
    assert _saldo_cambio(500.0, 500.004) is False      # ruido de redondeo
    assert _saldo_cambio(500.0, 499.99) is True        # un centavo sí
    assert _saldo_cambio(None, 500.0) is True          # nunca tuvo saldo: se pone
    assert _saldo_cambio(500.0, None) is False         # sin dato no se pisa


def test_el_cuadre_encuentra_el_hueco_y_no_repara_de_mas(monkeypatch):
    """La marca de agua no ve un hueco por debajo de ella: una factura perdida
    queda congelada para siempre. Contar contra contar la encuentra.

    Y repara HASTA el tope: si faltan trescientas eso no es un hueco, es que
    algo se rompió, y traerlas a escondidas taparía el problema.
    """
    from app.services import espejo_sae

    class _Q:
        def __init__(self, folios): self._f = folios
        def filter(self, *a, **k): return self
        def all(self): return [type("F", (), {"folio": n})() for n in self._f]

    class _DB:
        def __init__(self, folios): self._f = folios
        def query(self, *a, **k): return _Q(self._f)

    monkeypatch.setattr(espejo_sae, "folios_en_sae", lambda e, s: {1, 2, 3, 4, 5})
    traidos = []
    monkeypatch.setattr(espejo_sae, "_traer_folios",
                        lambda db, ctx, e, s, fol, err: (traidos.extend(fol) or len(fol), 0))

    r = espejo_sae.cuadre(_DB({1, 2, 5}), None, "03", ["ZEHMOVH"])
    assert r["faltantes"] == 2 and r["series"]["ZEHMOVH"]["folios"] == [3, 4]
    assert traidos == [3, 4] and r["reparadas"] == 2

    # con el tope en 1, dos faltantes ya no se reparan solas: se reportan
    traidos.clear()
    r2 = espejo_sae.cuadre(_DB({1, 2, 5}), None, "03", ["ZEHMOVH"], tope=1)
    assert r2["reparadas"] == 0 and traidos == []
    assert any("algo se rompió" in e for e in r2["errores"]), r2["errores"]

    # sin huecos, ni reporta ni repara
    r3 = espejo_sae.cuadre(_DB({1, 2, 3, 4, 5}), None, "03", ["ZEHMOVH"])
    assert r3["faltantes"] == 0 and r3["errores"] == []


def test_una_factura_sin_equivalencia_se_omite_sin_gritar(monkeypatch):
    """No se puede reflejar una factura de un cliente que el Facturador no sabe
    de quién es —el depósito la rechaza a propósito— y eso no cambia mañana.
    Contarla como error todos los días entrena al equipo a ignorar el reporte.
    Caso real: ZMAFAN 131, cliente sin contrato y factura en cancelación."""
    from app.services import espejo_sae

    def _explota(*a, **k):
        raise RuntimeError("422: Sin equivalencia SAE para '02:1' (cliente_externos)")

    monkeypatch.setattr(espejo_sae, "leer_encabezados",
                        lambda *a, **k: [{"cve_doc": "ZMAFAN 131", "folio": 131}])
    monkeypatch.setattr(espejo_sae, "leer_partidas", lambda *a, **k: {})
    monkeypatch.setattr(espejo_sae, "como_payload", _explota)
    errores = []
    hechas, omitidas = espejo_sae._traer_folios(None, None, "02", "ZMAFAN", [131], errores)
    assert (hechas, omitidas) == (0, 1)
    assert errores == []          # no es un error: es una omisión explicada


def test_los_abonos_de_una_serie_no_se_llevan_los_de_otra(monkeypatch):
    """La serie se pide como prefijo, así que ZEHMO también trae ZEHMOHOS.
    Cada REFER se parte y sólo cuenta el de la serie pedida; las series con
    dígito (ZCH5C) se parten bien."""
    from app.services import espejo_sae
    pedidos = []

    def _fake(sql, params=(), timeout=None):
        pedidos.append(params)
        return [{"doc": "ZEHMO        7", "abonado": "100.0000"},
                {"doc": "ZEHMOHOS   542", "abonado": "999.0000"},
                {"doc": "ZCH5C       12", "abonado": "50.5000"}]

    monkeypatch.setattr(sae_lectura, "consultar", _fake)
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    assert espejo_sae.abonos_de_serie("02", "ZEHMO") == {7: 100.0}
    assert pedidos[-1] == ("ZEHMO%",)
    assert espejo_sae.abonos_de_serie("04", "ZCH5C") == {12: 50.5}


class _FacturasQ:
    def __init__(self, filas): self._f = filas
    def filter(self, *a, **k): return self
    def all(self): return self._f


class _FacturasDB:
    def __init__(self, filas): self._f = filas
    def query(self, *a, **k): return _FacturasQ(self._f)


def _factura(folio, total, saldo, metodo="PPD"):
    return type("F", (), {"folio": folio, "total": total, "saldo_insoluto": saldo,
                          "metodo_pago": metodo})()


def test_el_cuadre_de_saldos_ve_la_factura_que_perdio_su_abono(monkeypatch):
    """Caso real del 28-sep-2026: el pago de EHMO que se aplicó a MAFAN el
    19-sep y SAE corrigió el 22-sep. La CxC ya no tiene esos abonos y ninguna
    pasada vuelve por la factura, porque sólo se revisa la que RECIBE un abono.
    ZMAFAN 186 se quedó «pagada parcial» con $50,059.84 que ya no existían."""
    from app.services import espejo_sae
    db = _FacturasDB([
        _factura(186, 135297.20, 85237.36),     # parcial con un abono que SAE borró
        _factura(156, 135297.20, 0),            # «pagada» sin abono en la CxC
        _factura(114, 59265.71, 0),             # pagada de verdad (REP 10)
        _factura(151, 11339.70, 1781.83),       # parcial de verdad
        _factura(200, 5000.00, 5000.00),        # debe todo y no tiene abonos: bien
        _factura(300, 800.00, 0, metodo="PUE"),  # contado sin CxC: su 0 es correcto
    ])
    monkeypatch.setattr(espejo_sae, "abonos_de_serie",
                        lambda e, s: {114: 59265.71, 151: 9557.87})
    traidas = {}

    def _traer(db, ctx, e, s, folios, errores, saldos=None):
        traidas.update({f: saldos[f] for f in folios})
        return len(folios), 0

    monkeypatch.setattr(espejo_sae, "_traer_folios", _traer)
    r = espejo_sae.cuadre_saldos(db, None, "02", ["ZMAFAN"])
    assert traidas == {156: 135297.20, 186: 135297.20}, traidas
    assert r["distintos"] == 2 and r["corregidos"] == 2 and r["errores"] == []
    assert r["series"]["ZMAFAN"]["folios"] == [156, 186]


def test_el_cuadre_de_saldos_no_corrige_de_mas(monkeypatch):
    """Si difieren más que el tope, lo probable es que la CxC no contestó
    completa: poner a deber cientos de facturas pagadas sería peor que el
    hueco. Se reporta y no se toca nada. Y una CxC que no contesta es un error
    de esa serie, no «todas deben el total»."""
    from app.services import espejo_sae
    db = _FacturasDB([_factura(n, 100.0, 0) for n in range(1, 6)])
    monkeypatch.setattr(espejo_sae, "abonos_de_serie", lambda e, s: {})
    traidas = []
    monkeypatch.setattr(espejo_sae, "_traer_folios",
                        lambda *a, **k: (traidas.extend(a[4]) or len(a[4]), 0))
    r = espejo_sae.cuadre_saldos(db, None, "02", ["ZMAFAN"], tope=3)
    assert traidas == [] and r["corregidos"] == 0 and r["distintos"] == 5
    assert any("no las corrijo a escondidas" in e for e in r["errores"]), r["errores"]

    def _caida(e, s):
        raise sae_lectura.SAENoDisponible("sin red")

    monkeypatch.setattr(espejo_sae, "abonos_de_serie", _caida)
    r2 = espejo_sae.cuadre_saldos(db, None, "02", ["ZMAFAN"])
    assert r2["distintos"] == 0 and traidas == []
    assert any("sin red" in e for e in r2["errores"])


def test_la_reparacion_deposita_el_saldo_medido_aunque_la_cxc_este_vacia(monkeypatch):
    """Sin `saldos`, «la CxC no tiene abonos» es «no hay dato» y no pisa lo
    guardado. Para la factura que perdió sus abonos eso es justo lo que hay
    que pisar, así que el saldo medido por el cuadre manda."""
    from app.services import espejo_sae
    from app.api.v1 import facturas as facturas_api

    monkeypatch.setattr(espejo_sae, "leer_encabezados", lambda *a, **k: [{
        "cve_doc": "ZMAFAN       186", "serie": "ZMAFAN", "folio": 186, "cliente_sae": "4",
        "fecha": "2026-09-14 00:00:00", "estado": "TIMBRADA", "uuid": "u-186",
        "cancelacion_msj": None, "observaciones": None, "subtotal": "135297.20",
        "total": "135297.20", "iva": "0", "ieps": "0", "uuid_sustitucion": None}])
    monkeypatch.setattr(espejo_sae, "leer_partidas", lambda *a, **k: {})
    monkeypatch.setattr(espejo_sae, "leer_abonos", lambda *a, **k: {})
    depositos = []
    monkeypatch.setattr(facturas_api, "factura_espejo",
                        lambda payload, db, ctx: depositos.append(payload))
    errores = []
    hechas, _ = espejo_sae._traer_folios(None, None, "02", "ZMAFAN", [186], errores,
                                         saldos={186: 135297.20})
    assert hechas == 1 and errores == []
    assert float(depositos[0].saldo_insoluto) == 135297.20

    depositos.clear()
    espejo_sae._traer_folios(None, None, "02", "ZMAFAN", [186], errores)
    assert depositos[0].saldo_insoluto is None     # sin medir, no se pisa


def test_la_pasada_reporta_aunque_nadie_haya_presionado_el_boton(monkeypatch):
    """La fecha de «SAE actualizado» que pinta la UI sale del reporte, también
    en las pasadas automáticas. Y una solicitud reclamada y nunca reportada
    deja la pantalla «Sincronizando…» hasta que el backend la expira a la hora,
    así que el reporte sale SIEMPRE — con botón o sin él."""
    from app.services import espejo_sae
    from app.core.config import settings as s

    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    monkeypatch.setattr(s, "ESPEJO_SAE_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(s, "ESPEJO_SAE_EMPRESAS", "")     # sin empresas: la pasada no toca SAE
    # …ni las del catálogo de artículos, que llevan su propia lista (26-sep-2026)
    monkeypatch.setattr(s, "ESPEJO_SAE_CLAVES_EMPRESAS", "")
    reclamos, reportes = [], []
    monkeypatch.setattr(espejo_sae, "_reclamar_solicitud", lambda t: reclamos.append(t) or None)
    monkeypatch.setattr(espejo_sae, "_reportar", lambda t, sol, tot: reportes.append((sol, tot)))

    r = espejo_sae.pasada_programada()
    assert r["corrio"] is False          # sin empresas no hay nada que traer
    assert reportes == []                # …y sin pasada no hay nada que reportar

    monkeypatch.setattr(s, "ESPEJO_SAE_EMPRESAS", "99")   # empresa sin series
    espejo_sae.pasada_programada()
    assert reclamos and reportes, "el botón se reclama y la pasada se reporta"
