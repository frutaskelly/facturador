"""Los catálogos que toda empresa nueva recibe: impuestos y categorías.

El semilla dejó de ser la tabla IMPU02 del SAE (que era la de Frutas Kelly, con
un "16% IVA" duplicado y cuatro nombres que prometían IEPS con la tasa en 0).
Lo que estas pruebas cuidan es justo lo que aquel arrastraba.
"""
from decimal import Decimal

from app.services.catalogos_default import (
    CATEGORIAS_DEFAULT,
    ESQUEMAS_IMPUESTO_DEFAULT,
)


def _por_codigo():
    return {e["codigo"]: e for e in ESQUEMAS_IMPUESTO_DEFAULT}


def test_el_nombre_dice_la_tasa_que_cobra():
    """El defecto que este catálogo viene a corregir: un esquema llamado
    '+ 8% IEPS' con la tasa en 0 promete un impuesto que el CFDI no estampa."""
    for e in ESQUEMAS_IMPUESTO_DEFAULT:
        if "IEPS" not in e["nombre"]:
            assert "ieps" not in e, e["codigo"]
            continue
        # Los de cuota ($/litro) no llevan tasa: la cuota se captura aparte.
        if e.get("tipo_ieps") == "CUOTA":
            assert "ieps" not in e and e["ieps_cuota"] == "0", e["codigo"]
            continue
        esperado = Decimal(e["nombre"].split("IEPS")[1].strip().rstrip("%")) / 100
        assert Decimal(e["ieps"]) == esperado, e["codigo"]


def test_no_hay_dos_esquemas_iguales():
    """El SAE traía 1 y 6 idénticos y nadie sabía cuál usar."""
    codigos = [e["codigo"] for e in ESQUEMAS_IMPUESTO_DEFAULT]
    assert len(codigos) == len(set(codigos))
    huella = [
        (e["iva"], e.get("ieps", "0"), e.get("tipo_ieps", "TASA"),
         e.get("ret_iva", "0"), bool(e.get("exento")))
        for e in ESQUEMAS_IMPUESTO_DEFAULT
    ]
    assert len(huella) == len(set(huella))


def test_estan_los_cuatro_de_uso_diario():
    """Sin estos cuatro no se puede facturar un abarrote."""
    e = _por_codigo()
    assert Decimal(e["IVA16"]["iva"]) == Decimal("0.16")
    assert Decimal(e["IVA0"]["iva"]) == 0
    assert Decimal(e["IEPS8"]["iva"]) == 0 and Decimal(e["IEPS8"]["ieps"]) == Decimal("0.08")
    assert e["SABORIZADA"]["tipo_ieps"] == "CUOTA"


def test_las_tasas_de_bebidas_alcoholicas_no_se_confunden():
    """25% y 26.5% NO son la misma tasa en dos épocas: son productos distintos
    (energetizantes vs. vino de mesa). Confundirlos fue el malentendido que
    venía heredado del SAE."""
    e = _por_codigo()
    assert Decimal(e["IEPS25"]["ieps"]) == Decimal("0.25")
    assert "energetizantes" in e["IEPS25"]["descripcion"].lower()
    assert Decimal(e["IEPS265"]["ieps"]) == Decimal("0.265")
    assert Decimal(e["IEPS30"]["ieps"]) == Decimal("0.30")
    assert Decimal(e["IEPS53"]["ieps"]) == Decimal("0.53")


def test_solo_exento_es_exento():
    """Exento y 0% no son lo mismo: en el CFDI viajan distinto."""
    exentos = [e["codigo"] for e in ESQUEMAS_IMPUESTO_DEFAULT if e.get("exento")]
    assert exentos == ["EXENTO"]


def test_la_cuota_por_litro_se_siembra_vacia():
    """Se actualiza por inflación cada año: un número que envejece en silencio
    es peor que uno vacío que obliga a capturarlo."""
    e = _por_codigo()["SABORIZADA"]
    assert e["ieps_cuota"] == "0"
    assert "captura" in e["descripcion"].lower()


def test_hay_un_esquema_con_retencion():
    """El SAE no traía ninguno y el flete a persona moral la lleva."""
    con_ret = [e for e in ESQUEMAS_IMPUESTO_DEFAULT if e.get("ret_iva")]
    assert [e["codigo"] for e in con_ret] == ["FLETE-RET"]
    assert Decimal(con_ret[0]["ret_iva"]) == Decimal("0.04")


