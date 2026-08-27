"""Los esquemas de impuesto que toda empresa nueva recibe."""
from decimal import Decimal

from app.services.catalogos_default import ESQUEMAS_IMPUESTO_DEFAULT


def test_son_los_ocho_de_sae_con_sus_codigos():
    assert len(ESQUEMAS_IMPUESTO_DEFAULT) == 8
    assert [e["codigo"] for e in ESQUEMAS_IMPUESTO_DEFAULT] == list("12345678")


def test_tasas_de_iva_correctas():
    iva = {e["codigo"]: Decimal(e["iva"]) for e in ESQUEMAS_IMPUESTO_DEFAULT}
    # 0% en alimentos (2), exento (3) y dulces/botanas (7); 16% en el resto.
    assert iva["2"] == 0 and iva["3"] == 0 and iva["7"] == 0
    for codigo in ("1", "4", "5", "6", "8"):
        assert iva[codigo] == Decimal("0.16"), codigo


def test_solo_el_3_es_exento():
    exentos = [e["codigo"] for e in ESQUEMAS_IMPUESTO_DEFAULT if e.get("exento")]
    assert exentos == ["3"]


def test_ninguno_cobra_ieps_aunque_el_nombre_lo_diga():
    """Verificado en la base de SAE: IMPUESTO1..8 = 0 en los 8 esquemas.
    Los nombres con '+ N% IEPS' son etiquetas para reconocerlos."""
    con_ieps_en_el_nombre = [e["codigo"] for e in ESQUEMAS_IMPUESTO_DEFAULT if "IEPS" in e["nombre"]]
    assert con_ieps_en_el_nombre == ["4", "5", "7", "8"]
    # Ninguno declara tasa de IEPS: la siembra siempre la pone en 0.
    assert all("ieps" not in e for e in ESQUEMAS_IMPUESTO_DEFAULT)


def test_todos_traen_nombre_y_descripcion():
    for e in ESQUEMAS_IMPUESTO_DEFAULT:
        assert e["nombre"].strip() and e["descripcion"].strip(), e["codigo"]
