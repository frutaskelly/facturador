"""Catálogos que toda empresa nueva necesita para poder trabajar desde el día 1.

Se siembran al dar de alta el tenant (registro autoservicio y empresa hija del
grupo): sin esquemas de impuesto no se puede dar de alta un producto con sus
tasas, y arrancar con la pantalla vacía obliga a teclear lo mismo siempre.

Todo lo sembrado es 100% EDITABLE por el usuario (nombre, tasas, descripción).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import CategoriaProducto, EsquemaImpuesto

# El catálogo fiscal con el que arranca una empresa nueva.
#
# NO es la tabla IMPU02 del SAE. Esa se copió para Frutas Kelly, que la tiene a
# su medida, y arrastraba tres defectos que no queremos heredarle a nadie: un
# "16% IVA" DUPLICADO, cuatro nombres que prometían IEPS con la tasa en 0, y
# ninguna retención. Aquí el nombre dice exactamente la tasa que cobra.
#
# El IEPS entra a la base del IVA (ver services/fiscal.py): el IVA se calcula
# sobre importe + IEPS, no sobre el importe solo.
#
# Todo esto es EDITABLE y BORRABLE por el usuario. Se siembran todos —no solo
# los cuatro de uso diario— porque borrar el que sobra es un clic, y descubrir
# a los seis meses que llevas facturando vino sin IEPS es otra cosa.
ESQUEMAS_IMPUESTO_DEFAULT: list[dict] = [
    # ── Los de todos los días ────────────────────────────────────────────────
    {"codigo": "IVA16", "nombre": "IVA 16%", "iva": "0.16",
     "descripcion": "Lo general: no alimentos, limpieza, desechables, servicios"},
    {"codigo": "IVA0", "nombre": "IVA 0% — alimentos", "iva": "0",
     "descripcion": "Canasta básica: fruta, verdura, carne, leche, pan, abarrotes"},
    # Exento NO es lo mismo que 0%: en el CFDI viajan distinto (el exento no
    # lleva traslado). Tenerlos separados evita facturar como 0% algo exento.
    {"codigo": "EXENTO", "nombre": "Exento de IVA", "iva": "0", "exento": True,
     "descripcion": "Lo que NO causa IVA. Para alimentos va IVA0, no éste."},
    {"codigo": "IEPS8", "nombre": "IVA 0% + IEPS 8%", "iva": "0", "ieps": "0.08",
     "descripcion": "Botanas, dulces, chocolate, galletas dulces, cereal azucarado"},
    # La cuota por litro se actualiza por inflación cada año. Se siembra en 0 a
    # propósito: un número que envejece en silencio es peor que uno vacío que
    # obliga a capturarlo.
    {"codigo": "SABORIZADA", "nombre": "IVA 16% + IEPS cuota por litro", "iva": "0.16",
     "tipo_ieps": "CUOTA", "ieps_cuota": "0",
     "descripcion": "Refrescos y bebidas saborizadas. CAPTURA la cuota vigente por litro (se actualiza cada año)."},

    # ── Bebidas con alcohol y energetizantes: de nicho, pero caros de errar ──
    # 25% y 26.5% NO son la misma tasa en dos épocas: son productos distintos.
    {"codigo": "IEPS25", "nombre": "IVA 16% + IEPS 25%", "iva": "0.16", "ieps": "0.25",
     "descripcion": "Bebidas energetizantes y sus concentrados/polvos"},
    {"codigo": "IEPS265", "nombre": "IVA 16% + IEPS 26.5%", "iva": "0.16", "ieps": "0.265",
     "descripcion": "Bebidas alcohólicas hasta 14° G.L.: vino de mesa, sidra, rompope"},
    {"codigo": "IEPS30", "nombre": "IVA 16% + IEPS 30%", "iva": "0.16", "ieps": "0.30",
     "descripcion": "Bebidas alcohólicas de más de 14° y hasta 20° G.L."},
    {"codigo": "IEPS53", "nombre": "IVA 16% + IEPS 53%", "iva": "0.16", "ieps": "0.53",
     "descripcion": "Bebidas alcohólicas de más de 20° G.L.: tequila, ron, whisky, vodka"},

    # ── Retención ────────────────────────────────────────────────────────────
    {"codigo": "FLETE-RET", "nombre": "IVA 16% + retención IVA 4%", "iva": "0.16",
     "ret_iva": "0.04",
     "descripcion": "Autotransporte terrestre de carga facturado a persona moral"},
]


def sembrar_esquemas_impuesto(db: Session, tenant_id) -> list[EsquemaImpuesto]:
    """Crea los esquemas que falten para el tenant, por código.

    Idempotente: no duplica ni pisa lo que el usuario ya tenga. No hace commit
    (lo hace el llamador dentro de su propia transacción).
    """
    existentes = {
        c for (c,) in db.query(EsquemaImpuesto.codigo)
        .filter(EsquemaImpuesto.tenant_id == tenant_id).all() if c
    }
    creados: list[EsquemaImpuesto] = []
    for spec in ESQUEMAS_IMPUESTO_DEFAULT:
        if spec["codigo"] in existentes:
            continue
        obj = EsquemaImpuesto(
            tenant_id=tenant_id,
            codigo=spec["codigo"],
            nombre=spec["nombre"],
            descripcion=spec["descripcion"],
            iva_tasa=Decimal(spec["iva"]),
            ieps_tasa=Decimal(spec.get("ieps", "0")),
            tipo_ieps=spec.get("tipo_ieps", "TASA"),
            ieps_cuota=Decimal(spec.get("ieps_cuota", "0")),
            iva_exento=bool(spec.get("exento", False)),
            retencion_iva_tasa=Decimal(spec.get("ret_iva", "0")),
            retencion_isr_tasa=Decimal(spec.get("ret_isr", "0")),
            activo=True,
        )
        db.add(obj)
        creados.append(obj)
    return creados


# Las categorías con las que arranca una empresa nueva: los departamentos que
# un distribuidor de alimentos usa desde el día 1. La lista sale de lo que el
# negocio real tiene en producción (ABARROTE, FRUTA Y VERDURA, LACTEOS,
# PAN Y TORTILLA, PROTEINA ANIMAL, CONGELADOS, PLASTICOS, PRODUCTO DE
# LIMPIEZA, SECOS), no de una lluvia de ideas.
#
# Los NOMBRES coinciden a propósito con los del "Catálogo sugerido" de la
# pantalla de Categorías (frontend): ese diálogo esconde lo que ya existe
# comparando el nombre en minúsculas, así que si aquí se llamaran distinto
# volvería a ofrecer lo mismo con otro nombre y el catálogo se duplicaría.
#
# Es un ARRANQUE, no un corsé: se editan, se borran y el catálogo sugerido
# sigue ofreciendo el resto (enlatados, granos, especias, aceites, papelería).
CATEGORIAS_DEFAULT: list[tuple[str, str]] = [
    ("Frutas", "Fruta fresca de temporada"),
    ("Verduras", "Verdura y hortaliza fresca"),
    ("Abarrotes", "Despensa y productos secos"),
    ("Lácteos", "Leche, queso, crema y yogurt"),
    ("Carnes", "Res, cerdo, pollo y pavo"),
    ("Embutidos", "Jamón, salchicha, tocino y salami"),
    ("Pescados y mariscos", "Producto del mar fresco y congelado"),
    ("Panadería", "Pan, tortilla y repostería"),
    ("Bebidas", "Agua, refrescos y jugos"),
    ("Botanas y dulces", "Frituras, galletas dulces y confitería"),
    ("Congelados", "Producto que requiere cadena de frío"),
    ("Limpieza", "Productos de limpieza e higiene"),
    ("Desechables", "Platos, vasos, cubiertos y servilletas"),
]


def sembrar_categorias(db: Session, tenant_id) -> list[CategoriaProducto]:
    """Crea las categorías que falten para el tenant, por nombre.

    Idempotente: no duplica ni pisa lo que el usuario ya tenga. El código lo
    deriva el servidor del nombre (regla de `categoria_codigo.py`), nunca se
    escribe a mano. No hace commit — lo hace el llamador.
    """
    from .categoria_codigo import generate_unique_codigo

    existentes = {
        (n or "").strip().lower()
        for (n,) in db.query(CategoriaProducto.nombre)
        .filter(
            CategoriaProducto.tenant_id == tenant_id,
            CategoriaProducto.deleted_at.is_(None),
        )
        .all()
    }
    creadas: list[CategoriaProducto] = []
    for nombre, descripcion in CATEGORIAS_DEFAULT:
        if nombre.strip().lower() in existentes:
            continue
        obj = CategoriaProducto(
            tenant_id=tenant_id,
            codigo=generate_unique_codigo(db, tenant_id, nombre),
            nombre=nombre,
            descripcion=descripcion,
            activo=True,
        )
        db.add(obj)
        db.flush()          # el siguiente código se calcula contra éste
        creadas.append(obj)
    return creadas


# La categoría a la que caen los productos que se dan de alta sin elegir una.
# Existe como categoría REAL y no como un hueco en blanco para que se puedan
# listar, contar y repartir después desde la pantalla de Categorías.
CATEGORIA_SIN_CATEGORIZAR = "Sin categorizar"


def categoria_sin_categorizar(db: Session, tenant_id) -> CategoriaProducto:
    """La categoría por defecto del tenant; la crea si aún no existe.

    Se busca por NOMBRE y no por código: el código lo deriva el servidor del
    nombre (`categoria_codigo.py`) y podría llevar sufijo anticolisión. No hace
    commit — lo hace el llamador dentro de su propia transacción.
    """
    from .categoria_codigo import generate_unique_codigo

    obj = (
        db.query(CategoriaProducto)
        .filter(
            CategoriaProducto.tenant_id == tenant_id,
            CategoriaProducto.nombre == CATEGORIA_SIN_CATEGORIZAR,
            CategoriaProducto.deleted_at.is_(None),
        )
        .first()
    )
    if obj is not None:
        return obj
    obj = CategoriaProducto(
        tenant_id=tenant_id,
        codigo=generate_unique_codigo(db, tenant_id, CATEGORIA_SIN_CATEGORIZAR),
        nombre=CATEGORIA_SIN_CATEGORIZAR,
        descripcion="Productos que aún no se clasifican. Es la categoría por defecto del sistema.",
        activo=True,
    )
    db.add(obj)
    db.flush()
    return obj
