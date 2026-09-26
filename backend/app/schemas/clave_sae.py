"""El catálogo de claves de SAE (espejo de INVE##): su depósito y su búsqueda."""
import uuid
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


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
