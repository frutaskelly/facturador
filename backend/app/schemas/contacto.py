"""Schema del formulario de contacto público (landing facturador.mx)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ContactoIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    # Correo y teléfono son opcionales por separado, pero el router exige AL MENOS
    # uno: hay landings (smartsupply) con un solo campo "teléfono o correo".
    correo: Optional[str] = Field(default=None, max_length=254)
    empresa: Optional[str] = Field(default=None, max_length=160)
    telefono: Optional[str] = Field(default=None, max_length=40)
    mensaje: str = Field(min_length=5, max_length=4000)
    # Honeypot anti-bot: campo oculto en el form; si llega con valor, es un bot.
    website: Optional[str] = Field(default=None, max_length=254)
    # Token de Cloudflare Turnstile (captcha); requerido solo si el server lo exige.
    turnstile_token: Optional[str] = Field(default=None, max_length=4096)


class ContactoOut(BaseModel):
    ok: bool = True
