"""La comparación de dos versiones del mismo documento (sin base de datos).

Lo que decide si esto sirve o se vuelve ruido que el equipo aprende a ignorar:
qué cuenta como cambio y qué no. Estas pruebas fijan la frontera.
"""
from app.services import oc_cambios


def _doc(lineas, **cab):
    return {"lineas": lineas, **cab}


BASE = _doc(
    [
        {"clave": "SAND-FRUT-6101", "descripcion": "SANDIA DULCINEA",
         "cantidad": "25", "unidad": "PIEZA", "precio": "30.00"},
        {"clave": "PEPI-FRUT-342", "descripcion": "PEPINO",
         "cantidad": "10", "unidad": "KILOGRAMO"},
    ],
    fecha_entrega="2026-09-03",
    observaciones="ENTREGAR ANTES DE LAS 9",
)


def test_el_mismo_documento_no_es_un_cambio():
    assert oc_cambios.diff(BASE, dict(BASE)) is None


def test_reordenar_no_es_un_cambio():
    otro = _doc(list(reversed(BASE["lineas"])), **{k: BASE[k] for k in
                                                  ("fecha_entrega", "observaciones")})
    assert oc_cambios.diff(BASE, otro) is None
    assert oc_cambios.huella(BASE) == oc_cambios.huella(otro)


def test_cantidades_equivalentes_no_son_un_cambio():
    """'10', '10.0' y '10.00' son la misma cantidad; el OCR alterna entre las tres."""
    otro = _doc(
        [dict(BASE["lineas"][0], cantidad="25.00"),
         dict(BASE["lineas"][1], cantidad="10.0")],
        fecha_entrega="2026-09-03", observaciones="ENTREGAR ANTES DE LAS 9",
    )
    assert oc_cambios.diff(BASE, otro) is None


def test_la_clave_manda_sobre_la_descripcion():
    """Misma clave con la descripción reescrita: es el mismo renglón, no uno nuevo."""
    otro = _doc(
        [dict(BASE["lineas"][0], descripcion="SANDIA DULCINEA SIN SEMILLA"),
         BASE["lineas"][1]],
        fecha_entrega="2026-09-03", observaciones="ENTREGAR ANTES DE LAS 9",
    )
    assert oc_cambios.diff(BASE, otro) is None


def test_acentos_y_guiones_no_hacen_ruido():
    """'PIÑA -FRUT-350' y 'PINA-FRUT-350' son la misma clave, igual que en el cruce."""
    a = _doc([{"clave": "PIÑA -FRUT-350", "descripcion": "PIÑA", "cantidad": "3"}])
    b = _doc([{"clave": "PINA-FRUT-350", "descripcion": "PINA", "cantidad": "3"}])
    assert oc_cambios.diff(a, b) is None


def test_cambio_de_cantidad_se_reporta_con_el_antes_y_el_despues():
    otro = _doc(
        [dict(BASE["lineas"][0], cantidad="40"), BASE["lineas"][1]],
        fecha_entrega="2026-09-03", observaciones="ENTREGAR ANTES DE LAS 9",
    )
    d = oc_cambios.diff(BASE, otro)
    cambiada = d["lineas"]["cambiadas"][0]
    assert cambiada["clave"] == "SAND-FRUT-6101"
    assert cambiada["antes"][0]["cantidad"] == "25"
    assert cambiada["ahora"][0]["cantidad"] == "40"
    assert d["resumen"] == "1 partida cambiada"


def test_partida_nueva_y_partida_quitada():
    otro = _doc(
        [BASE["lineas"][0], {"clave": "AJO -FRUT-017", "descripcion": "AJO", "cantidad": "2"}],
        fecha_entrega="2026-09-03", observaciones="ENTREGAR ANTES DE LAS 9",
    )
    d = oc_cambios.diff(BASE, otro)
    assert [x["descripcion"] for x in d["lineas"]["nuevas"]] == ["AJO"]
    assert [x["descripcion"] for x in d["lineas"]["quitadas"]] == ["PEPINO"]
    assert d["lineas"]["cambiadas"] == []


def test_solo_cambia_la_fecha_de_entrega():
    otro = dict(BASE, fecha_entrega="2026-09-05")
    d = oc_cambios.diff(BASE, otro)
    assert d["cabecera"]["fecha_entrega"] == {"antes": "2026-09-03", "ahora": "2026-09-05"}
    assert d["resumen"] == "entrega 2026-09-03 → 2026-09-05"


def test_lo_que_no_cambia_lo_que_se_entrega_no_despierta_a_nadie():
    """Otro nombre de archivo, otro link de Drive, otras pistas del cliente."""
    otro = dict(BASE, archivo_nombre="OCO 25390 v2.pdf",
                archivo_url="https://drive.google.com/file/d/otro/view",
                nombre="OPERADORA BALLES VEGA DE HIDALGO SA DE CV")
    assert oc_cambios.diff(BASE, otro) is None


def test_documento_vacio_contra_uno_con_partidas():
    d = oc_cambios.diff({}, BASE)
    assert len(d["lineas"]["nuevas"]) == 2
    assert d["lineas"]["quitadas"] == []


def test_una_linea_que_no_es_dict_no_tumba_la_comparacion():
    """El payload lo escribe el bot: si un día manda basura, esto no revienta."""
    assert oc_cambios.diff(BASE, _doc(["basura", None] + BASE["lineas"],
                                      fecha_entrega="2026-09-03",
                                      observaciones="ENTREGAR ANTES DE LAS 9")) is None


def test_cantidad_no_numerica_se_compara_como_texto():
    """Sin inventar un 0 que haría pasar por iguales dos partidas distintas."""
    a = _doc([{"clave": "X-Y-1", "descripcion": "X", "cantidad": "A GRANEL"}])
    b = _doc([{"clave": "X-Y-1", "descripcion": "X", "cantidad": "POR DEFINIR"}])
    assert oc_cambios.diff(a, b) is not None
    assert oc_cambios.diff(a, dict(a)) is None
