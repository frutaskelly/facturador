"""POS — motor del pipeline configurable por tenant.

El flujo NO está en código: cada tenant define en `tenants.config.pos`
(Ajustes › Punto de venta) QUÉ etapas usa, EN QUÉ ORDEN, y puede agregar
etapas PROPIAS ("Empaque", "Verificación"…). Este módulo deriva la máquina de
estados de esa configuración; cambiar el flujo de un cliente jamás toca código.

Convenciones:
- `etapas` es la lista ORDENADA del flujo real; "pedido" siempre va primero.
- Las 4 etapas canónicas conservan su semántica (caja = cobro en Fase 2,
  almacén = surtido, salida = entrega). Las etapas custom son checkpoints puros
  (estampan quién/cuándo, sin side-effects) y declaran qué rol las trabaja vía
  `permiso` (uno de los 4 permisos de acción del seed 0003 — sin RBAC nuevo).
- El inventario sale al COMPLETAR la etapa `inventario_sale_en` (id de etapa
  del flujo, o "crear"); si esa etapa no está en el flujo, sale al cerrar la
  última etapa. Valores legados (cobro/surtido/entrega) se traducen.
"""
from __future__ import annotations

import re
from typing import Optional

ETAPAS_CANONICAS = ["pedido", "caja", "almacen", "salida"]
COMPLETADO = "completado"
SALE_AL_CREAR = "crear"

_LEGACY_SALE_EN = {"cobro": "caja", "surtido": "almacen", "entrega": "salida"}
_SLUG_RE = re.compile(r"^[a-z0-9_]{2,30}$")

ETIQUETA_CANONICA = {"pedido": "Pedido", "caja": "Caja", "almacen": "Almacén", "salida": "Salida"}

# Permiso de ACCIÓN por etapa canónica (seed 0003; OWNER bypassa).
_ACCION_CANONICA = {
    "pedido": "pedido:capturar",
    "caja": "pedido:cobrar",
    "almacen": "pedido:surtir",
    "salida": "pedido:entregar",
}
ACCIONES_VALIDAS = set(_ACCION_CANONICA.values())
# Permiso de MENÚ (ver la cola) correspondiente a cada permiso de acción.
_MENU_DE_ACCION = {
    "pedido:capturar": "menu:pos.pedido",
    "pedido:cobrar": "menu:pos.caja",
    "pedido:surtir": "menu:pos.almacen",
    "pedido:entregar": "menu:pos.salida",
}

DEFAULT_CONFIG = {
    "activo": False,
    "etapas": ["pedido", "caja", "almacen", "salida"],
    "etapas_custom": [],               # [{id, nombre, permiso}]
    "credito": False,
    "inventario_sale_en": "almacen",   # id de etapa del flujo | "crear"
    "serie_id": None,
    "permitir_sobregiro": False,
    "ticket": {"formato": "80mm", "auto_imprimir": False},
}


def _customs(cfg: dict) -> dict[str, dict]:
    return {
        c["id"]: c
        for c in (cfg.get("etapas_custom") or [])
        if isinstance(c, dict) and c.get("id")
    }


def pos_config(tenant) -> dict:
    """Config del POS del tenant: defaults + lo guardado, normalizado."""
    guardado = ((tenant.config or {}).get("pos") or {}) if tenant is not None else {}
    cfg = {**DEFAULT_CONFIG, **guardado}
    cfg["ticket"] = {**DEFAULT_CONFIG["ticket"], **(guardado.get("ticket") or {})}
    cfg["etapas"] = etapas_flujo(cfg)
    # Back-compat: los valores viejos (cobro/surtido/entrega) se traducen a etapa.
    sale = cfg.get("inventario_sale_en") or "almacen"
    cfg["inventario_sale_en"] = _LEGACY_SALE_EN.get(sale, sale)
    return cfg


def etapas_flujo(cfg: dict) -> list[str]:
    """El flujo real: la lista configurada, deduplicada, con 'pedido' SIEMPRE al
    frente y sin etapas desconocidas (ni canónicas ni custom declaradas)."""
    customs = _customs(cfg)
    vistas: set[str] = set()
    flujo: list[str] = []
    for e in cfg.get("etapas") or []:
        if e in vistas or e == "pedido":
            continue
        if e in ETAPAS_CANONICAS or e in customs:
            vistas.add(e)
            flujo.append(e)
    return ["pedido"] + flujo


