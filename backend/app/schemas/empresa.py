"""Schemas para los datos fiscales del emisor (tenant) y sus CSD."""
from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field


class EmpresaOut(BaseModel):
    legal_name: str = ""
    rfc: str = ""
    regimen_fiscal_sat: str = ""
    domicilio_fiscal_cp: str = ""
    domicilio_fiscal: Dict[str, Any] = Field(default_factory=dict)
    has_logo: bool = False


class EmpresaUpdate(BaseModel):
    legal_name: str = Field(max_length=254)
    rfc: str = Field(max_length=15)
    regimen_fiscal_sat: str = Field(max_length=4)
    domicilio_fiscal_cp: str = Field(max_length=5)
    domicilio_fiscal: Dict[str, Any] = Field(default_factory=dict)


class EmpresaHijaIn(BaseModel):
    """Alta de una empresa HIJA del grupo (otro RFC/razón social del mismo dueño)."""
    legal_name: str = Field(min_length=2, max_length=254)
    rfc: str = Field(min_length=12, max_length=15)
    regimen_fiscal_sat: str = Field(min_length=3, max_length=4)
    domicilio_fiscal_cp: str = Field(min_length=5, max_length=5)


class EmpresaHijaOut(BaseModel):
    tenant_id: str
    slug: str
    legal_name: str
    rfc: str


class CsdOut(BaseModel):
    """Pass-through del objeto que devuelve Facturama por cada CSD cargado."""

    model_config = {"extra": "allow"}


class OnboardingPaso(BaseModel):
    id: str
    titulo: str
    completo: bool
    detalle: str = ""


class EmpresaOnboardingOut(BaseModel):
    """Estado de la configuración fiscal del emisor (wizard de onboarding)."""

    datos_fiscales_completos: bool
    rfc: str = ""
    csd_cargado: bool
    csd: Dict[str, Any] | None = None
    multiemisor: bool
    listo_para_facturar: bool
    pasos: list[OnboardingPaso] = Field(default_factory=list)
    # Ambiente real del PAC ("sandbox" | "producción"), lo decide FACTURAMA_BASE_URL.
    ambiente: str = "sandbox"


class EmpresaGrupoItem(BaseModel):
    """Una empresa del usuario, con lo que le falta para poder facturar."""

    tenant_id: str
    slug: str
    legal_name: str = ""
    trade_name: str = ""
    rfc: str = ""
    regimen_fiscal_sat: str = ""
    domicilio_fiscal_cp: str = ""
    domicilio_fiscal: Dict[str, Any] = Field(default_factory=dict)
    # Color con el que se reconoce la empresa. None = automático (el front lo
    # deriva del id): así una cuenta nueva ya viene con colores distintos sin
    # que nadie los elija.
    color: str | None = None
    es_principal: bool = False
    es_actual: bool = False
    # ¿Pertenece al grupo de la empresa activa? (un usuario puede estar invitado
    # a empresas de otro dueño; esas se listan pero no cuentan para el tope).
    en_grupo: bool = True
    rol: str = ""
    # ¿Puede editar sus datos fiscales desde aquí, sin cambiarse a ella?
    puede_editar: bool = False
    # Estado de configuración — los chips de la tarjeta.
    datos_fiscales: bool = False
    csd: bool = False
    logo: bool = False
    series: bool = False
    correo: bool = False
    listo_para_facturar: bool = False


class EmpresaGrupoOut(BaseModel):
    empresas: list[EmpresaGrupoItem] = Field(default_factory=list)
    grupo_total: int = 0
    grupo_max: int = 0
    puede_agregar: bool = False


class EmpresaColorIn(BaseModel):
    """`null` devuelve la empresa al color automático."""

    color: str | None = None


class EmpresaColorOut(BaseModel):
    color: str | None = None
