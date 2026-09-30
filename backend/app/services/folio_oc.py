"""El folio de las OC que arma el bot — dos formatos conviven.

Viejo (entregas hasta el 4-oct-2026, semana 39): proyecto + SEMANA + punto + día.
    VH-39ROV-MIE, HO-38IMS-LUN, VH-38ROV-LUN-B (la B del corte de 25-sep).

Nuevo (entregas desde el 5-oct-2026, semana 40): sucursal + proyecto, punto y la
FECHA exacta de entrega; el punto va con cinco letras (30-sep: ROV → ROVIR). Decisión del dueño 29-sep-2026: la semana es un número
que se CALCULA —y cada cambio de cálculo (13-sep, 38-B) dejó entregas dobles—;
la fecha no se interpreta.
    TBVH-ROVIR-20260930   Tabasco · hospitales VH · Rovirosa · 30-sep
    HGHO-IMSSB-20261005   Hidalgo · Hospitales · IMSS Bienestar · 5-oct

Los dos llevan el sufijo de entrega aparte al final: …-2. Cualquier otro folio
(Río Libre, los numéricos de Balles/Jubran) es libre y no se interpreta.

La semana ya no viaja en el folio: sale de la fecha con `semana_equipo`, que
cuenta desde el PRIMER LUNES DE ENERO (no ISO; ver el corte 38-B).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

# Sucursal (2 letras) pegada al proyecto (2-3, el prefijo del formato viejo).
_RE_NUEVO = re.compile(r"^([A-Z]{2})([A-Z]{2,3})-([A-Z]{3,5})-(\d{8})(?:-(\d{1,2}))?$")
# El mismo, para buscarlo dentro de un texto (la observación de SAE).
RE_NUEVO_EN_TEXTO = re.compile(r"\b([A-Z]{4,5}-[A-Z]{3,5}-\d{8}(?:-\d{1,2})?)\b")


@dataclass(frozen=True)
class FolioNuevo:
    sucursal: str
    proyecto: str
    punto: str
    fecha: date
    aparte: Optional[int]

    @property
    def prefijo(self) -> str:
        return f"{self.sucursal}{self.proyecto}"


def parse_nuevo(folio: Optional[str]) -> Optional[FolioNuevo]:
    """«TBVH-ROVIR-20260930-2» → sus partes; None si no es del formato nuevo."""
    m = _RE_NUEVO.match((folio or "").strip().upper())
    if not m:
        return None
    try:
        fecha = date(int(m.group(4)[:4]), int(m.group(4)[4:6]), int(m.group(4)[6:]))
    except ValueError:
        return None
    return FolioNuevo(m.group(1), m.group(2), m.group(3), fecha,
                      int(m.group(5)) if m.group(5) else None)


def _primer_lunes(anio: int) -> date:
    d = date(anio, 1, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def semana_equipo(d: date) -> int:
    """La semana como la cuenta el equipo: la 1 arranca el primer lunes de enero.

    2026: 21-sep = 38, 28-sep = 39, 5-oct = 40. Lo que cae antes del primer
    lunes es la última semana del año anterior.
    """
    ancla = _primer_lunes(d.year)
    if d < ancla:
        ancla = _primer_lunes(d.year - 1)
    return (d - ancla).days // 7 + 1


def lunes_de(d: date) -> date:
    return d - timedelta(days=d.weekday())
