"""¿Cambió el documento de una OC respecto a la versión que ya se remisionó?

El cliente reenvía el mismo folio corregido más veces de lo que parece: una
partida de más, una cantidad distinta, otra fecha de entrega. Mientras la orden
sigue PENDIENTE eso se resuelve solo (la ingesta reemplaza el payload). El caso
que importa es el otro: cuando la remisión YA existe, la ingesta no pisa nada
—correcto— y hasta 0067 tampoco decía nada, así que el Master se quedaba con la
versión nueva y la remisión con la vieja.

Aquí vive la comparación. Dos reglas que la hacen usable en vez de ruidosa:

1. **Se comparan las partidas por su identidad, no por su posición.** La clave
   del cliente manda; sin clave, la descripción normalizada. Reordenar el PDF no
   es un cambio.
2. **Solo cuenta lo que cambia lo que se entrega o lo que se cobra**: partidas,
   fecha de entrega y observaciones del documento. Que el archivo se llame
   distinto o que el link de Drive se renueve no despierta a nadie.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Optional

# Lo que se compara del documento. El resto del payload (archivo, jid, pistas
# del cliente) puede cambiar sin que cambie lo que hay que entregar.
CAMPOS_CABECERA = ("fecha_entrega", "observaciones")


def _texto(v) -> str:
    return " ".join(str(v or "").split())


def _norm(v) -> str:
    """Mayúsculas, sin acentos ni signos: 'PIÑA -FRUT-350' == 'PINA-FRUT-350'.

    Misma tolerancia que el cruce por clave de la bandeja — si allá dos textos
    son el mismo producto, aquí no pueden ser un cambio."""
    t = unicodedata.normalize("NFKD", _texto(v).upper())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", t)


def _cantidad(v) -> str:
    """Cantidades y precios comparables: '10', '10.0' y '10.00' son lo mismo.

    Un texto que no es número se compara como texto — no se inventa un 0, que
    haría pasar por iguales dos partidas distintas."""
    try:
        return str(Decimal(str(v).replace(",", "").strip()).normalize())
    except (InvalidOperation, ValueError, AttributeError):
        return _texto(v).upper()


def _clave_linea(ln: dict) -> str:
    """La identidad de una partida: su clave, y si no trae, su descripción."""
    return _norm(ln.get("clave")) or _norm(ln.get("descripcion"))


def _valor_linea(ln: dict) -> tuple:
    return (_cantidad(ln.get("cantidad")),
            _norm(ln.get("unidad")),
            _cantidad(ln.get("precio")) if ln.get("precio") is not None else "")


def _visible(ln: dict) -> dict:
    """La partida como se le enseña a una persona en el aviso y en la pantalla."""
    return {
        "clave": _texto(ln.get("clave")) or None,
        "descripcion": _texto(ln.get("descripcion")) or None,
        "cantidad": _texto(ln.get("cantidad")) or None,
        "unidad": _texto(ln.get("unidad")) or None,
        "precio": _texto(ln.get("precio")) or None,
    }


def huella(payload: Optional[dict]) -> str:
    """Huella estable de lo que importa del documento.

    Sirve para no volver a avisar de un reenvío idéntico al que ya despertó la
    incidencia: el bot reintenta, y un aviso repetido por cada reintento es la
    forma más rápida de que el equipo aprenda a ignorar los avisos."""
    p = payload or {}
    lineas = sorted(
        (_clave_linea(ln),) + _valor_linea(ln) for ln in (p.get("lineas") or [])
    )
    base = {"lineas": lineas, **{c: _texto(p.get(c)) for c in CAMPOS_CABECERA}}
    return hashlib.sha256(
        json.dumps(base, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def diff(antes: Optional[dict], ahora: Optional[dict]) -> Optional[dict]:
    """Qué cambió del documento, o None si en lo que importa no cambió nada.

    Las partidas se agrupan por identidad y se comparan como multiconjunto: dos
    renglones con la misma clave y la misma cantidad son el mismo pedido aunque
    vengan en otro orden o partidos distinto."""
    a, b = antes or {}, ahora or {}

    def agrupar(p):
        out: dict[str, list] = {}
        for ln in (p.get("lineas") or []):
            if not isinstance(ln, dict):
                continue
            out.setdefault(_clave_linea(ln), []).append(ln)
        return out

    ga, gb = agrupar(a), agrupar(b)
    nuevas, quitadas, cambiadas = [], [], []
    for k in gb.keys() - ga.keys():
        nuevas += [_visible(ln) for ln in gb[k]]
    for k in ga.keys() - gb.keys():
        quitadas += [_visible(ln) for ln in ga[k]]
    for k in ga.keys() & gb.keys():
        va = sorted(_valor_linea(ln) for ln in ga[k])
        vb = sorted(_valor_linea(ln) for ln in gb[k])
        if va == vb:
            continue
        cambiadas.append({
            "clave": _texto(gb[k][0].get("clave")) or None,
            "descripcion": _texto(gb[k][0].get("descripcion")) or None,
            "antes": [_visible(ln) for ln in ga[k]],
            "ahora": [_visible(ln) for ln in gb[k]],
        })

    cabecera = {}
    for campo in CAMPOS_CABECERA:
        if _texto(a.get(campo)) != _texto(b.get(campo)):
            cabecera[campo] = {"antes": _texto(a.get(campo)) or None,
                               "ahora": _texto(b.get(campo)) or None}

    if not (nuevas or quitadas or cambiadas or cabecera):
        return None
    return {
        "lineas": {"nuevas": nuevas, "quitadas": quitadas, "cambiadas": cambiadas},
        "cabecera": cabecera,
        "resumen": resumen({"lineas": {"nuevas": nuevas, "quitadas": quitadas,
                                       "cambiadas": cambiadas},
                            "cabecera": cabecera}),
    }


def resumen(d: dict) -> str:
    """Una línea para el aviso de WhatsApp y para el chip de la bandeja.

    El aviso viaja por WhatsApp, donde nadie abre un JSON: tiene que decir de
    un vistazo si vale la pena ir a mirar."""
    ln = d.get("lineas") or {}
    partes = []
    for etiqueta, key in (("nueva", "nuevas"), ("quitada", "quitadas"), ("cambiada", "cambiadas")):
        n = len(ln.get(key) or [])
        if n:
            partes.append(f"{n} partida{'s' if n != 1 else ''} {etiqueta}{'s' if n != 1 else ''}")
    cab = d.get("cabecera") or {}
    if "fecha_entrega" in cab:
        partes.append(f"entrega {cab['fecha_entrega']['antes'] or '—'} → "
                      f"{cab['fecha_entrega']['ahora'] or '—'}")
    if "observaciones" in cab:
        partes.append("cambió la observación")
    return " · ".join(partes) or "cambió el documento"
