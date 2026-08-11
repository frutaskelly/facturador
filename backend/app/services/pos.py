"""POS — motor del pipeline configurable por tenant (Fase 0).

El flujo NO está en código: cada tenant prende/apaga etapas en
`tenants.config.pos` (Ajustes › Punto de venta) y este módulo deriva la máquina
de estados de esa configuración. Agregar o quitar etapas para un cliente jamás
toca código.

Convenciones:
- `pos_etapa` de la remisión = estación donde el pedido ESPERA.
- El orden de etapas es fijo (pedido → caja → almacén → salida); lo configurable
  es CUÁLES están activas ("pedido" siempre). Orden libre: solo si un cliente
  real lo pide (decisión #1 del plan).
- El inventario sale al COMPLETAR la etapa `inventario_sale_en` (mapeada a la
  primera etapa activa igual-o-posterior; si no hay, a la última activa) — así
  un mostrador de 2 etapas y una bodega de 4 usan el mismo motor.
"""
from __future__ import annotations

from typing import Optional

ETAPAS_ORDEN = ["pedido", "caja", "almacen", "salida"]
COMPLETADO = "completado"

# A qué etapa corresponde cada momento de salida de inventario.
_SALE_EN_ETAPA = {"cobro": "caja", "surtido": "almacen", "entrega": "salida"}

# Permiso de ACCIÓN para completar cada etapa (seed 0003; OWNER bypassa).
PERMISO_ACCION = {
    "pedido": "pedido:capturar",
    "caja": "pedido:cobrar",
    "almacen": "pedido:surtir",
    "salida": "pedido:entregar",
}
# Permiso de MENÚ para ver la cola de cada etapa.
PERMISO_MENU = {e: f"menu:pos.{e}" for e in ETAPAS_ORDEN}

DEFAULT_CONFIG = {
    "activo": False,
    "etapas": ["pedido", "caja", "almacen", "salida"],
    "credito": False,
    "inventario_sale_en": "surtido",   # "cobro" | "surtido" | "entrega"
    "serie_id": None,
    "permitir_sobregiro": False,
    "ticket": {"formato": "80mm", "auto_imprimir": False},
}


def pos_config(tenant) -> dict:
    """Config del POS del tenant: defaults + lo guardado en `config.pos`."""
    guardado = ((tenant.config or {}).get("pos") or {}) if tenant is not None else {}
    cfg = {**DEFAULT_CONFIG, **guardado}
    cfg["ticket"] = {**DEFAULT_CONFIG["ticket"], **(guardado.get("ticket") or {})}
    cfg["etapas"] = etapas_activas(cfg)
    return cfg


def etapas_activas(cfg: dict) -> list[str]:
    """Etapas prendidas, en el orden canónico. 'pedido' siempre presente."""
    pedidas = set(cfg.get("etapas") or [])
    pedidas.add("pedido")
    return [e for e in ETAPAS_ORDEN if e in pedidas]


def primera_cola(cfg: dict) -> str:
    """Etapa donde cae un pedido recién creado (la primera activa después de
    'pedido'); si el flujo es solo captura, nace COMPLETADO."""
    activas = etapas_activas(cfg)
    return activas[1] if len(activas) > 1 else COMPLETADO


def siguiente_etapa(cfg: dict, actual: str) -> str:
    """Etapa a la que pasa el pedido al COMPLETAR `actual`."""
    activas = etapas_activas(cfg)
    if actual not in activas:
        raise ValueError(f"Etapa '{actual}' no está activa en el flujo")
    i = activas.index(actual)
    return activas[i + 1] if i + 1 < len(activas) else COMPLETADO


def etapa_salida_inventario(cfg: dict) -> Optional[str]:
    """Etapa cuyo COMPLETAR dispara la salida de inventario.

    Regla: la etapa mapeada de `inventario_sale_en` si está activa; si no, la
    primera activa POSTERIOR a ella; si tampoco hay, la última etapa activa
    (el cierre del flujo). None solo si el flujo es únicamente 'pedido'
    (mostrador exprés: sale al crear — lo maneja el llamador).
    """
    activas = etapas_activas(cfg)
    colas = [e for e in activas if e != "pedido"]
    if not colas:
        return None
    objetivo = _SALE_EN_ETAPA.get(cfg.get("inventario_sale_en") or "surtido", "almacen")
    idx = ETAPAS_ORDEN.index(objetivo)
    for e in ETAPAS_ORDEN[idx:]:
        if e in colas:
            return e
    return colas[-1]


def validar_config(cfg: dict) -> Optional[str]:
    """Mensaje de error si la config es inválida; None si está bien."""
    etapas = cfg.get("etapas") or []
    invalidas = [e for e in etapas if e not in ETAPAS_ORDEN]
    if invalidas:
        return f"Etapas desconocidas: {', '.join(invalidas)}"
    if cfg.get("inventario_sale_en") not in _SALE_EN_ETAPA:
        return "inventario_sale_en debe ser cobro, surtido o entrega"
    formato = ((cfg.get("ticket") or {}).get("formato") or "80mm")
    if formato not in ("80mm", "carta"):
        return "ticket.formato debe ser 80mm o carta"
    return None
