"""El checklist de primeros pasos (helper puro armar_checklist)."""
import pytest

from app.api.v1.empresa import PASOS_MARCABLES, armar_checklist

ORDEN = [
    "fiscal", "logo", "correo", "clientes", "series", "esquemas",
    "categorias", "productos", "listas", "remision", "primera_factura",
]


def _flags(**overrides):
    base = {k: False for k in (
        "fiscal", "logo", "correo", "clientes", "series", "esquemas",
        "categorias", "productos", "listas", "remision", "primera_factura",
    )}
    base.update(overrides)
    return base


def test_orden_y_total():
    r = armar_checklist(**_flags())
    assert r["total"] == 11
    assert [p["id"] for p in r["pasos"]] == ORDEN
    assert all(p["href"].startswith("/") for p in r["pasos"])


def test_vacio_siguiente_es_fiscal():
    r = armar_checklist(**_flags())
    assert r["completos"] == 0 and r["todo_listo"] is False
    assert r["siguiente"] == "fiscal"


def test_avance_parcial_apunta_al_primero_incompleto():
    r = armar_checklist(**_flags(fiscal=True, logo=True, correo=True))
    assert r["completos"] == 3
    assert r["siguiente"] == "clientes"


def test_todo_listo():
    r = armar_checklist(**{k: True for k in _flags()})
    assert r["completos"] == 11 and r["todo_listo"] is True
    assert r["siguiente"] is None


def test_solo_esquemas_y_categorias_son_marcables():
    r = armar_checklist(**_flags())
    marcables = {p["id"] for p in r["pasos"] if p["marcable"]}
    assert marcables == PASOS_MARCABLES == {"esquemas", "categorias"}


@pytest.mark.parametrize("paso", ["esquemas", "categorias"])
def test_marcar_a_mano_completa_el_paso(paso):
    r = armar_checklist(**_flags(), marcados={paso})
    p = next(x for x in r["pasos"] if x["id"] == paso)
    assert p["completo"] is True and p["marcado_manual"] is True
    assert r["completos"] == 1


def test_el_dato_real_completa_aunque_no_este_marcado():
    r = armar_checklist(**_flags(esquemas=True))
    p = next(x for x in r["pasos"] if x["id"] == "esquemas")
    assert p["completo"] is True and p["marcado_manual"] is False


def test_marcar_un_paso_no_marcable_no_lo_completa():
    r = armar_checklist(**_flags(), marcados={"productos"})
    p = next(x for x in r["pasos"] if x["id"] == "productos")
    assert p["completo"] is False
