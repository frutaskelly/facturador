"""El catálogo de claves de SAE (espejo de INVE##): su depósito y su búsqueda."""
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ClaveSaeItem(BaseModel):
    clave: str = Field(min_length=1, max_length=50)
    descripcion: Optional[str] = Field(default=None, max_length=254)
    # STATUS de SAE: 'A' activa, cualquier otra cosa = baja.
    activa: bool = True


class ClavesSaeIn(BaseModel):
    empresa: str = Field(min_length=1, max_length=4)
    claves: List[ClaveSaeItem] = Field(min_length=1, max_length=50000)
    # El depósito REEMPLAZA el catálogo de esa empresa. Si el conector alcanzó a
    # leer solo una parte de INVE## (timeout a media consulta), reemplazar
    # convertiría claves buenas en "no existe en SAE" y trabaría exports
    # válidos. Por eso un encogimiento brusco se rechaza salvo que el operador
    # lo confirme — un catálogo que se parte a la mitad es una lectura mala,
    # no un inventario que se vació.
    forzar: bool = False


class ClavesSaeResult(BaseModel):
    empresa: str
    recibidas: int
    creadas: int
    actualizadas: int
    eliminadas: int
    total: int


class ClaveSaeEnEmpresa(BaseModel):
    """Cómo está una clave en UNA empresa de SAE, según el espejo."""
    activa: bool
    descripcion: Optional[str] = None


class ClaveSaeBuscadaOut(BaseModel):
    """Una clave de SAE con todas las empresas donde existe y el producto del
    Facturador que la lleva (si alguno).

    Es la respuesta de `GET /productos/claves-sae`, la búsqueda con la que el
    bot decide si algo se da de ALTA (no existe en ninguna) o se CAMBIA (existe,
    y entonces `empresas` son justo las que hay que mandar en el cambio).
    """
    clave: str
    # La de la primera empresa donde está activa; si no hay ninguna activa, la
    # primera que tenga descripción.
    descripcion: Optional[str] = None
    # Sólo las empresas donde la clave existe: {"02": {"activa": true, ...}}.
    empresas: Dict[str, ClaveSaeEnEmpresa] = Field(default_factory=dict)
    producto_id: Optional[uuid.UUID] = None
    producto_nombre: Optional[str] = None


# ── «Así está en SAE» y el estado de las claves del catálogo (2-oct-2026) ────

class ArticuloSaeEmpresaOut(BaseModel):
    """Un artículo en UNA empresa de SAE, leído en vivo de INVE.

    `unidad` es el UNI_MED crudo de SAE (KG, PZ, CJ…) y `unidad_canonica` la
    misma en el idioma del Facturador (KILO, PIEZA, CAJA…), para compararla
    contra la presentación sin que la pantalla traduzca. Las descripciones del
    esquema y de las claves SAT son informativas: se leen, no se guardan.
    """
    existe: bool = False
    activa: Optional[bool] = None
    descripcion: Optional[str] = None
    unidad: Optional[str] = None
    unidad_canonica: Optional[str] = None
    linea: Optional[str] = None
    esquema: Optional[int] = None
    esquema_descripcion: Optional[str] = None
    sat: Optional[str] = None
    sat_descripcion: Optional[str] = None
    sat_unidad: Optional[str] = None
    sat_unidad_descripcion: Optional[str] = None


class ArticuloSaeOut(BaseModel):
    """Respuesta de `GET /productos/claves-sae/{clave}/en-sae`.

    Siempre trae las cuatro empresas del SAE 10 (02-05); la que no tiene la
    clave sale con `existe=false`. Con SAE caído o sin configurar contesta
    igual, con `disponible=false` y el motivo: la pantalla de ligar se degrada
    a lo que dice el espejo, nunca a un error.
    """
    clave: str
    disponible: bool
    motivo: Optional[str] = None
    empresas: Dict[str, ArticuloSaeEmpresaOut] = Field(default_factory=dict)


class ClaveSaeEstadoEmpresa(BaseModel):
    activa: bool


class SolicitudSaeResumenOut(BaseModel):
    """La última alta pedida de una clave, lo justo para pintar su chip
    («Alta pendiente», «Alta con error»)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tipo: str
    estado: str
    empresas: List[str] = Field(default_factory=list)
    solicitada_at: datetime
    motivo: Optional[str] = None


class ClaveSaeEstadoOut(BaseModel):
    """Una clave que usa el catálogo: en qué empresas existe según el espejo y
    su última solicitud de ALTA (si alguna)."""
    clave: str
    # Sólo las empresas 02-05 donde el espejo la tiene.
    empresas: Dict[str, ClaveSaeEstadoEmpresa] = Field(default_factory=dict)
    solicitud: Optional[SolicitudSaeResumenOut] = None


class ClaveClienteOut(BaseModel):
    """Un SKU exclusivo de cliente: el artículo de SAE con el que ESOS clientes
    facturan el producto en esa unidad, distinto de la clave de todos."""
    producto_id: uuid.UUID
    unidad: str
    clave: str
    clientes: list[str]
