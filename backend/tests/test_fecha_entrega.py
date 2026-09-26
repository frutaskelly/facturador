"""fecha_entrega_de_notas — con los textos reales de las facturas de Chiapas."""
from datetime import date

import pytest

from app.services.fecha_entrega import fecha_entrega_de_notas

F = date(2026, 9, 24)   # fecha de factura típica: días después de entregar


@pytest.mark.parametrize("notas, fecha_factura, esperada", [
    ("SEM 35 ENTREGA PARA COMEDORES DEL 1 DE SEPTIEMBRE DEL 2026. EN: SHANKA # PED 2221",
     F, date(2026, 9, 1)),
    ("SEMANA 38: DIF ENTREGA LUNES 21 DE SEPTIEMBRE DE 2026 EN:MUNICIPIO DE TUXTLA",
     F, date(2026, 9, 21)),
    ("SEM 32 HOSPITAL YAJALON EXTRA 05 AGOSTO 2026. P1132",
     date(2026, 8, 12), date(2026, 8, 5)),
    ("SEMANA 36 ALBERGUE LUNES 07/09/2026", F, date(2026, 9, 7)),
    ("CEREALES CENDIS ENTREGA 07-09-2026", F, date(2026, 9, 7)),
    ("SEMANA 36 CERESOS FESTIVO ENTREGA VIERNES 11/09/2026, SABADO 12/09/2026 Y LUNES 14/09/2026",
     F, date(2026, 9, 11)),
    ("OC 25152 ENTREGA EN BODEGA DOMINGO 23 DE AGOSTO **CLIENTE HIGA**",
     date(2026, 8, 28), date(2026, 8, 23)),
    ("OC HO-35ATL-JUE ATLAPEXCO SEMANA 35 ATLAPEXCO JUEVES 03/09/2026 HO-35ATL-JUE",
     F, date(2026, 9, 3)),
    # Con acentos y minúsculas: se normaliza.
    ("Entrega miércoles 2 de septiembre de 2026", F, date(2026, 9, 2)),
    # Abreviaturas y SETIEMBRE.
    ("ENTREGA 3 SEP 2026", F, date(2026, 9, 3)),
    ("ENTREGA 4 SEPT. 2026", F, date(2026, 9, 4)),
    ("ENTREGA 8 AGO", F, date(2026, 8, 8)),
    ("ENTREGA 10 DE SETIEMBRE", F, date(2026, 9, 10)),
    # Año de dos dígitos.
    ("ENTREGA 07.09.26", F, date(2026, 9, 7)),
    # Variantes vistas en las notas reales de Chiapas (jul–sep 2026).
    ("SEM 37 ENTREGA 10 DE SEPTIEMBRE2026", F, date(2026, 9, 10)),
    ("SEM 36 ENTREGA 3 DE SEMPTIEMBR HOSPITAL", date(2026, 9, 6), date(2026, 9, 3)),
    ("ENTREGA 31 DE AGO0STO  2026", date(2026, 9, 6), date(2026, 8, 31)),
    ("SEM 30 ENTRAGA 27 DE JULIO 2026", date(2026, 8, 1), date(2026, 7, 27)),
    ("ENTREGA 35 CAJAS 2 DE JULIO DEL 2026", date(2026, 7, 1), date(2026, 7, 2)),
    ("ENTREGA 29 DE JUNIO 2026 12 CAMAS 6 DE JUNIO", date(2026, 7, 3), date(2026, 6, 29)),
])
def test_ejemplos_reales(notas, fecha_factura, esperada):
    assert fecha_entrega_de_notas(notas, fecha_factura) == esperada


@pytest.mark.parametrize("notas", [
    "SECRETARIA OFICIALIA MAYOR ENTREGA MIERCOLES 29 EN BODEGA DE TUXTLA",   # sin mes
    "OC 0000024547 ENTREGAR PARA BALLES",
    "",
    None,
    "SEM 35 PED 2221",
    "ENTREGA 04 DE JULIO 2024",          # errata de año: fuera de la ventana
    "5 MAYOR ENTREGA",                   # «MAYOR» no es mayo
])
def test_sin_fecha(notas):
    assert fecha_entrega_de_notas(notas, F) is None


def test_sin_anio_de_diciembre_facturado_en_enero():
    # «28 DE DICIEMBRE» facturado el 5 de enero: 2026-12-28 caería 11 meses
    # DESPUÉS de la factura, así que es el año anterior.
    assert fecha_entrega_de_notas("ENTREGA 28 DE DICIEMBRE", date(2027, 1, 5)) == date(2026, 12, 28)


def test_sin_anio_hasta_siete_dias_despues_se_queda_en_el_anio():
    assert fecha_entrega_de_notas("ENTREGA 30 DE SEPTIEMBRE", F) == date(2026, 9, 30)


def test_fuera_de_ventana_se_descarta():
    # Más de 120 días antes, o más de 7 después: no es la entrega.
    assert fecha_entrega_de_notas("ENTREGA 01/01/2026", F) is None
    assert fecha_entrega_de_notas("ENTREGA 15/10/2026", F) is None
    # Una fecha fuera de ventana no le gana a una buena que viene después.
    assert fecha_entrega_de_notas("VIGENCIA 01/01/2026 ENTREGA 07/09/2026", F) == date(2026, 9, 7)


def test_prefiere_la_primera_despues_de_entrega():
    notas = "PEDIDO DEL 01/09/2026 ENTREGA 03/09/2026"
    assert fecha_entrega_de_notas(notas, F) == date(2026, 9, 3)


def test_sin_entrega_toma_la_primera_del_texto():
    assert fecha_entrega_de_notas("LUNES 07/09/2026 Y MARTES 08/09/2026", F) == date(2026, 9, 7)


def test_si_nada_va_despues_de_entrega_toma_la_primera():
    assert fecha_entrega_de_notas("07/09/2026 ENTREGA EN BODEGA", F) == date(2026, 9, 7)


def test_fecha_imposible_no_truena():
    assert fecha_entrega_de_notas("ENTREGA 31/02/2026", F) is None
    assert fecha_entrega_de_notas("ENTREGA 31 DE SEPTIEMBRE DE 2026", F) is None
