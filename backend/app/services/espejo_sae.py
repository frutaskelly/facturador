"""El espejo de facturas de SAE, corriendo DENTRO del Facturador.

Hasta hoy este trabajo vivía en el bot de WhatsApp —era el único con acceso a
la base de SAE— y llegaba aquí por HTTP cada media hora. Con el acceso propio
(24-sep-2026) el Facturador puede leerlo él mismo, y eso cambia dos cosas: ya
no hay un salto de red con su payload en medio, y la frescura deja de depender
de que otro programa esté vivo.

LA MARCA DE AGUA ES EL PROPIO ESPEJO. No hay tabla de estado ni archivo con el
último folio: se pregunta `max(folio)` de lo ya reflejado para esa serie y se
piden a SAE solo los posteriores. Un estado que se deriva no se desincroniza,
y si alguien borra una factura del espejo, la siguiente pasada la vuelve a
traer sola.

DOS BARRIDOS, porque son dos problemas distintos:

  · LO NUEVO (`nuevas`): folios por encima de la marca. Es una consulta
    diminuta sobre un índice, así que puede correr cada medio minuto.
  · LO QUE CAMBIÓ (`cancelaciones`): una factura ya reflejada que se canceló
    NO cambia de folio, así que la marca de agua no la ve nunca. Se revisa una
    ventana corta comparando estado, y sin pedir partidas, que es lo caro.

Reusa la MISMA ruta de depósito que usaba el bot (`factura_espejo`): la lógica
de cruce con el cliente, la liga con la remisión y los candados viven ahí, y
duplicarlos sería garantizar que se separen.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models.factura import Factura
from ..schemas.factura import FacturaEspejoIn, LineaFacturaEspejoIn
from . import sae_lectura

_LOTE_PARTIDAS = 40


def _num(v) -> str:
    return str(v if v is not None else 0).strip() or "0"


def marca_de_agua(db: Session, empresa: str, serie: str) -> int:
    """El folio más alto que el espejo ya tiene de esa serie. 0 si no hay nada."""
    valor = (db.query(func.max(Factura.folio))
             .filter(Factura.origen == "ESPEJO_SAE",
                     Factura.espejo_empresa == empresa,
                     Factura.serie == serie)
             .scalar())
    return int(valor or 0)


def leer_encabezados(empresa: str, serie: str, desde_folio: Optional[int] = None,
                     desde_fecha: Optional[dt.date] = None,
                     limite: int = 500) -> list[dict[str, Any]]:
    """Encabezados de FACTFxx con su UUID y su observación, como los lee el bot.

    Es el mismo SELECT que `facturador_espejo.leer_facturas`, con dos cambios:
    lo que viene de afuera viaja como PARÁMETRO, y se puede pedir «de este
    folio en adelante», que es lo que hace barata la pasada frecuente.
    """
    factf = sae_lectura.tabla("FACTF", empresa)
    cfdi = sae_lectura.tabla("CFDI", empresa)
    obs = sae_lectura.tabla("OBS_DOCF", empresa)
    cond, params = ["F.SERIE = %s"], [serie]
    if desde_folio:
        cond.append("F.FOLIO > %s")
        params.append(int(desde_folio))
    if desde_fecha:
        cond.append("F.FECHA_DOC >= %s")
        params.append(desde_fecha.isoformat())
    filas = sae_lectura.consultar(
        f"SELECT TOP {int(limite)} RTRIM(F.CVE_DOC) AS cve_doc, RTRIM(F.SERIE) AS serie, "
        "F.FOLIO AS folio, LTRIM(RTRIM(F.CVE_CLPV)) AS cliente_sae, "
        "CONVERT(varchar(19), F.FECHA_DOC, 120) AS fecha, "
        # Redondeados a dos, como SAE los muestra y como salen en el CFDI: con
        # cuatro decimales el importe difería un centavo de la pantalla en los
        # que caen en .xx5, y un centavo hace dudar de todo lo demás.
        "CAST(CAST(ROUND(ISNULL(F.CAN_TOT,0),2) AS decimal(18,2)) AS varchar(30)) AS subtotal, "
        "CAST(CAST(ROUND(ISNULL(F.IMPORTE,0),2) AS decimal(18,2)) AS varchar(30)) AS total, "
        # IVA e IEPS POR SEPARADO: derivar el IVA como total menos subtotal
        # suma los dos impuestos cuando hay IEPS de por medio.
        "CAST(CAST(ROUND(ISNULL(F.IMP_TOT4,0),2) AS decimal(18,2)) AS varchar(30)) AS iva, "
        "CAST(CAST(ROUND(ISNULL(F.IMP_TOT1,0),2) AS decimal(18,2)) AS varchar(30)) AS ieps, "
        "RTRIM(ISNULL(F.STATUS,'')) AS status, "
        "LTRIM(RTRIM(ISNULL(C.UUID,''))) AS uuid, "
        "LTRIM(RTRIM(ISNULL(C.FECHA_CANCELA,''))) AS fecha_cancela, "
        "LEFT(ISNULL(O.STR_OBS,''), 250) AS observaciones, "
        # La cancelación PEDIDA al SAT: SAE deja la factura viva hasta que el
        # SAT responde, y el estado de cuenta la seguía cobrando.
        "LTRIM(RTRIM(ISNULL(C.MSJ_CANC,''))) AS cancelacion_msj, "
        "LTRIM(RTRIM(ISNULL(C.UUID_REL,''))) AS uuid_sustitucion "
        f"FROM {factf} F "
        f"LEFT JOIN {cfdi} C ON RTRIM(C.CVE_DOC)=RTRIM(F.CVE_DOC) AND C.TIPO_DOC='F' "
        f"LEFT JOIN {obs} O ON O.CVE_OBS=F.CVE_OBS "
        f"WHERE {' AND '.join(cond)} ORDER BY F.FOLIO",
        tuple(params),
    )
    salida = []
    for r in filas:
        cve = str(r.get("cve_doc") or "").strip()
        if not cve:
            continue
        cancelada = (str(r.get("status") or "").strip().upper() == "C"
                     or bool(str(r.get("fecha_cancela") or "").strip()))
        uuid_f = str(r.get("uuid") or "").strip()
        salida.append({
            "cve_doc": cve, "serie": str(r.get("serie") or "").strip(),
            "folio": int(r.get("folio") or 0),
            "cliente_sae": str(r.get("cliente_sae") or "").strip(),
            "fecha": str(r.get("fecha") or "").strip(),
            "subtotal": _num(r.get("subtotal")), "total": _num(r.get("total")),
            "iva": _num(r.get("iva")), "ieps": _num(r.get("ieps")),
            # Sin UUID no hay timbrado: el documento existe pero el PAC no lo
            # confirmó. El backend además rechaza TIMBRADA sin uuid_fiscal.
            "estado": ("CANCELADA" if cancelada else "TIMBRADA" if uuid_f else "BORRADOR"),
            "uuid": uuid_f or None,
            "observaciones": str(r.get("observaciones") or "").strip() or None,
            "cancelacion_msj": str(r.get("cancelacion_msj") or "").strip() or None,
            "uuid_sustitucion": str(r.get("uuid_sustitucion") or "").strip() or None,
        })
    return salida


def leer_partidas(empresa: str, cve_docs: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{cve_doc: [partidas]} de PAR_FACTFxx, por lotes y con la descripción."""
    par = sae_lectura.tabla("PAR_FACTF", empresa)
    inve = sae_lectura.tabla("INVE", empresa)
    salida: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(cve_docs), _LOTE_PARTIDAS):
        lote = [d for d in cve_docs[i:i + _LOTE_PARTIDAS] if d]
        if not lote:
            continue
        marcadores = ", ".join(["%s"] * len(lote))
        for r in sae_lectura.consultar(
            "SELECT RTRIM(A.CVE_DOC) AS doc, LTRIM(RTRIM(A.CVE_ART)) AS clave, "
            "CAST(CAST(A.CANT AS decimal(18,4)) AS varchar(30)) AS cantidad, "
            "CAST(CAST(A.PREC AS decimal(18,4)) AS varchar(30)) AS precio, "
            "CAST(CAST(ISNULL(A.TOT_PARTIDA,0) AS decimal(18,4)) AS varchar(30)) AS importe, "
            "RTRIM(ISNULL(I.DESCR,'')) AS descripcion "
            f"FROM {par} A LEFT JOIN {inve} I ON RTRIM(I.CVE_ART)=RTRIM(A.CVE_ART) "
            f"WHERE RTRIM(A.CVE_DOC) IN ({marcadores}) "
            "ORDER BY A.CVE_DOC, TRY_CAST(A.NUM_PAR AS INT)",
            tuple(lote),
        ):
            clave = str(r.get("clave") or "").strip()
            salida.setdefault(str(r.get("doc") or "").strip(), []).append({
                "clave": clave or None,
                "cantidad": _num(r.get("cantidad")),
                "precio_unitario": _num(r.get("precio")),
                "importe": _num(r.get("importe")),
                "descripcion": (str(r.get("descripcion") or "").strip() or clave or "PARTIDA SAE")[:1000],
            })
    return salida


