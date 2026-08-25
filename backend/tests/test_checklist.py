"""El checklist de primeros pasos (helper puro armar_checklist)."""
from app.api.v1.empresa import armar_checklist


def _flags(**overrides):
    base = dict(fiscal=False, logo=False, correo=False, productos=False,
                clientes=False, listas=False, series=False, primera_factura=False)
    base.update(overrides)
    return base


def test_vacio_ocho_pasos_siguiente_es_fiscal():
    r = armar_checklist(**_flags())
    assert r["total"] == 8 and r["completos"] == 0
    assert r["todo_listo"] is False
    assert r["siguiente"] == "fiscal"
    assert [p["id"] for p in r["pasos"]][0] == "fiscal"
    assert all(p["href"].startswith("/") for p in r["pasos"])


def test_avance_parcial_apunta_al_primero_incompleto():
    r = armar_checklist(**_flags(fiscal=True, logo=True))
    assert r["completos"] == 2
    assert r["siguiente"] == "correo"


def test_todo_listo():
    r = armar_checklist(**{k: True for k in _flags()})
    assert r["completos"] == 8 and r["todo_listo"] is True
    assert r["siguiente"] is None
