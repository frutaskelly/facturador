"""Backfill de impuestos en remisiones históricas (decisión 2026-07-29).

Recalcula iva_importe/ieps_importe por línea y los totales del encabezado de
TODAS las remisiones, usando services/fiscal.calcular_linea_producto — el mismo
cerebro que el alta/edición y las facturas (nada de replicar la lógica en SQL).
Idempotente: correrlo dos veces produce lo mismo.

Uso:
    DATABASE_URL=... python -m scripts.backfill_remisiones_impuestos
"""
from decimal import Decimal

from app.core.db import SessionLocal
from app.models import EsquemaImpuesto, LineaRemision, Producto, Remision
from app.services.fiscal import calcular_linea_producto

ZERO = Decimal("0")


def main() -> None:
    db = SessionLocal()
    try:
        productos = {p.id: p for p in db.query(Producto).all()}
        esquemas = {e.id: e for e in db.query(EsquemaImpuesto).all()}
        rems = db.query(Remision).all()
        cambiadas = 0
        for rem in rems:
            lineas = db.query(LineaRemision).filter(LineaRemision.remision_id == rem.id).all()
            iva_total = ZERO
            ieps_total = ZERO
            for ln in lineas:
                prod = productos.get(ln.producto_id)
                esq = esquemas.get(prod.esquema_impuesto_id) if prod and prod.esquema_impuesto_id else None
                calc = calcular_linea_producto(
                    prod, esq, Decimal(ln.importe or 0), Decimal(ln.cantidad_solicitada or 0)
                )
                ln.iva_importe = calc["iva_importe"]
                ln.ieps_importe = calc["ieps_importe"]
                iva_total += calc["iva_importe"]
                ieps_total += calc["ieps_importe"]
            antes = rem.total
            rem.iva = iva_total
            rem.ieps = ieps_total
            rem.total = (rem.subtotal or ZERO) - (rem.descuento or ZERO) + iva_total + ieps_total
            if antes != rem.total or iva_total > 0 or ieps_total > 0:
                cambiadas += 1
        db.commit()
        print(f"{len(rems)} remisiones procesadas; {cambiadas} con impuestos/total actualizado")
    finally:
        db.close()


if __name__ == "__main__":
    main()
