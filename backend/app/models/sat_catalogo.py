"""Catálogo SAT oficial (CFDI 4.0) — tablas GLOBALES, no tenant-scoped.

Sembradas por la migración 0041 desde backend/data/*.csv.gz (catCFDI oficial).
Son la única fuente para sugerir y validar clave_sat/unidad_sat: la IA propone,
pero solo se acepta lo que existe aquí. Solo lectura para la app.
"""
from sqlalchemy import Column, String, Text

from ..core.db import Base


class SatClaveProdServ(Base):
    __tablename__ = "sat_clave_prodserv"

    clave = Column(String(8), primary_key=True)          # c_ClaveProdServ
    descripcion = Column(Text, nullable=False)
    palabras_similares = Column(Text)


class SatClaveUnidad(Base):
    __tablename__ = "sat_clave_unidad"

    clave = Column(String(20), primary_key=True)         # c_ClaveUnidad
    nombre = Column(Text, nullable=False)
