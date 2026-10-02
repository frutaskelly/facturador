"""El folio de las OC que arma el bot — dos formatos conviven.

Viejo (entregas hasta el 4-oct-2026, semana 39): proyecto + SEMANA + punto + día.
    VH-39ROV-MIE, HO-38IMS-LUN, VH-38ROV-LUN-B (la B del corte de 25-sep).

Nuevo (entregas desde el 5-oct-2026, semana 40): sucursal + proyecto, punto y la
FECHA exacta de entrega; el punto va con cinco letras (30-sep: ROV → ROVIR). Decisión del dueño 29-sep-2026: la semana es un número
que se CALCULA —y cada cambio de cálculo (13-sep, 38-B) dejó entregas dobles—;
la fecha no se interpreta.
    TBVH-ROVIR-20260930   Tabasco · hospitales VH · Rovirosa · 30-sep
    HGHO-IMSSB-20261005   Hidalgo · Hospitales · IMSS Bienestar · 5-oct

Con almacén (decisión del dueño 2-oct-2026, desde la misma semana 40): sucursal +
ALMACÉN, el proyecto con TRES letras, el punto y la fecha. Las 29 remisiones que la
semana 40 alcanzó a generar con el formato de arriba se renombraron a éste.
    HGPA-HOS-PACHU-261009     Hidalgo · almacén Pachuca · Hospitales · Pachuca · 9-oct
    TBVH-HOS-ROVIR-261007     Tabasco · almacén Villahermosa · Hospitales · Rovirosa
La fecha va AAMMDD (dueño, 2-oct-2026); la de 8 dígitos (20261009) se sigue leyendo.

Proyecto con CINCO letras (dueño, 2-oct-2026, mismo día): las primeras cinco del
nombre, como el punto. DIF se queda de tres. Las de tres letras se renombraron y se
siguen leyendo.
    HGPA-HOSPI-PACHU-261009   (antes HGPA-HOS-PACHU-261009)

Todos llevan el sufijo de entrega aparte al final: …-2. Cualquier otro folio
(Río Libre, los numéricos de Balles/Jubran) es libre y no se interpreta.

`proyecto` es SIEMPRE el prefijo de siempre (HO, VH, DI…): es el que el bot traduce
a proyecto y el que se compara contra los folios viejos. El código del proyecto
(cinco letras, o las tres de antes) se traduce con PROYECTO_3.

La semana ya no viaja en el folio: sale de la fecha con `semana_equipo`, que
cuenta desde el PRIMER LUNES DE ENERO (no ISO; ver el corte 38-B).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

# Sucursal (2 letras) pegada al proyecto (2-3, el prefijo del formato viejo).
_RE_NUEVO = re.compile(r"^([A-Z]{2})([A-Z]{2,3})-([A-Z]{3,5})-(\d{6}(?:\d{2})?)(?:-(\d{1,2}))?$")
# Sucursal + almacén (2 y 2) · proyecto (5, o 3 los de antes) · punto · fecha.
_RE_ALMACEN = re.compile(r"^([A-Z]{2})([A-Z]{2})-([A-Z]{3,5})-([A-Z]{3,5})-(\d{6}(?:\d{2})?)(?:-(\d{1,2}))?$")
# Los dos, para buscarlos dentro de un texto (la observación de SAE).
RE_NUEVO_EN_TEXTO = re.compile(r"\b([A-Z]{4,5}(?:-[A-Z]{3,5})?-[A-Z]{3,5}-\d{6}(?:\d{2})?(?:-\d{1,2})?)\b")

# (sucursal, código del proyecto) → el prefijo de siempre. Lo que no está aquí
# (BIC) no tuvo prefijo viejo y se queda con su código.
PROYECTO_5 = {
    ("HG", "HOSPI"): "HO", ("HG", "DIF"): "DI", ("HG", "CEREZ"): "CE",
    ("HG", "SEGUR"): "SP", ("HG", "SNERI"): "SN",
    ("TB", "HOSPI"): "VH",
}
# Los de tres letras (2-oct-2026, antes del cambio a cinco) se siguen leyendo.
_PROYECTO_3_VIEJO = {
    ("HG", "HOS"): "HO", ("HG", "CER"): "CE", ("HG", "SEG"): "SP",
    ("HG", "NER"): "SN", ("TB", "HOS"): "VH",
}
PROYECTO_3 = {**_PROYECTO_3_VIEJO, **PROYECTO_5}
# prefijo → los códigos con que puede venir (el vigente primero).
_DE_PREFIJO: dict[str, list[tuple[str, str]]] = {}
for _k, _v in PROYECTO_3.items():
    _DE_PREFIJO.setdefault(_v, []).append(_k)
for _v in _DE_PREFIJO.values():
    _v.sort(key=lambda k: -len(k[1]))


@dataclass(frozen=True)
class FolioNuevo:
    sucursal: str
    proyecto: str            # el prefijo de siempre: HO, VH, DI…
    punto: str
    fecha: date
    aparte: Optional[int]
    almacen: Optional[str] = None      # sólo el formato con almacén
    proyecto3: Optional[str] = None    # HOSPI, DIF… (ídem; HOS en los de antes)

    @property
    def prefijo(self) -> str:
        """La cabeza literal del folio, para buscar por LIKE: HGHO o HGPA-HOSPI."""
        if self.almacen:
            return f"{self.sucursal}{self.almacen}-{self.proyecto3}"
        return f"{self.sucursal}{self.proyecto}"


def _fecha8(s: str) -> Optional[date]:
    """AAAAMMDD o, desde el 2-oct-2026, AAMMDD (261009 = 9-oct-2026)."""
    if len(s) == 6:
        s = "20" + s
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None


def parse_nuevo(folio: Optional[str]) -> Optional[FolioNuevo]:
    """«TBVH-ROVIR-20260930-2» o «HGPA-HOS-PACHU-20261009» → sus partes;
    None si no es de ninguno de los formatos con fecha."""
    f = (folio or "").strip().upper()
    m = _RE_ALMACEN.match(f)
    if m:
        fecha = _fecha8(m.group(5))
        if fecha is None:
            return None
        suc, p3 = m.group(1), m.group(3)
        return FolioNuevo(suc, PROYECTO_3.get((suc, p3), p3), m.group(4), fecha,
                          int(m.group(6)) if m.group(6) else None,
                          almacen=m.group(2), proyecto3=p3)
    m = _RE_NUEVO.match(f)
    if not m:
        return None
    fecha = _fecha8(m.group(4))
    if fecha is None:
        return None
    return FolioNuevo(m.group(1), m.group(2), m.group(3), fecha,
                      int(m.group(5)) if m.group(5) else None)


def like_con_fecha(prefijo_viejo: str) -> list[str]:
    """Patrones LIKE de los folios con fecha de un proyecto dado por su prefijo
    de siempre (HO → «__HO-%», «HG__-HOSPI-%» y el de tres letras «HG__-HOS-%»)."""
    pats = [f"__{prefijo_viejo}-%"]
    for suc, p in _DE_PREFIJO.get(prefijo_viejo, []):
        pats.append(f"{suc}__-{p}-%")
    return pats


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
