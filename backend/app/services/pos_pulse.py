"""POS realtime — "pulso" por tenant vía Redis (fail-open).

Cada cambio del POS (iniciar/avanzar/cobrar) incrementa un contador por tenant;
las estaciones consultan `GET /pos/pulse` (barato) cada pocos segundos y solo
recargan su cola cuando el contador cambió — casi-tiempo-real sin WebSocket,
robusto a través del túnel de Cloudflare. Sin Redis, `read` devuelve 0 y las
estaciones caen a su recarga periódica (degradación elegante).
"""
from __future__ import annotations

import logging

from ..core.config import settings

log = logging.getLogger(__name__)

_redis = None
_redis_init = False


def _client():
    global _redis, _redis_init
    if _redis_init:
        return _redis
    _redis_init = True
    try:
        import redis

        _redis = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
        )
        _redis.ping()
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("POS pulse sin Redis (fail-open): %s", exc)
        _redis = None
    return _redis


def bump(tenant_id) -> None:
    """Marca que hubo un cambio en el POS de este tenant."""
    r = _client()
    if r is None:
        return
    try:
        r.incr(f"pos:pulse:{tenant_id}")
    except Exception as exc:  # noqa: BLE001 — nunca romper la transacción por el pulso
        log.warning("POS pulse bump falló: %s", exc)


def read(tenant_id) -> int:
    """Valor actual del pulso (0 si no hay Redis o aún no hay cambios)."""
    r = _client()
    if r is None:
        return 0
    try:
        v = r.get(f"pos:pulse:{tenant_id}")
        return int(v) if v is not None else 0
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("POS pulse read falló: %s", exc)
        return 0
