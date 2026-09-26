"""La fecha de ENTREGA escondida en las notas de una factura.

La factura se hace días después de entregar, y el que cruza ventas contra
compras (Mini Conta: merma y ganancia por producto) necesita el día en que la
mercancía salió, no el día del timbre. Cuando no hay remisión que lo diga, la
única pista es el texto libre de `facturas.notas`, que se escribe a mano:

    SEM 35 ENTREGA PARA COMEDORES DEL 1 DE SEPTIEMBRE DEL 2026. EN: SHANKA
    SEMANA 36 ALBERGUE LUNES 07/09/2026
    OC 25152 ENTREGA EN BODEGA DOMINGO 23 DE AGOSTO **CLIENTE HIGA**

Función pura, sin BD: se prueba con los textos reales y se puede reusar.

Reglas:
  · Formatos: «1 DE SEPTIEMBRE DEL 2026», «05 AGOSTO 2026», «23 DE AGOSTO»
    (sin año), «07/09/2026», «07-09-2026», «07.09.26». Día primero siempre
    (México). Meses completos, SETIEMBRE y abreviaturas de 3-4 letras.
  · Sin año → el de la factura; si así queda más de 7 días DESPUÉS de la
    factura, el anterior (una entrega de diciembre facturada en enero).
  · Sin mes («MIERCOLES 29») → no se adivina.
  · Solo cuentan fechas entre factura−120 días y factura+7 días; lo demás es
    otra cosa (una vigencia, una fecha de OC vieja) y se descarta.
  · Si hay varias, gana la primera DESPUÉS de la palabra ENTREG*; si ninguna
    va después, la primera del texto.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date, timedelta
from typing import Optional

_DIAS_ANTES = 120
_DIAS_DESPUES = 7

_MESES = {
    "ENERO": 1, "ENE": 1,
    "FEBRERO": 2, "FEB": 2,
    "MARZO": 3, "MAR": 3,
    "ABRIL": 4, "ABR": 4,
    "MAYO": 5, "MAY": 5,
    "JUNIO": 6, "JUN": 6,
    "JULIO": 7, "JUL": 7,
    "AGOSTO": 8, "AGO": 8,
    "SEPTIEMBRE": 9, "SETIEMBRE": 9, "SEPT": 9, "SEP": 9, "SET": 9,
    "OCTUBRE": 10, "OCT": 10,
    "NOVIEMBRE": 11, "NOV": 11,
    "DICIEMBRE": 12, "DIC": 12,
}
_MESES_LARGOS = [m for m in _MESES if len(m) >= 6]

# «1 DE SEPTIEMBRE DEL 2026» · «05 AGOSTO 2026» · «23 DE AGOSTO» · «3 SEP. 2026»
# · «10 DE SEPTIEMBRE2026» (sin espacio). La palabra se captura entera y se
# resuelve aparte: así «35 CAJAS» simplemente no es un mes.
_TEXTO = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:DE\s+)?([A-Z]{3,11})(?![A-Z])\.?"
    r"(?:\s*,?\s*(?:DEL?\s+)?(\d{4})(?!\d))?"
)


def _mes(palabra: str) -> Optional[int]:
    """El mes que nombra la palabra. Tolera erratas SOLO en nombres largos
    («SEMPTIEMBR» en facturas reales): en los cortos una errata choca con
    palabras normales («MAYOR» no es mayo)."""
    if palabra in _MESES:
        return _MESES[palabra]
    if len(palabra) >= 6:
        parecido = difflib.get_close_matches(palabra, _MESES_LARGOS, n=1, cutoff=0.8)
        if parecido:
            return _MESES[parecido[0]]
    return None
# «07/09/2026» · «07-09-2026» · «07.09.26»
_NUMERICO = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})(?!\d)")
_ENTREG = re.compile(r"ENTREG")


def _normalizar(s: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", s)
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return sin_acentos.upper()


def _fecha(anio: int, mes: int, dia: int) -> Optional[date]:
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def _candidatas(texto: str, fecha_factura: date) -> list[tuple[int, date]]:
    out: list[tuple[int, date]] = []
    for m in _TEXTO.finditer(texto):
        mes = _mes(m.group(2))
        if mes is None:
            continue
        dia = int(m.group(1))
        if m.group(3):
            f = _fecha(int(m.group(3)), mes, dia)
        else:
            f = _fecha(fecha_factura.year, mes, dia)
            if f is not None and f > fecha_factura + timedelta(days=_DIAS_DESPUES):
                f = _fecha(fecha_factura.year - 1, mes, dia)
        if f is not None:
            out.append((m.start(), f))
    for m in _NUMERICO.finditer(texto):
        anio = int(m.group(3))
        if anio < 100:
            anio += 2000
        f = _fecha(anio, int(m.group(2)), int(m.group(1)))
        if f is not None:
            out.append((m.start(), f))
    out.sort(key=lambda x: x[0])
    return out


def fecha_entrega_de_notas(notas: Optional[str], fecha_factura: date) -> Optional[date]:
    """La fecha de entrega que dicen las notas, o None si no dicen una creíble."""
    if not notas:
        return None
    texto = _normalizar(notas)
    desde = fecha_factura - timedelta(days=_DIAS_ANTES)
    hasta = fecha_factura + timedelta(days=_DIAS_DESPUES)
    validas = [(p, f) for p, f in _candidatas(texto, fecha_factura) if desde <= f <= hasta]
    if not validas:
        return None
    m = _ENTREG.search(texto)
    if m is not None:
        despues = [f for p, f in validas if p > m.start()]
        if despues:
            return despues[0]
    return validas[0][1]