def test_todos_traen_nombre_y_descripcion():
    for e in ESQUEMAS_IMPUESTO_DEFAULT:
        assert e["nombre"].strip() and e["descripcion"].strip(), e["codigo"]


# ─── categorías ──────────────────────────────────────────────────────────────
def test_las_categorias_cubren_los_departamentos_reales():
    """Salen de lo que el negocio real tiene en producción, no de una lluvia de
    ideas: fruta/verdura, abarrote, lácteos, pan, proteína, congelados,
    plásticos y limpieza."""
    nombres = {n.lower() for n, _ in CATEGORIAS_DEFAULT}
    for esperado in ("frutas", "verduras", "abarrotes", "lácteos", "carnes",
                     "panadería", "congelados", "limpieza", "desechables"):
        assert esperado in nombres, esperado


def test_las_categorias_no_se_repiten_y_traen_descripcion():
    nombres = [n.strip().lower() for n, _ in CATEGORIAS_DEFAULT]
    assert len(nombres) == len(set(nombres))
    for nombre, desc in CATEGORIAS_DEFAULT:
        assert nombre.strip() and desc.strip(), nombre


# ─── la siembra de verdad, contra la base ────────────────────────────────────
def test_sembrar_deja_la_empresa_lista_para_trabajar(db_engine):
    """Las listas de arriba son la intención; esto comprueba lo que de verdad
    queda en la base: los códigos los deriva el servidor y la siembra no puede
    duplicar si se corre dos veces (el alta de una empresa hija la repite)."""
    import uuid as _uuid

    from app.core.db import SessionLocal
    from app.models import CategoriaProducto, EsquemaImpuesto, Tenant
    from app.services.catalogos_default import (
        categoria_sin_categorizar,
        sembrar_categorias,
        sembrar_esquemas_impuesto,
    )

    db = SessionLocal()
    try:
        suf = _uuid.uuid4().hex[:8]
        t = Tenant(slug=f"seed-{suf}", legal_name="Semilla SA",
                   rfc=f"S{suf.upper()}X"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush()

        sembrar_esquemas_impuesto(db, t.id)
        categoria_sin_categorizar(db, t.id)
        sembrar_categorias(db, t.id)
        db.flush()

        esq = db.query(EsquemaImpuesto).filter(EsquemaImpuesto.tenant_id == t.id).all()
        assert len(esq) == len(ESQUEMAS_IMPUESTO_DEFAULT)
        # Las tasas llegan a la base, no solo al diccionario.
        por_cod = {e.codigo: e for e in esq}
        assert por_cod["IEPS8"].ieps_tasa == Decimal("0.0800")
        assert por_cod["IEPS53"].ieps_tasa == Decimal("0.5300")
        assert por_cod["SABORIZADA"].tipo_ieps == "CUOTA"
        assert por_cod["FLETE-RET"].retencion_iva_tasa == Decimal("0.0400")
        assert por_cod["EXENTO"].iva_exento is True

        cats = db.query(CategoriaProducto).filter(CategoriaProducto.tenant_id == t.id).all()
        assert len(cats) == len(CATEGORIAS_DEFAULT) + 1        # + «Sin categorizar»
        assert "Sin categorizar" in {c.nombre for c in cats}
        # El código lo pone el servidor y es único dentro de la empresa.
        codigos = [c.codigo for c in cats]
        assert all(codigos) and len(codigos) == len(set(codigos))

        # Idempotente: correrla otra vez no duplica nada.
        sembrar_esquemas_impuesto(db, t.id)
        categoria_sin_categorizar(db, t.id)
        sembrar_categorias(db, t.id)
        db.flush()
        assert db.query(EsquemaImpuesto).filter(
            EsquemaImpuesto.tenant_id == t.id).count() == len(ESQUEMAS_IMPUESTO_DEFAULT)
        assert db.query(CategoriaProducto).filter(
            CategoriaProducto.tenant_id == t.id).count() == len(CATEGORIAS_DEFAULT) + 1

        db.rollback()
    finally:
        db.close()
