"""Los REP y las notas de crédito que timbra SAE, leídos por el Facturador.

Puerto de `facturador_espejo_cobranza.py` del bot (24-sep-2026). Misma lógica y
mismas consultas: lo que cambia es quién las hace. Sin esto, apagar el espejo
del bot dejaba «Comprobantes de pago» vacío.

DOS COSAS QUE NO SON OBVIAS Y VIENEN DEL ORIGINAL:

  · El saldo anterior y la parcialidad que lleva el XML del REP NO están en
    ninguna columna: se reconstruyen desde la CxC —cargo original menos los
    abonos previos— porque los REP traen IMPORTE=0.
  · Una nota de crédito CANCELADA ya no tiene renglones en la CxC, y sin
    renglones no hay cliente al cual colgarla. Se cuenta y se deja: es lo que
    hacía el bot y es lo correcto, porque inventarle dueño sería peor.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models.cliente_externo import ClienteExterno
from . import sae_lectura

log = logging.getLogger(__name__)

_LOTE = 40
# Concepto de la CxC -> forma de pago del SAT, cuando el REP no la trae.
_FORMA_POR_CONCEPTO = {"10": "01", "11": "02", "15": "02", "22": "03", "1003": "03"}
# Clientes de mostrador de la empresa 02: no son clientes de verdad.
_PLACEHOLDERS = {"02": {"1", "2", "3"}}


def _num(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _dinero(v: float) -> str:
    return f"{round(float(v or 0), 2):.2f}"


def _partir(ref: str) -> Optional[tuple[str, int]]:
    """'ZHGO 370' -> ('ZHGO', 370) · 'ZCH5C 12' -> ('ZCH5C', 12). None si no
    tiene forma de documento.

    BUG DEL 24 AL 26-SEP-2026: aquí se partía con «letras y luego dígitos»
    (`^([A-Za-z]*)\s*(\d+)$`), y las series con dígitos de la empresa 04
    —ZCH5C, MIN5C— no casaban: sus REP y notas de crédito se descartaban EN
    SILENCIO (el renglón salía None y el pago llegaba sin facturas, o la nota
    se contaba como omitida). El bot no lo tenía porque partía por el ÚLTIMO
    bloque de dígitos. Ahora la regla vive en `sae_lectura.partir_documento`:
    el folio son los dígitos después del último espacio, y un documento pegado
    ('ZCH5C12') es ambiguo y NO se adivina.
    """
    return sae_lectura.partir_documento(ref)


def clientes_con_equivalencia(db: Session, ctx: AuthContext, empresa: str) -> set[str]:
    """Las claves de SAE de esa empresa que el Facturador sabe de quién son.

    Reflejar un comprobante de un cliente sin equivalencia no se puede: el
    depósito lo rechaza. Se filtra aquí para contarlo como omitido en vez de
    coleccionar errores.
    """
    prefijo = f"{empresa}:"
    return {
        eq.clave.split(":", 1)[1].upper()
        for eq in db.query(ClienteExterno).filter(
            ClienteExterno.tenant_id == ctx.tenant_id,
            ClienteExterno.sistema == "SAE",
            ClienteExterno.clave.like(f"{prefijo}%")).all()
        if eq.clave and ":" in eq.clave
    }


def saldos_por_factura(empresa: str, refers: list[str]) -> dict[str, list[dict]]:
    """{refer: [abonos en orden]} con el saldo que la factura tenía ANTES de cada uno.

    Es el ImpSaldoAnt / ImpSaldoInsoluto / NumParcialidad del XML del REP,
    reconstruido desde la CxC: cargo original menos los abonos anteriores
    (pagos Y notas de crédito, que ahí también son abonos).
    """
    cuen_m, cuen_det = sae_lectura.tabla("CUEN_M", empresa), sae_lectura.tabla("CUEN_DET", empresa)
    out: dict[str, list[dict]] = {}
    for i in range(0, len(refers), _LOTE):
        lote = [r for r in refers[i:i + _LOTE] if r]
        if not lote:
            continue
        marcadores = ", ".join(["%s"] * len(lote))
        if sae_lectura.motor() == "firebird":
            cargos, movs = _saldos_firebird(cuen_m, cuen_det, lote, marcadores)
        else:
            cargos = {
                str(r.get("refer") or "").strip(): _num(r.get("cargo"))
                for r in sae_lectura.consultar(
                    f"SELECT LTRIM(RTRIM(REFER)) AS refer, SUM(IMPORTE) AS cargo FROM {cuen_m} "
                    f"WHERE NUM_CPTO=1 AND LTRIM(RTRIM(REFER)) IN ({marcadores}) "
                    "GROUP BY LTRIM(RTRIM(REFER))", tuple(lote))
            }
            movs = sae_lectura.consultar(
                "SELECT LTRIM(RTRIM(REFER)) AS refer, "
                "LTRIM(RTRIM(ISNULL(CVE_DOC_COMPPAGO,''))) AS rep, IMPORTE AS importe, ID_MOV AS id_mov "
                f"FROM {cuen_det} WHERE TIPO_MOV='A' AND SIGNO=-1 "
                f"AND LTRIM(RTRIM(REFER)) IN ({marcadores}) "
                "ORDER BY LTRIM(RTRIM(REFER)), FECHA_APLI, ID_MOV", tuple(lote))
        corrido: dict[str, float] = {}
        parcialidad: dict[str, int] = {}
        for r in movs:
            refer = str(r.get("refer") or "").strip()
            rep = str(r.get("rep") or "").strip()
            imp = _num(r.get("importe"))
            antes = cargos.get(refer, 0.0) - corrido.get(refer, 0.0)
            corrido[refer] = corrido.get(refer, 0.0) + imp
            if rep:
                parcialidad[refer] = parcialidad.get(refer, 0) + 1
            out.setdefault(refer, []).append({
                "rep": rep, "importe": imp,
                "saldo_anterior": max(0.0, antes), "saldo_insoluto": max(0.0, antes - imp),
                "parcialidad": parcialidad.get(refer, 0) or None,
            })
    return out


def _saldos_firebird(cuen_m: str, cuen_det: str, lote: list,
                     marcadores: str) -> tuple[dict[str, float], list[dict]]:
    """Cargos y abonos desde el SAE 9. TRIM de los dos lados, como el SQL del
    10: el REFER de SAE puede traer relleno a la izquierda."""
    cargos: dict[str, float] = {}
    for r in sae_lectura.consultar(
        f"SELECT TRIM(M.REFER) AS refer, SUM(M.IMPORTE) AS cargo FROM {cuen_m} M "
        f"WHERE M.NUM_CPTO = 1 AND TRIM(M.REFER) IN ({marcadores}) "
        "GROUP BY TRIM(M.REFER)", tuple(lote)):
        cargos[sae_lectura.texto(r.get("refer"))] = _num(r.get("cargo"))
    movs = [{"refer": sae_lectura.texto(r.get("refer")), "rep": sae_lectura.texto(r.get("rep")),
             "importe": r.get("importe"), "id_mov": r.get("id_mov")}
            for r in sae_lectura.consultar(
                "SELECT TRIM(D.REFER) AS refer, D.CVE_DOC_COMPPAGO AS rep, "
                "D.IMPORTE AS importe, D.ID_MOV AS id_mov "
                f"FROM {cuen_det} D WHERE D.TIPO_MOV = 'A' AND D.SIGNO = -1 "
                f"AND TRIM(D.REFER) IN ({marcadores}) "
                "ORDER BY 1, D.FECHA_APLI, D.ID_MOV", tuple(lote))]
    return cargos, movs


def leer_reps(empresa: str, desde: dt.date) -> list[dict[str, Any]]:
    """REP emitidos o cancelados desde esa fecha, con sus renglones."""
    factg, cfdi = sae_lectura.tabla("FACTG", empresa), sae_lectura.tabla("CFDI", empresa)
    if sae_lectura.motor() == "firebird":
        # En el SAE 9 las fechas del CFDI son TEXTO ISO (VARCHAR 30): comparar
        # contra 'AAAA-MM-DD' funciona igual que en el 10. Lo demás, crudo.
        lx = sae_lectura
        cab = [{
            "cve_doc": lx.texto(r.get("cve_doc")), "serie": lx.texto(r.get("serie")),
            "folio": r.get("folio"), "cliente_sae": lx.texto(r.get("cliente_sae")),
            "fecha": lx.fecha_hora(r.get("fecha")), "status": lx.texto(r.get("status")),
            "uuid": lx.texto(r.get("uuid")), "fecha_cancela": lx.fecha_hora(r.get("fecha_cancela")),
            "forma_sat": lx.texto(r.get("forma_sat")),
        } for r in lx.consultar(
            "SELECT G.CVE_DOC AS cve_doc, G.SERIE AS serie, G.FOLIO AS folio, "
            "G.CVE_CLPV AS cliente_sae, G.FECHA_DOC AS fecha, G.STATUS AS status, "
            "C.UUID AS uuid, C.FECHA_CANCELA AS fecha_cancela, "
            f"{lx.col_o_nulo('G', factg, 'FORMADEPAGOSAT', 5)} AS forma_sat "
            f"FROM {factg} G LEFT JOIN {cfdi} C "
            "ON TRIM(C.CVE_DOC) = TRIM(G.CVE_DOC) AND C.TIPO_DOC = 'G' "
            "WHERE G.FECHA_DOC >= %s OR C.FECHA_CANCELA >= %s",
            (desde.isoformat(), desde.isoformat()))]
    else:
        cab = _reps_sqlserver(factg, cfdi, desde)
    reps = []
    for r in cab:
        cve = str(r.get("cve_doc") or "").strip()
        if not cve:
            continue
        cancelado = (str(r.get("status") or "").strip().upper() == "C"
                     or bool(str(r.get("fecha_cancela") or "").strip()))
        reps.append({
            "cve_doc": cve, "serie": re.sub(r"\s+", "", str(r.get("serie") or "")).upper(),
            "folio": int(_num(r.get("folio"))), "cliente_sae": str(r.get("cliente_sae") or "").strip(),
            "fecha": str(r.get("fecha") or "").strip(), "cancelado": cancelado,
            "uuid": str(r.get("uuid") or "").strip() or None,
            "fecha_cancela": str(r.get("fecha_cancela") or "").strip() or None,
            "forma_sat": str(r.get("forma_sat") or "").strip() or None,
            "renglones": [],
        })
    _colgar_renglones(empresa, reps, "CVE_DOC_COMPPAGO")
    return reps


def _reps_sqlserver(factg: str, cfdi: str, desde: dt.date) -> list[dict[str, Any]]:
    return sae_lectura.consultar(
        "SELECT LTRIM(RTRIM(G.CVE_DOC)) AS cve_doc, RTRIM(ISNULL(G.SERIE,'')) AS serie, "
        "G.FOLIO AS folio, LTRIM(RTRIM(G.CVE_CLPV)) AS cliente_sae, "
        "CONVERT(varchar(19), G.FECHA_DOC, 120) AS fecha, RTRIM(ISNULL(G.STATUS,'')) AS status, "
        "LTRIM(RTRIM(ISNULL(C.UUID,''))) AS uuid, "
        "LTRIM(RTRIM(ISNULL(C.FECHA_CANCELA,''))) AS fecha_cancela, "
        "RTRIM(ISNULL(G.FORMADEPAGOSAT,'')) AS forma_sat "
        f"FROM {factg} G LEFT JOIN {cfdi} C "
        "ON LTRIM(RTRIM(C.CVE_DOC))=LTRIM(RTRIM(G.CVE_DOC)) AND C.TIPO_DOC='G' "
        "WHERE G.FECHA_DOC >= %s OR C.FECHA_CANCELA >= %s",
        (desde.isoformat(), desde.isoformat()),
    )


def _colgar_renglones(empresa: str, reps: list[dict], columna: str) -> None:
    cuen_det = sae_lectura.tabla("CUEN_DET", empresa)
    docs = [p["cve_doc"] for p in reps]
    por_rep: dict[str, list] = {}
    for i in range(0, len(docs), _LOTE):
        lote = docs[i:i + _LOTE]
        if not lote:
            continue
        marcadores = ", ".join(["%s"] * len(lote))
        if sae_lectura.motor() == "firebird":
            lx = sae_lectura
            filas = [{"doc": lx.texto(r.get("doc")), "refer": lx.texto(r.get("refer")),
                      "importe": r.get("importe"), "fecha": lx.fecha_hora(r.get("fecha")),
                      "concepto": r.get("concepto")}
                     for r in lx.consultar(
                         f"SELECT TRIM(D.{columna}) AS doc, TRIM(D.REFER) AS refer, "
                         "D.IMPORTE AS importe, D.FECHA_APLI AS fecha, D.NUM_CPTO AS concepto "
                         f"FROM {cuen_det} D WHERE TRIM(D.{columna}) IN ({marcadores})",
                         tuple(lote))]
        else:
            filas = sae_lectura.consultar(
                f"SELECT LTRIM(RTRIM({columna})) AS doc, LTRIM(RTRIM(REFER)) AS refer, "
                "IMPORTE AS importe, CONVERT(varchar(19), FECHA_APLI, 120) AS fecha, "
                "NUM_CPTO AS concepto "
                f"FROM {cuen_det} WHERE LTRIM(RTRIM({columna})) IN ({marcadores})",
                tuple(lote),
            )
        for r in filas:
            por_rep.setdefault(str(r.get("doc") or "").strip(), []).append({
                "refer": str(r.get("refer") or "").strip(), "importe": _num(r.get("importe")),
                "fecha": str(r.get("fecha") or "").strip(),
                "concepto": str(r.get("concepto") or "").strip()})
    for p in reps:
        p["renglones"] = por_rep.get(p["cve_doc"], [])


def leer_notas(empresa: str, desde: dt.date) -> list[dict[str, Any]]:
    """Notas de crédito (CFDI de egreso) timbradas o canceladas desde esa fecha."""
    cfdi, cuen_det = sae_lectura.tabla("CFDI", empresa), sae_lectura.tabla("CUEN_DET", empresa)
    fb = sae_lectura.motor() == "firebird"
    if fb:
        lx = sae_lectura
        cab = [{"cve_doc": lx.texto(r.get("cve_doc")), "uuid": lx.texto(r.get("uuid")),
                "fecha": lx.fecha_hora(r.get("fecha")),
                "fecha_cancela": lx.fecha_hora(r.get("fecha_cancela")),
                "serie_folio": _serie_folio_xml(r.get("xml"))}
               for r in lx.consultar(
                   "SELECT C.CVE_DOC AS cve_doc, C.UUID AS uuid, C.FECHA_CERT AS fecha, "
                   "C.FECHA_CANCELA AS fecha_cancela, C.XML_DOC AS xml "
                   f"FROM {cfdi} C WHERE C.TIPO_DOC = 'E' "
                   "AND (C.FECHA_CERT >= %s OR C.FECHA_CANCELA >= %s)",
                   (desde.isoformat(), desde.isoformat()))]
    else:
        cab = sae_lectura.consultar(
            "SELECT LTRIM(RTRIM(C.CVE_DOC)) AS cve_doc, LTRIM(RTRIM(ISNULL(C.UUID,''))) AS uuid, "
            "LEFT(C.FECHA_CERT, 19) AS fecha, "
            "LTRIM(RTRIM(ISNULL(C.FECHA_CANCELA,''))) AS fecha_cancela "
            f"FROM {cfdi} C WHERE C.TIPO_DOC='E' AND (C.FECHA_CERT >= %s OR C.FECHA_CANCELA >= %s)",
            (desde.isoformat(), desde.isoformat()),
        )
    notas = [{"cve_doc": str(r.get("cve_doc") or "").strip(),
              "uuid": str(r.get("uuid") or "").strip() or None,
              "fecha": str(r.get("fecha") or "").strip(),
              "fecha_cancela": str(r.get("fecha_cancela") or "").strip() or None,
              "serie_folio": r.get("serie_folio"),
              "renglones": [], "cliente_sae": None}
             for r in cab if str(r.get("cve_doc") or "").strip()]
    docs = [n["cve_doc"] for n in notas]
    por_nota: dict[str, list] = {}
    for i in range(0, len(docs), _LOTE):
        lote = docs[i:i + _LOTE]
        if not lote:
            continue
        marcadores = ", ".join(["%s"] * len(lote))
        for r in sae_lectura.consultar(
            "SELECT TRIM(D.DOCTO) AS doc, TRIM(D.CVE_CLIE) AS cliente, "
            "TRIM(D.REFER) AS refer, D.IMPORTE AS importe "
            f"FROM {cuen_det} D WHERE D.NUM_CPTO = 1002 AND TRIM(D.DOCTO) IN ({marcadores})"
            if fb else
            "SELECT LTRIM(RTRIM(DOCTO)) AS doc, LTRIM(RTRIM(CVE_CLIE)) AS cliente, "
            "LTRIM(RTRIM(REFER)) AS refer, IMPORTE AS importe "
            f"FROM {cuen_det} WHERE NUM_CPTO=1002 AND LTRIM(RTRIM(DOCTO)) IN ({marcadores})",
            tuple(lote),
        ):
            por_nota.setdefault(str(r.get("doc") or "").strip(), []).append({
                "cliente": str(r.get("cliente") or "").strip(),
                "refer": str(r.get("refer") or "").strip(),
                "importe": _num(r.get("importe"))})
    for n in notas:
        n["renglones"] = por_nota.get(n["cve_doc"], [])
        if n["renglones"]:
            n["cliente_sae"] = n["renglones"][0]["cliente"]
    return notas


_XML_COMPROBANTE = re.compile(r"<(?:cfdi:)?Comprobante\b[^>]*>", re.IGNORECASE)


def _serie_folio_xml(xml: Any) -> Optional[tuple[str, int]]:
    """('NC', 12) del <cfdi:Comprobante Serie="NC" Folio="12">, o None."""
    m = _XML_COMPROBANTE.search(str(xml or ""))
    if not m:
        return None
    serie = re.search(r'\sSerie="([^"]*)"', m.group(0))
    folio = re.search(r'\sFolio="0*(\d+)"', m.group(0))
    if not folio:
        return None
    return "".join((serie.group(1) if serie else "").split()).upper(), int(folio.group(1))


def _renglon(refer: str, importe: float, saldo: Optional[dict] = None,
             series=None) -> Optional[dict]:
    sf = sae_lectura.partir_con_series(refer, series) if series else _partir(refer)
    if sf is None or not sf[1]:
        return None
    d = {"serie": sf[0], "folio": sf[1], "importe": _dinero(importe)}
    if saldo:
        d.update({"saldo_anterior": _dinero(saldo["saldo_anterior"]),
                  "saldo_insoluto": _dinero(saldo["saldo_insoluto"])})
        if saldo.get("parcialidad"):
            d["num_parcialidad"] = saldo["parcialidad"]
    return d


def sincronizar(db: Session, ctx: AuthContext, empresa: str, dias: int = 3,
                desde_minimo: Optional[dt.date] = None,
                series: Optional[list] = None, completa: bool = False) -> dict[str, Any]:
    """Una empresa: primero los REP, luego las notas de crédito.

    Reusa las MISMAS rutas de depósito que usaba el bot, por lo mismo de
    siempre: la lógica de cruce y los candados viven ahí, y duplicarlos sería
    garantizar que se separen.

    `desde_minimo` es el piso del SAE 9 (1-ene-2026): la ventana nunca empieza
    antes. Con `completa` arranca EN el piso —todo el año—: el reloj la pide
    una vez al día por empresa (y con el botón), así entran los pagos de un
    cliente que se dio de alta después, sin releer el año cada cinco minutos.
    `series` (las del SAE 9) deja partir un documento con el folio pegado
    contra las series conocidas; ver `sae_lectura.partir_con_series`.
    """
    from ..api.v1.espejo_cobranza import (NotaCreditoEspejoIn, ReciboPagoEspejoIn,
                                          nota_credito_espejo, recibo_pago_espejo)

    desde = dt.date.today() - dt.timedelta(days=max(0, int(dias)))
    permitidos = clientes_con_equivalencia(db, ctx, empresa)
    if desde_minimo is not None:
        if completa or desde < desde_minimo:
            desde = desde_minimo
        if not permitidos:
            # SAE 9 sin un solo cliente ligado: no hay a quién reflejarle nada,
            # así que ni se le pregunta a SAE (el año entero, por Tailscale).
            return {"ok": True, "empresa": empresa,
                    "pagos": {"enviados": 0, "omitidos": 0},
                    "notas_credito": {"enviados": 0, "omitidos": 0},
                    "errores": [], "descartados": [], "sin_equivalencias": True}
    placeholders = _PLACEHOLDERS.get(empresa, set())

    def _entra(cli: Optional[str]) -> bool:
        if not cli or cli in placeholders:
            return False
        return cli.upper() in permitidos

    reps = leer_reps(empresa, desde)
    notas = leer_notas(empresa, desde)
    refers = sorted({g["refer"] for p in reps for g in p["renglones"]})
    saldos = saldos_por_factura(empresa, refers) if refers else {}

    pagos = {"enviados": 0, "omitidos": 0}
    ncs = {"enviados": 0, "omitidos": 0}
    errores: list[str] = []
    # Lo que NO se pudo partir en serie y folio. No es error (un REFER raro se
    # quedaría gritando tres días seguidos), pero tampoco se calla: así se
    # perdieron los pagos de ZCH5C y MIN5C del 24 al 26-sep-2026 sin que nada
    # lo dijera.
    descartados: list[str] = []

    for p in reps:
        # Sin timbre no hay comprobante: un REP que SAE todavía no mandó al PAC
        # no existe para nadie más que para SAE.
        if not _entra(p["cliente_sae"]) or (not p["cancelado"] and not p["uuid"]):
            pagos["omitidos"] += 1
            continue
        renglones = []
        for g in p["renglones"]:
            saldo = next((s for s in saldos.get(g["refer"], []) if s["rep"] == p["cve_doc"]), None)
            d = _renglon(g["refer"], g["importe"], saldo, series)
            if d:
                renglones.append(d)
            else:
                descartados.append(f"REP {p['cve_doc']}: renglón {g['refer']!r}")
        fecha_pago = max((g["fecha"] for g in p["renglones"]), default=p["fecha"])
        forma = p["forma_sat"] or next(
            (_FORMA_POR_CONCEPTO.get(g["concepto"]) for g in p["renglones"]
             if _FORMA_POR_CONCEPTO.get(g["concepto"])), None) or "99"
        cuerpo = {
            "empresa": empresa, "cve_doc": p["cve_doc"], "serie": p["serie"],
            "folio": p["folio"], "cliente_sae": p["cliente_sae"],
            "fecha_pago": fecha_pago.replace(" ", "T"), "forma_pago": forma,
            "estado": "CANCELADO" if p["cancelado"] else "TIMBRADO",
            "uuid": p["uuid"], "facturas": renglones,
        }
        if p["fecha_cancela"]:
            cuerpo["fecha_cancelacion"] = p["fecha_cancela"][:10] + "T00:00:00"
        try:
            recibo_pago_espejo(payload=ReciboPagoEspejoIn(**cuerpo), db=db, ctx=ctx)
            pagos["enviados"] += 1
        except Exception as e:
            errores.append(f"REP {p['cve_doc']}: {type(e).__name__}: {e}")

    # Las series de nota que dieron los XML de esta misma pasada, más las
    # conocidas: si a una nota le falta su XML, su CVE_DOC pegado
    # ('NC0000000003') todavía se parte sin adivinar.
    series_nc = sorted({n["serie_folio"][0] for n in notas
                        if n.get("serie_folio") and n["serie_folio"][0]} | set(series or []))
    for n in notas:
        # El SAE 9 trae la serie y el folio del XML timbrado de la nota: es el
        # dato que el SAT tiene, sin depender de cómo SAE escriba su CVE_DOC.
        sf = n.get("serie_folio") or (sae_lectura.partir_con_series(n["cve_doc"], series_nc)
                                      if series_nc else _partir(n["cve_doc"]))
        # Una nota CANCELADA ya no tiene renglones en la CxC, y sin ellos no hay
        # cliente al cual colgarla: se cuenta y se deja. Inventarle dueño sería
        # peor que no reflejarla.
        if not _entra(n["cliente_sae"]):
            ncs["omitidos"] += 1
            continue
        if sf is None:
            ncs["omitidos"] += 1
            descartados.append(f"NC {n['cve_doc']!r}")
            continue
        renglones = []
        for g in n["renglones"]:
            d = _renglon(g["refer"], g["importe"], series=series)
            if d:
                renglones.append(d)
            else:
                descartados.append(f"NC {n['cve_doc']}: renglón {g['refer']!r}")
        cuerpo = {
            "empresa": empresa, "cve_doc": n["cve_doc"], "serie": sf[0], "folio": sf[1],
            "cliente_sae": n["cliente_sae"], "fecha": n["fecha"].replace(" ", "T"),
            "estado": "CANCELADA" if n["fecha_cancela"] else "VIGENTE",
            "uuid": n["uuid"], "facturas": renglones,
        }
        if n["fecha_cancela"]:
            cuerpo["fecha_cancelacion"] = n["fecha_cancela"][:10] + "T00:00:00"
        try:
            nota_credito_espejo(payload=NotaCreditoEspejoIn(**cuerpo), db=db, ctx=ctx)
            ncs["enviados"] += 1
        except Exception as e:
            errores.append(f"NC {n['cve_doc']}: {type(e).__name__}: {e}")

    if descartados:
        log.warning("cobranza SAE %s: %d documentos sin forma de serie y folio: %s",
                    empresa, len(descartados), "; ".join(descartados[:5]))
    return {"ok": not errores, "empresa": empresa, "pagos": pagos,
            "notas_credito": ncs, "errores": errores[:20],
            "descartados": descartados[:20]}
