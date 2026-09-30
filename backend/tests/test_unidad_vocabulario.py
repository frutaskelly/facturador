"""La unidad con la que entra una partida (regla del dueño, 30-sep-2026).

Un producto tiene variantes por unidad (SANDIA en KILO → SANDIAKG, en PIEZA →
SANDIAPZ, cada una con su precio). Orden para decidir la unidad del sistema:
vocabulario con la unidad de la orden → unidad de la orden (diccionario) →
vocabulario del texto → habitual del cliente / del producto (adivinada).
Sin base de datos: `decidir_unidad` es pura y el cotizador la usa igual.
"""
import uuid

from app.models import Producto
from app.services.cotizador import _presentacion_para
from app.services.producto_match import ReglaUnidad, decidir_unidad, normalizar_unidad_oc

SANDIA = uuid.uuid4()
OTRO = uuid.uuid4()
PRES = {"KILO": 1, "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "SANDIAPZ"}}


def _dec(texto, unidad, vocab=None, habitual=None):
    return decidir_unidad(texto=texto, unidad_raw=unidad, producto_id=SANDIA,
                          presentaciones=PRES, unidad_base="KILO", presentacion_default="KILO",
                          vocab=vocab or {}, habitual_cliente=habitual)


def test_normaliza_la_unidad_tal_como_la_escribe_la_orden():
    assert normalizar_unidad_oc("Pz.") == "PZ"
    assert normalizar_unidad_oc(" malla chica ") == "MALLACHICA"
    assert normalizar_unidad_oc("") is None and normalizar_unidad_oc(None) is None


def test_el_orden_de_la_regla():
    vocab = {
        ("sandia", "MALLA"): ReglaUnidad(SANDIA, "PIEZA", False),
        ("sandia pza", ""): ReglaUnidad(SANDIA, "PIEZA", False),
        ("sandia", "KG"): ReglaUnidad(SANDIA, "PIEZA", True),   # el vocabulario manda
    }
    # 1. el renglón con esa unidad de la orden, aun contra el diccionario
    assert _dec("SANDIA", "kg", vocab).presentacion == "PIEZA"
    assert _dec("SANDIA", "MALLA", vocab).fuente == "vocabulario_unidad"
    # 2. la unidad de la orden por el diccionario
    d = _dec("SANDIA", "PZA")
    assert (d.presentacion, d.adivinada, d.fuente) == ("PIEZA", False, "orden")
    # 3. sin unidad, la del renglón del texto
    d = _dec("Sandía PZA", None, vocab)
    assert (d.presentacion, d.adivinada) == ("PIEZA", False)
    # contradicción: la orden dice KG y el vocabulario del texto PIEZA
    d = _dec("SANDIA PZA", "KG", vocab)
    assert d.conflicto and d.adivinada and d.presentacion == "KILO"
    # 4. nadie dijo nada: la habitual del cliente, marcada como adivinada
    d = _dec("SANDIA", None, habitual="PIEZA")
    assert (d.presentacion, d.adivinada, d.fuente) == ("PIEZA", True, "cliente")
    assert _dec("SANDIA", "", {}).presentacion == "KILO"


def test_una_regla_de_otro_producto_o_de_una_unidad_que_no_vende_no_cuenta():
    vocab = {("sandia pza", ""): ReglaUnidad(OTRO, "PIEZA", False),
             ("sandia", "CJA"): ReglaUnidad(SANDIA, "CAJA", False)}
    assert _dec("SANDIA PZA", None, vocab).adivinada is True
    # CJA → CAJA por diccionario; SANDIA no la vende, pero lo decide quien revisa
    assert _dec("SANDIA", "CJA", vocab).fuente == "orden"


def test_el_cotizador_ya_no_pone_la_habitual_del_cliente_por_encima_de_la_orden():
    """EHMO tiene la SANDIA registrada en KILO: «2 PZ» se cotizaba 2 KILO."""
    prod = Producto(id=SANDIA, unidad_base="KILO", presentacion_default="KILO", presentaciones=PRES)
    assert _presentacion_para(prod, "PZ", "KILO") == "PIEZA"
    assert _presentacion_para(prod, "", "PIEZA") == "PIEZA"        # sin unidad: la del cliente
    assert _presentacion_para(prod, "BOLSA", None) == "KILO"       # no la vende: la del producto
    vocab = {("sandia pza", ""): ReglaUnidad(SANDIA, "PIEZA", False)}
    assert _presentacion_para(prod, "", "KILO", texto="SANDIA PZA", vocab=vocab) == "PIEZA"
