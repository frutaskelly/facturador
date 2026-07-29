"""Schemas para la configuración de correo SMTP del tenant."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class CorreoConfigIn(BaseModel):
    host: str = Field(max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(max_length=255)
    password: Optional[str] = Field(default=None, max_length=255)
    from_email: str = Field(max_length=255)
    from_name: Optional[str] = Field(default=None, max_length=255)
    use_ssl: bool = False


class CorreoConfigOut(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    from_email: str = ""
    from_name: Optional[str] = None
    use_ssl: bool = False
    configured: bool = False
    has_password: bool = False


class CorreoProbarIn(BaseModel):
    to: EmailStr = Field(max_length=255)