def etiqueta(cfg: dict, etapa: str) -> str:
    if etapa in ETIQUETA_CANONICA:
        return ETIQUETA_CANONICA[etapa]
    if etapa == COMPLETADO:
        return "Completado"
    c = _customs(cfg).get(etapa)
    return (c or {}).get("nombre") or etapa


def permiso_accion(cfg: dict, etapa: str) -> str:
    """Permiso para COMPLETAR una etapa. Custom: el declarado en su config
    (default pedido:surtir — estación operativa)."""
    if etapa in _ACCION_CANONICA:
        return _ACCION_CANONICA[etapa]
    c = _customs(cfg).get(etapa) or {}
    p = c.get("permiso") or "pedido:surtir"
    return p if p in ACCIONES_VALIDAS else "pedido:surtir"


def permiso_menu(cfg: dict, etapa: str) -> str:
    """Permiso para VER la cola de una etapa (derivado de su permiso de acción)."""
    return _MENU_DE_ACCION[permiso_accion(cfg, etapa)]


def primera_cola(cfg: dict) -> str:
    flujo = etapas_flujo(cfg)
    return flujo[1] if len(flujo) > 1 else COMPLETADO


def siguiente_etapa(cfg: dict, actual: str) -> str:
    flujo = etapas_flujo(cfg)
    if actual not in flujo:
        raise ValueError(f"Etapa '{actual}' no está en el flujo")
    i = flujo.index(actual)
    return flujo[i + 1] if i + 1 < len(flujo) else COMPLETADO


def etapa_salida_inventario(cfg: dict) -> Optional[str]:
    """Etapa cuyo COMPLETAR dispara la salida de inventario.

    - `inventario_sale_en == "crear"` → SALE_AL_CREAR (al iniciar el pedido).
    - Si la etapa configurada está en el flujo → esa.
    - Si no está (o el flujo cambió) → la última etapa del flujo (el cierre).
    - Flujo de solo 'pedido' → None (el llamador saca al crear).
    """
    if (cfg.get("inventario_sale_en") or "") == SALE_AL_CREAR:
        return SALE_AL_CREAR
    flujo = etapas_flujo(cfg)
    colas = [e for e in flujo if e != "pedido"]
    if not colas:
        return None
    objetivo = cfg.get("inventario_sale_en")
    return objetivo if objetivo in colas else colas[-1]


def validar_config(cfg: dict) -> Optional[str]:
    """Mensaje de error si la config es inválida; None si está bien."""
    customs = cfg.get("etapas_custom") or []
    ids_custom = set()
    for c in customs:
        cid = (c.get("id") or "").strip()
        nombre = (c.get("nombre") or "").strip()
        if not _SLUG_RE.fullmatch(cid):
            return f"Id de etapa inválido: '{cid}' (minúsculas/números/_, 2-30 caracteres)"
        if cid in ETAPAS_CANONICAS or cid in (COMPLETADO, SALE_AL_CREAR):
            return f"El id '{cid}' está reservado"
        if cid in ids_custom:
            return f"Id de etapa duplicado: '{cid}'"
        if not nombre or len(nombre) > 40:
            return f"La etapa '{cid}' necesita nombre (máx. 40 caracteres)"
        if c.get("permiso") and c["permiso"] not in ACCIONES_VALIDAS:
            return f"Permiso desconocido para la etapa '{cid}'"
        ids_custom.add(cid)

    conocidas = set(ETAPAS_CANONICAS) | ids_custom
    invalidas = [e for e in (cfg.get("etapas") or []) if e not in conocidas]
    if invalidas:
        return f"Etapas desconocidas en el flujo: {', '.join(invalidas)}"
    if len(cfg.get("etapas") or []) > 8:
        return "Máximo 8 etapas en el flujo"

    sale = cfg.get("inventario_sale_en") or "almacen"
    sale = _LEGACY_SALE_EN.get(sale, sale)
    if sale != SALE_AL_CREAR and sale not in conocidas:
        return "inventario_sale_en debe ser una etapa del flujo o 'crear'"
    formato = ((cfg.get("ticket") or {}).get("formato") or "80mm")
    if formato not in ("80mm", "carta"):
        return "ticket.formato debe ser 80mm o carta"
    return None
