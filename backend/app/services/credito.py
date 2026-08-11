"""Crédito de clientes — validación de límite y movimientos de saldo.

`saldo_actual` = lo que el cliente DEBE. Un cargo a crédito (venta fiada) lo
sube; un abono lo baja. El límite se valida contra saldo + nuevo cargo.
Los acumuladores (`ventas_ytd`, `ultima_venta_at`, `ultimo_pago_at`) se
actualizan aquí — la infra existía dormida desde el modelo inicial.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func

ZERO = Decimal("0")


def validar_credito_disponible(cliente, monto: Decimal) -> None:
    """422 si cargar `monto` a crédito excede el límite del cliente.

    Límite 0 = sin crédito autorizado (solo contado) — regla acordada: los
    clientes existentes arrancan en 0 hasta que se les asigne un límite.
    """
    monto = Decimal(monto)
    if monto <= ZERO:
        return
    limite = Decimal(cliente.limite_credito or 0)
    saldo = Decimal(cliente.saldo_actual or 0)
    if limite <= ZERO:
        raise HTTPException(
            status_code=422,
            detail=f"{cliente.legal_name} no tiene crédito autorizado (asigna un límite en Clientes)",
        )
    disponible = limite - saldo
    if monto > disponible:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Crédito insuficiente para {cliente.legal_name}: disponible "
                f"${disponible:,.2f} de ${limite:,.2f} (saldo ${saldo:,.2f})"
            ),
        )


def aplicar_cargo_credito(cliente, monto: Decimal) -> None:
    """Venta fiada: sube el saldo del cliente."""
    cliente.saldo_actual = Decimal(cliente.saldo_actual or 0) + Decimal(monto)


def aplicar_pago(cliente, monto: Decimal) -> None:
    """Abono a cuenta: baja el saldo (nunca por debajo de 0) y estampa fecha."""
    nuevo = Decimal(cliente.saldo_actual or 0) - Decimal(monto)
    cliente.saldo_actual = nuevo if nuevo > ZERO else ZERO
    cliente.ultimo_pago_at = func.now()


def registrar_venta(cliente, total: Decimal) -> None:
    """Acumuladores de venta (contado o crédito): ventas del año + última venta."""
    cliente.ventas_ytd = Decimal(cliente.ventas_ytd or 0) + Decimal(total)
    cliente.ultima_venta_at = func.now()