def como_payload(empresa: str, cab: dict, partidas: list[dict]) -> FacturaEspejoIn:
    """El encabezado de SAE y sus partidas, con la forma que espera el depósito."""
    return FacturaEspejoIn(
        empresa=empresa, serie=cab["serie"], folio=cab["folio"],
        cliente_sae=cab["cliente_sae"], fecha=cab["fecha"] or None,
        estado=cab["estado"], uuid_fiscal=cab["uuid"],
        cancelacion_msj=(cab["cancelacion_msj"] or None),
        observaciones=cab["observaciones"],
        subtotal=cab["subtotal"], total=cab["total"],
        iva=cab["iva"], ieps=cab["ieps"],
        uuid_sustitucion=cab["uuid_sustitucion"],
        lineas=[LineaFacturaEspejoIn(**p) for p in partidas],
    )


def sincronizar(db: Session, ctx: AuthContext, empresa: str, series: list[str],
                dias_cancelaciones: int = 3, limite: int = 500) -> dict[str, Any]:
    """Una pasada: trae lo nuevo y revisa lo que pudo haber cambiado.

    Devuelve el conteo por serie. No lanza por una factura mala: una sola
    factura rara no puede dejar sin espejo a las otras doscientas, así que su
    error se cuenta y se sigue. Lo que sí sube es un fallo de SAE, porque
    entonces la pasada entera no significa nada y decir «0 nuevas» sería
    mentir.
    """
    from ..api.v1.facturas import factura_espejo   # perezoso: la ruta importa servicios

    hoy = dt.date.today()
    desde = hoy - dt.timedelta(days=max(0, int(dias_cancelaciones)))
    res: dict[str, Any] = {"empresa": empresa, "nuevas": 0, "actualizadas": 0,
                           "errores": [], "por_serie": {}}

    for serie in series:
        marca = marca_de_agua(db, empresa, serie)
        cabs = leer_encabezados(empresa, serie, desde_folio=marca or None, limite=limite)

        # LO QUE PUDO CAMBIAR: una factura ya reflejada que se cancela NO cambia
        # de folio, así que la marca de agua no la ve nunca. Se comparan los
        # estados de una ventana corta y solo se re-depositan las que difieren.
        vistos = {c["folio"] for c in cabs}
        previas = leer_encabezados(empresa, serie, desde_fecha=desde, limite=limite)
        en_espejo = {
            f.folio: f.estado for f in db.query(Factura).filter(
                Factura.origen == "ESPEJO_SAE", Factura.espejo_empresa == empresa,
                Factura.serie == serie, Factura.fecha >= desde).all()
        }
        cambiadas = [c for c in previas
                     if c["folio"] not in vistos
                     and c["folio"] in en_espejo
                     and en_espejo[c["folio"]] != c["estado"]]

        pendientes = cabs + cambiadas
        if not pendientes:
            res["por_serie"][serie] = {"nuevas": 0, "actualizadas": 0, "marca": marca}
            continue

        partidas = leer_partidas(empresa, [c["cve_doc"] for c in pendientes])
        nuevas = actualizadas = 0
        for cab in pendientes:
            try:
                factura_espejo(payload=como_payload(empresa, cab, partidas.get(cab["cve_doc"], [])),
                               db=db, ctx=ctx)
                if cab["folio"] in vistos:
                    nuevas += 1
                else:
                    actualizadas += 1
            except Exception as e:      # una factura rara no tumba la pasada
                res["errores"].append(f"{serie} {cab['folio']}: {type(e).__name__}: {e}")
        res["nuevas"] += nuevas
        res["actualizadas"] += actualizadas
        res["por_serie"][serie] = {"nuevas": nuevas, "actualizadas": actualizadas,
                                   "marca": marca}
    return res
