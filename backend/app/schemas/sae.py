"""Lo que el Facturador contesta de SAE en vivo (rutas de /sae)."""
from typing import List

from pydantic import BaseModel


class LineaSaeOut(BaseModel):
    """Una línea de producto de SAE (CLIN): su clave y su nombre."""
    codigo: str
    nombre: str


class EsquemaSaeOut(BaseModel):
    """Un esquema de impuestos de SAE (IMPU). IVA e IEPS en PORCENTAJE:
    16.0 es 16 %, no 0.16."""
    codigo: int
    descripcion: str
    iva: float
    ieps: float


class SaeCatalogosOut(BaseModel):
    """Con qué se puede dar de alta o cambiar un artículo en esa empresa."""
    empresa: str
    lineas: List[LineaSaeOut]
    esquemas: List[EsquemaSaeOut]
    # Las unidades que el escritor sabe traducir, una por unidad de SAE.
    unidades: List[str]
