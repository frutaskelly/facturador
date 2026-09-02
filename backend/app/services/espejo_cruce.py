"""Cruce OC ↔ espejo de SAE, en ambas direcciones.

La llave de conciliación de todo el ecosistema (export masivo, bot, Master) es
la Observación de la factura de SAE: "OC <su_pedido> …" o, en EHMO/MAFAN, el
folio interno al final del texto. `extraer_oc` / `norm_oc` viven aquí porque
las usan los DOS lados del cruce:

1. **Factura → remisión** (POST /facturas/espejo): al depositar una factura
   timbrada, busca UNA remisión libre del cliente con ese `su_pedido`.
2. **Remisión → factura** (`ligar_remision_con_espejo`, este módulo): el
   REINTENTO. Si la remisión nace o cambia DESPUÉS de la última pasada de esa
   factura por el espejo (la sync del conector solo re-manda ~3 días de
   FECHA_DOC), el camino 1 ya no se vuelve a intentar y el vínculo se pierde —
   caso real HO-33APA-MAR / ZEHMOHOS 810 (2-sep-2026), arreglado a mano
   re-mandando el folio. Al crear o editar una remisión con `su_pedido`, este
   camino busca la factura espejo huérfana que la estaba esperando.

Ambos lados aplican LOS MISMOS candados: una sola candidata (con dos no se
adivina), tolerancia de importes 15%/$100 (SAE ajusta cantidades al importar),
y jamás pisar una factura nativa viva.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Cliente, Factura, Remision

_RE_OC_OBS = re.compile(r"\bOC[\s:]+([A-Z0-9][A-Z0-9\-\/\.]*)")
# El folio interno de una entrega EHMO/MAFAN: dos letras del proyecto, la
# semana, el punto y el día — HO-33PAC-LUN, SN-33NER-JUE, VH-35SAL-VIE.
_RE_FOLIO_INTERNO = re.compile(r"\b([A-Z]{2}-\d{1,2}[A-Z]{2,4}(?:-[A-Z]{3})?)\b")


def norm_oc(v: Optional[str]) -> Optional[str]:
    """SAE escribe la OC con ceros a la izquierda ('0000024646') y el
    Facturador la guarda limpia ('24646'): se comparan sin ceros. Un folio
    alfanumérico (DI-32MAF) se compara tal cual, en mayúsculas."""
    v = (v or "").strip().upper()
    if not v:
        return None
    return (v.lstrip("0") or "0") if v.isdigit() else v


def extraer_oc(observaciones: Optional[str]) -> Optional[str]:
    """'OC 0000024736 ENTREGA CEDIS' → '24736'; 'OC DI-32MAF …' → 'DI-32MAF'.

    Es la MISMA llave que escribe el export masivo en la Observación de SAE
    (services/export_sae._observacion) y la que el resto del ecosistema
    (bot, Master) usa para conciliar. Solo la primera: una obs con dos OC es
    ambigua y no decide.

    Segundo formato, el de EHMO/MAFAN: sus observaciones NO llevan el prefijo
    "OC" y el folio interno va al FINAL — «SEMANA 33 SECRETARIO NERI REQ
    20/08/2026 SN-33NER-JUE». Ese folio es igualmente el `su_pedido` de la
    remisión, así que también sirve de llave; sin reconocerlo, 46 facturas
    reales quedaron sin ligar a su entrega (detectado el 30-ago buscando
    SN-33NER-JUE).
    """
    texto = (observaciones or "").upper()
    m = _RE_OC_OBS.search(texto)
    if m:
        return norm_oc(m.group(1))
    m = _RE_FOLIO_INTERNO.search(texto)
    return norm_oc(m.group(1)) if m else None


def _dentro_de_tolerancia(total_rem, total_fac) -> bool:
    """Una OC puede amparar VARIAS entregas: si el importe se aleja demasiado,
    la candidata no es (o no es SOLO) esta remisión — no se estampa. SAE ajusta
    cantidades al importar, así que la tolerancia es generosa (15% o $100)."""
    if not total_rem or not total_fac:
        return True
    dif = abs(Decimal(str(total_rem)) - Decimal(str(total_fac)))
    return dif <= max(Decimal(str(total_fac)) * Decimal("0.15"), Decimal("100"))


def ligar_remision_con_espejo(db: Session, rem: Remision) -> Optional[Factura]:
    """Reintento del cruce, del lado de la remisión: busca la factura ESPEJO_SAE
    TIMBRADA del mismo cliente, sin remisión ligada, cuya OC (extraída de sus
    observaciones) coincide con el `su_pedido` de `rem`. Si hay EXACTAMENTE una
    y el importe cuadra, estampa y liga igual que lo haría el espejo.

    No hace flush ni commit: muta `rem` (y devuelve la factura ligada) dentro
    de la transacción del caller. Devuelve None si no ligó — nunca es error:
    lo ambiguo o descuadrado queda para la estampa manual, como siempre.
    """
    if not rem.su_pedido or rem.factura_sae:
        return None
    if rem.estado not in ("BORRADOR", "CONFIRMADA"):
        return None
    oc = norm_oc(rem.su_pedido)
    if oc is None:
        return None
    # Nunca pisar el vínculo con una factura NATIVA viva (mismo candado que el
    # espejo): si la remisión ya tiene CFDI propio, no se toca.
    if rem.factura_id:
        previa = db.query(Factura).filter(Factura.id == rem.factura_id).one_or_none()
        if previa is not None and previa.origen != "ESPEJO_SAE" and previa.estado != "CANCELADA":
            return None
    # Cliente ya cortado del espejo (espejo_sae apagado): sus ventas nuevas se
    # facturan nativas aquí — una espejo huérfana vieja ya no le estampa nada.
    en_espejo = db.query(Cliente.espejo_sae).filter(
        Cliente.id == rem.cliente_facturacion_id
    ).scalar()
    if not en_espejo:
        return None

    # Huérfanas: timbradas del cliente SIN remisión ligada por factura_id. La
    # OC no se puede extraer en SQL, así que se filtra en Python — el NOT
    # EXISTS deja pocas filas (lo normal es que casi todas ya estén ligadas).
    ya_ligada = (
        db.query(Remision.id)
        .filter(Remision.factura_id == Factura.id, Remision.deleted_at.is_(None))
        .exists()
    )
    huerfanas = [
        f for f in db.query(Factura).filter(
            Factura.tenant_id == rem.tenant_id,
            Factura.cliente_id == rem.cliente_facturacion_id,
            Factura.origen == "ESPEJO_SAE",
            Factura.estado == "TIMBRADA",
            Factura.notas.isnot(None),
            Factura.deleted_at.is_(None),
            ~ya_ligada,
        )
        if extraer_oc(f.notas) == oc
    ]

    # Una estampa manual pendiente ('ZHGO331', 'ZHGO 0331') también reclama la
    # factura aunque aún no tenga factura_id: se casa con la misma tolerancia
    # de ceros/espacios que usa el espejo (parsear_marca).
    from .export_sae import parsear_marca

    def _reclamada_por_marca(f: Factura) -> bool:
        marcadas = db.query(Remision.factura_sae).filter(
            Remision.tenant_id == rem.tenant_id,
            Remision.factura_sae.ilike(f"{f.serie}%"),
            Remision.deleted_at.is_(None),
        ).all()
        return any(parsear_marca(v or "") == (f.serie, f.folio) for (v,) in marcadas)

    huerfanas = [f for f in huerfanas if not _reclamada_por_marca(f)]
    if len(huerfanas) != 1:
        # Con dos candidatas no se adivina — igual que el espejo.
        return None
    factura = huerfanas[0]
    if not _dentro_de_tolerancia(rem.total, factura.total):
        return None

    # FOR UPDATE sobre la elegida: serializa contra un POST /facturas/espejo
    # concurrente del mismo folio, y se re-verifica que siga huérfana.
    factura = (
        db.query(Factura)
        .filter(Factura.id == factura.id, Factura.deleted_at.is_(None))
        .with_for_update()
        .one_or_none()
    )
    if factura is None or factura.estado != "TIMBRADA":
        return None
    if db.query(Remision.id).filter(
        Remision.factura_id == factura.id, Remision.deleted_at.is_(None)
    ).first() is not None:
        return None

    rem.factura_sae = f"{factura.serie} {factura.folio}"
    rem.factura_id = factura.id
    rem.export_sae_folio = None   # ya no hay nada que exportar: manda la marca
    rem.estado = "FACTURADA"
    return factura
