"""Depósito del catálogo de claves de SAE (espejo de INVE##)."""
from typing import List, Optional

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
