"""Rate limiting con Redis (fixed-window) + IP real del cliente.

Compartido entre workers vía Redis. Si Redis no está disponible, FALLA ABIERTO
(no bloquea a usuarios legítimos) y lo registra — el rate limit es una capa de
defensa, no la única (ver también el kill-switch SIGNUP_ENABLED).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from .config import settings

log = logging.getLogger(__name__)

_redis = None
_redis_init = False


def _client():
    global _redis, _redis_init
    if _redis_init:
        return _redis
    _redis_init = True
    try:
        import redis  # redis==5.x ya está en requirements

        _redis = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
        )
        _redis.ping()
    except Exception as exc:  # noqa: BLE001 — fail-open intencional
        log.warning("Rate limit sin Redis (fail-open): %s", exc)
        _redis = None
    return _redis


def hit(key: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Registra un acceso en la ventana. Devuelve (permitido, retry_after_s).

    Fixed-window: INCR + EXPIRE al primer hit. Fail-open si Redis no responde.
    """
    r = _client()
    if r is None:
        return True, 0
    try:
        full = f"rl:{key}"
        n = r.incr(full)
        if n == 1:
            r.expire(full, window_s)
        if n > limit:
            ttl = r.ttl(full)
            return False, max(1, ttl if isinstance(ttl, int) and ttl > 0 else window_s)
        return True, 0
    except Exception as exc:  # noqa: BLE001 — fail-open intencional
        log.warning("Rate limit error (fail-open): %s", exc)
        return True, 0


def enforce(key: str, limit: int, window_s: int, *, detail: str | None = None) -> None:
    """`hit()` + 429 con Retry-After si se excede. Para endpoints que cuestan
    dinero o recursos externos (correo real, IA, folios del PAC): límites
    generosos que el uso legítimo no alcanza, pero que cortan loops/abuso."""
    ok, retry = hit(key, limit, window_s)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=detail or "Demasiadas solicitudes; intenta de nuevo en unos minutos",
            headers={"Retry-After": str(retry)},
        )


def client_ip(request: Request) -> str:
    """IP real del cliente. Detrás de Cloudflare/tunnel viene en CF-Connecting-IP;
    si no, la conexión directa. X-Forwarded-For NO se usa: cualquiera que alcance
    el origen sin pasar por Cloudflare (LAN, puerto expuesto) puede falsificarlo
    por request y evadir el rate limit."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    return request.client.host if request.client else "unknown"
