"""¿De qué proyecto es esta factura? Un solo criterio para todos los reportes.

Antes Reportes y Cobranza clasificaban con un mapa serie→nombre escrito en el
código, con nombres que no cuadraban con Catálogo → Proyectos. Desde el 1-oct
(migr 0094) el catálogo manda y este módulo es lo único que lo interpreta:

  1. El proyecto que la factura ya trae (`facturas.proyecto_id`).
  2. El de su remisión, si la tiene ligada.
  3. Su serie: el proyecto que la declara en `series`. Si varios comparten la
     serie (ZMAFAN), gana el que tenga alguna de sus `palabras_obs` en la
     observación de la factura; si ninguna aparece, el que no tiene palabras.

Y luego «se reporta en»: NERI cobra con su lista pero su fila es CERESOS.
Lo que no cae en ningún proyecto se reporta con el nombre del cliente.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import Proyecto, Remision


def _norm(serie: str | None) -> str:
    return (serie or "").strip().upper()


class ProyectoDeFactura:
    def __init__(self, db: Session, tenant_id):
        # Borrados incluidos: una factura vieja sigue siendo de su proyecto
        # aunque el proyecto ya no se use. El filtro por inquilino va explícito
        # aunque RLS lo haga en producción: la BD de pruebas corre sin RLS.
        proyectos = db.query(Proyecto).filter(Proyecto.tenant_id == tenant_id).all()
        self._por_id = {p.id: p for p in proyectos}
        self._por_serie: dict[str, list[Proyecto]] = {}
        for p in proyectos:
            if p.deleted_at is not None:
                continue
            for s in p.series or []:
                self._por_serie.setdefault(_norm(s), []).append(p)
        # Con palabras primero: son los que se distinguen; el que no tiene se
        # queda con el resto.
        for lista in self._por_serie.values():
            lista.sort(key=lambda p: (not p.palabras_obs, p.nombre))
        self._de_remision = dict(
            db.query(Remision.factura_id, Remision.proyecto_id)
            .filter(
                Remision.tenant_id == tenant_id,
                Remision.factura_id.isnot(None),
                Remision.proyecto_id.isnot(None),
                Remision.deleted_at.is_(None),
            )
            .all()
        )

    def proyecto(self, f) -> Proyecto | None:
        """El proyecto de la factura, ya llevado a la fila donde se reporta."""
        p = self._por_id.get(getattr(f, "proyecto_id", None))
        if p is None:
            p = self._por_id.get(self._de_remision.get(f.id))
        if p is None:
            p = self._por_serie_y_obs(f)
        if p is not None and p.reporta_en_id in self._por_id:
            p = self._por_id[p.reporta_en_id]
        return p

    def _por_serie_y_obs(self, f) -> Proyecto | None:
        candidatos = self._por_serie.get(_norm(f.serie))
        if not candidatos:
            return None
        notas = f.notas or ""
        for p in candidatos:
            if not p.palabras_obs:
                return p
            if any(re.search(rf"\b{re.escape(w)}\b", notas, re.I) for w in p.palabras_obs):
                return p
        return None

    def nombre(self, f) -> str | None:
        p = self.proyecto(f)
        return p.nombre if p else None

    def fila(self, f, nombre_cliente: str) -> str:
        """La fila del reporte: el proyecto o, si no cae en ninguno, el cliente."""
        return self.nombre(f) or nombre_cliente
