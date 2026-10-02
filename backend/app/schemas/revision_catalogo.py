from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ClaveInfo(BaseModel):
    clave: str
    en_sae: list[str]            # empresas de SAE donde está activa
    facturas: int
    formato_viejo: bool
    de_sku: Optional[str] = None


class UnidadPropuesta(ClaveInfo):
    unidad: str
    alternativas: list[ClaveInfo]


class Propuesta(BaseModel):
    queda_sku: str
    nombre_final: str
    unidad_base: str
    unidades: list[UnidadPropuesta]
    quitar: list[str]
    se_unen: list[str]
    alertas: list[str]
    # Lo que impide aprobar (p. ej. borradores en una unidad que se quita).
    bloqueos: list[str]


class UnidadMiembro(BaseModel):
    unidad: str
    clave: str
    en_sae: bool
    borradores: int


class MiembroRevision(BaseModel):
    id: str
    sku: str
    nombre: str
    unidad_base: str
    unidades: list[UnidadMiembro]
    categoria: str
    esquema: str
    clave_sat: str
    alta: Optional[date] = None
    ventas: int
    precios: int
    alias: int
    catalogo: int


class DecisionOut(BaseModel):
    estado: Literal["APROBADO", "RECHAZADO", "APLICADO"]
    nota: Optional[str] = None
    por: Optional[str] = None
    at: Optional[datetime] = None
    # El grupo ya no es el que se decidió (cambió quién lo forma o sus unidades).
    desactualizada: bool = False


class GrupoRevision(BaseModel):
    clave: str
    tipo: Literal["GEMELOS", "UNIDADES", "EMPAQUE"]
    raiz: str
    estado: Literal["PENDIENTE", "APROBADO", "RECHAZADO", "APLICADO"]
    decision: Optional[DecisionOut] = None
    propuesta: Propuesta
    miembros: list[MiembroRevision]
    ventas: int


class ResumenRevision(BaseModel):
    grupos: int
    productos: int
    por_estado: dict[str, int]
    por_tipo: dict[str, int]
    aplicados: int
    # Productos con clave de formato viejo (Balles y Jubran): fuera de la revisión.
    exclusivos: int = 0


class RevisionOut(BaseModel):
    grupos: list[GrupoRevision]
    resumen: ResumenRevision


class AjusteIn(BaseModel):
    """Lo que la persona cambió de la propuesta automática. Todo opcional."""
    grupo: str = Field(min_length=3, max_length=300)
    queda_sku: Optional[str] = Field(default=None, max_length=50)
    nombre_final: Optional[str] = Field(default=None, max_length=254)
    claves: Optional[dict[str, str]] = None
    quitar: Optional[list[str]] = None


class DecisionIn(AjusteIn):
    estado: Literal["APROBADO", "RECHAZADO", "PENDIENTE"]
    nota: Optional[str] = Field(default=None, max_length=2000)


class AplicarIn(BaseModel):
    """Qué grupos aprobados unir. Sin lista = todos los aprobados vigentes."""
    grupos: Optional[list[str]] = Field(default=None, max_length=500)


class GrupoAplicado(BaseModel):
    grupo: str
    nombre: str
    resumen: dict


class GrupoOmitido(BaseModel):
    grupo: str
    nombre: str
    motivo: str


class AplicarOut(BaseModel):
    aplicados: list[GrupoAplicado]
    omitidos: list[GrupoOmitido]
