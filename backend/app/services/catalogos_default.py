"""Catálogos que toda empresa nueva necesita para poder trabajar desde el día 1.

Se siembran al dar de alta el tenant (registro autoservicio y empresa hija del
grupo): sin esquemas de impuesto no se puede dar de alta un producto con sus
tasas, y arrancar con la pantalla vacía obliga a teclear lo mismo siempre.

Todo lo sembrado es 100% EDITABLE por el usuario (nombre, tasas, descripción).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import EsquemaImpuesto

# Los 8 esquemas de Aspel SAE (tabla IMPU02), con el código que usa SAE.
#
# OJO con las TASAS: los nombres 4/5/7/8 dicen "+ N% IEPS", pero hoy ningún
# esquema cobra IEPS (verificado en la base de SAE: IMPUESTO1..8 = 0; el único
# con tasa es el IVA). Se siembran con la tasa REAL —IEPS 0— y el nombre tal
# cual lo usa SAE, para que el usuario los reconozca. Si algún día reactiva el
# IEPS, solo edita la tasa del esquema correspondiente.
ESQUEMAS_IMPUESTO_DEFAULT: list[dict] = [
    {"codigo": "1", "nombre": "16% IVA", "iva": "0.16",
     "descripcion": "No alimentos (limpieza, desechables), alimento para mascota, agua mineral/gaseosa"},
    {"codigo": "2", "nombre": "0% IVA", "iva": "0",
     "descripcion": "Alimentos — frutas, verduras, carne, lácteos, pan, abarrotes"},
    {"codigo": "3", "nombre": "IVA exento", "iva": "0", "exento": True,
     "descripcion": "Evitarlo en alimentos (para alimentos va el 2)"},
    {"codigo": "4", "nombre": "16% IVA + 8% IEPS", "iva": "0.16",
     "descripcion": "Refrescos y jugos"},
    {"codigo": "5", "nombre": "16% IVA + 25% IEPS", "iva": "0.16",
     "descripcion": "Etiqueta con tasa desactualizada"},
    {"codigo": "6", "nombre": "16% IVA", "iva": "0.16",
     "descripcion": "Variante"},
    {"codigo": "7", "nombre": "0% IVA + 8% IEPS", "iva": "0",
     "descripcion": "Dulces, chocolate, galletas dulces, botanas, granola"},
    {"codigo": "8", "nombre": "16% IVA + 26.5% IEPS", "iva": "0.16",
     "descripcion": "Vino de mesa hasta 14 grados"},
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
            ieps_tasa=Decimal("0"),
            iva_exento=bool(spec.get("exento", False)),
            activo=True,
        )
        db.add(obj)
        creados.append(obj)
    return creados
