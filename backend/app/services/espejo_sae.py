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


def leer_abonos(empresa: str, cve_docs: list[str]) -> dict[str, float]:
    """{cve_doc: total abonado} desde CxC (CUEN_DETxx, TIPO_MOV='A').

    Es la fuente correcta del saldo: los REP (FACTGxx) traen IMPORTE=0 —los
    importes reales del complemento viven en el XML— pero CxC ya los tiene
    aplicados factura por factura, con REFER = CVE_DOC. El saldo del espejo es
    el total de la factura menos lo abonado aquí.
    """
    cuen = sae_lectura.tabla("CUEN_DET", empresa)
    abonos: dict[str, float] = {}
    for i in range(0, len(cve_docs), _LOTE_PARTIDAS):
        lote = [d for d in cve_docs[i:i + _LOTE_PARTIDAS] if d]
        if not lote:
            continue
        marcadores = ", ".join(["%s"] * len(lote))
        for r in sae_lectura.consultar(
            "SELECT RTRIM(REFER) AS doc, "
            "CAST(CAST(SUM(IMPORTE) AS decimal(18,4)) AS varchar(30)) AS abonado "
            f"FROM {cuen} WHERE TIPO_MOV='A' AND SIGNO=-1 "
            f"AND RTRIM(REFER) IN ({marcadores}) GROUP BY RTRIM(REFER)",
            tuple(lote),
        ):
            try:
                abonos[str(r.get("doc") or "").strip()] = float(r.get("abonado") or 0)
            except (TypeError, ValueError):
                continue
    return abonos


def folios_con_abonos(empresa: str, desde: dt.date, series: list[str]) -> dict[str, list[int]]:
    """{serie: [folios]} de facturas que recibieron un ABONO desde esa fecha.

    Un pago llega DÍAS después de la factura, así que la ventana por fecha del
    documento no lo ve nunca. Esto trae de vuelta las facturas viejas cuyo
    saldo cambió — sin esto, el estado de cuenta sigue cobrando lo ya pagado.
    """
    cuen = sae_lectura.tabla("CUEN_DET", empresa)
    out: dict[str, list[int]] = {}
    for r in sae_lectura.consultar(
        f"SELECT DISTINCT RTRIM(REFER) AS doc FROM {cuen} "
        "WHERE TIPO_MOV='A' AND FECHA_APLI >= %s",
        (desde.isoformat(),),
    ):
        doc = str(r.get("doc") or "").strip()
        for serie in series:
            if doc.startswith(serie):
                resto = doc[len(serie):].strip()
                if resto.isdigit():
                    out.setdefault(serie, []).append(int(resto))
                break
    return out


def como_payload(empresa: str, cab: dict, partidas: list[dict],
                 saldo: Optional[float] = None) -> FacturaEspejoIn:
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
        saldo_insoluto=saldo,
        lineas=[LineaFacturaEspejoIn(**p) for p in partidas],
    )


def _saldos(empresa: str, cabs: list[dict]) -> dict[str, float]:
    """{cve_doc: saldo} = total menos lo abonado, para esos encabezados."""
    if not cabs:
        return {}
    abonado = leer_abonos(empresa, [c["cve_doc"] for c in cabs])
    out: dict[str, float] = {}
    for c in cabs:
        pagado = abonado.get(c["cve_doc"])
        if pagado is None:
            continue
        try:
            out[c["cve_doc"]] = max(0.0, round(float(c["total"]) - float(pagado), 2))
        except (TypeError, ValueError):
            continue
    return out


def _saldo_cambio(guardado: Optional[float], nuevo: Optional[float]) -> bool:
    """¿Vale la pena reescribir? Un centavo sí; el mismo número, no.

    `nuevo` en None es «CxC no reporta abonos de esta factura»: no es cero, es
    que no hay información, y no se usa para pisar lo guardado.
    """
    if nuevo is None:
        return False
    if guardado is None:
        return True
    return abs(float(guardado) - float(nuevo)) > 0.005


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
    try:
        abonos_por_serie = folios_con_abonos(empresa, desde, list(series))
    except Exception as e:     # sin CxC la pasada sigue: peor es no espejar nada
        abonos_por_serie = {}
        res["errores"].append(f"abonos: {type(e).__name__}: {e}")

    for serie in series:
        marca = marca_de_agua(db, empresa, serie)
        cabs = leer_encabezados(empresa, serie, desde_folio=marca or None, limite=limite)

        # LO QUE PUDO CAMBIAR: una factura ya reflejada que se cancela NO cambia
        # de folio, así que la marca de agua no la ve nunca. Se comparan los
        # estados de una ventana corta y solo se re-depositan las que difieren.
        vistos = {c["folio"] for c in cabs}
        previas = leer_encabezados(empresa, serie, desde_fecha=desde, limite=limite)
        en_espejo = {
            f.folio: (f.estado, float(f.saldo_insoluto) if f.saldo_insoluto is not None else None)
            for f in db.query(Factura).filter(
                Factura.origen == "ESPEJO_SAE", Factura.espejo_empresa == empresa,
                Factura.serie == serie, Factura.fecha >= desde).all()
        }
        cambiadas = [c for c in previas
                     if c["folio"] not in vistos
                     and c["folio"] in en_espejo
                     and en_espejo[c["folio"]][0] != c["estado"]]

        # EL PAGO LLEGA DÍAS DESPUÉS DE LA FACTURA, así que ninguna ventana por
        # fecha del documento lo ve. Sin esto el estado de cuenta sigue cobrando
        # lo ya pagado.
        #
        # PERO SOLO SE REESCRIBE LO QUE DE VERDAD CAMBIÓ. Con «tiene abono
        # reciente» como único criterio, cada pasada volvía a depositar las
        # mismas facturas: a 30 segundos son 2,880 escrituras al día para no
        # cambiar nada, con el backend rehaciendo partidas y bloqueando
        # remisiones en cada una. Es la misma lección que el espejo del bot
        # aprendió con su caché de huellas, y aquí se paga comparando el saldo.
        con_abono = abonos_por_serie.get(serie, [])
        ya = vistos | {c["folio"] for c in cambiadas}
        candidatos = [c for c in previas if c["folio"] in con_abono and c["folio"] not in ya]
        saldos_nuevos = _saldos(empresa, candidatos)
        repago = [c for c in candidatos
                  if _saldo_cambio(en_espejo.get(c["folio"], (None, None))[1],
                                   saldos_nuevos.get(c["cve_doc"]))]

        pendientes = cabs + cambiadas + repago
        if not pendientes:
            res["por_serie"][serie] = {"nuevas": 0, "actualizadas": 0, "marca": marca}
            continue

        docs = [c["cve_doc"] for c in pendientes]
        partidas = leer_partidas(empresa, docs)
        abonado = leer_abonos(empresa, docs)   # para las nuevas; las de repago ya se midieron
        nuevas = actualizadas = 0
        for cab in pendientes:
            try:
                pagado = abonado.get(cab["cve_doc"])
                saldo = None
                if pagado is not None:
                    try:
                        saldo = max(0.0, round(float(cab["total"]) - float(pagado), 2))
                    except (TypeError, ValueError):
                        saldo = None
                factura_espejo(payload=como_payload(empresa, cab,
                                                    partidas.get(cab["cve_doc"], []), saldo),
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


def folios_en_sae(empresa: str, serie: str) -> set[int]:
    """Todos los folios que SAE tiene de esa serie. Una columna, nada más."""
    return {
        int(r["folio"]) for r in sae_lectura.consultar(
            f"SELECT F.FOLIO AS folio FROM {sae_lectura.tabla('FACTF', empresa)} F "
            "WHERE RTRIM(F.SERIE) = %s", (serie,))
        if str(r.get("folio") or "").strip().isdigit() or isinstance(r.get("folio"), int)
    }


def cuadre(db: Session, ctx: AuthContext, empresa: str, series: list[str],
           reparar: bool = True, tope: int = 25) -> dict[str, Any]:
    """Contar contra contar: ¿el espejo tiene TODAS las facturas de SAE?

    LA MARCA DE AGUA NO PUEDE VER UN HUECO. Pide lo posterior al folio más
    alto, así que cualquier factura que se haya perdido por debajo queda
    congelada para siempre. Se descubrió el 24-sep-2026: siete facturas que
    existían en SAE y nunca llegaron al espejo —ZHGO 62 a 67 y ZMAFAN 131—,
    invisibles para toda la maquinaria porque nadie contaba.

    Es una consulta de una columna por serie, así que cuesta poco y puede
    correr una vez al día.

    REPARA HASTA `tope` y NO MÁS. Si faltan cinco, las trae; si faltan
    trescientas, eso no es un hueco, es que algo se rompió, y traerlas a
    escondidas taparía el problema en vez de mostrarlo.
    """
    res: dict[str, Any] = {"empresa": empresa, "series": {}, "faltantes": 0,
                           "reparadas": 0, "omitidas": 0, "errores": []}
    for serie in series:
        try:
            en_sae = folios_en_sae(empresa, serie)
        except Exception as e:
            res["errores"].append(f"{serie}: {type(e).__name__}: {e}")
            continue
        en_espejo = {
            f.folio for f in db.query(Factura.folio).filter(
                Factura.origen == "ESPEJO_SAE", Factura.espejo_empresa == empresa,
                Factura.serie == serie).all()
        }
        faltan = sorted(en_sae - en_espejo)
        info = {"sae": len(en_sae), "espejo": len(en_espejo), "faltan": len(faltan),
                "folios": faltan[:50], "reparadas": 0, "omitidas": 0}
        res["faltantes"] += len(faltan)
        if faltan and reparar and len(faltan) <= tope:
            info["reparadas"], info["omitidas"] = _traer_folios(
                db, ctx, empresa, serie, faltan, res["errores"])
            res["reparadas"] += info["reparadas"]
            res["omitidas"] += info["omitidas"]
        elif faltan and reparar:
            res["errores"].append(
                f"{serie}: faltan {len(faltan)} facturas, más del tope de {tope} — "
                f"eso no es un hueco, es que algo se rompió; no las traigo a escondidas")
        res["series"][serie] = info
    return res


def _traer_folios(db: Session, ctx: AuthContext, empresa: str, serie: str,
                  folios: list[int], errores: list) -> tuple[int, int]:
    """Deposita esos folios, uno por uno. Devuelve (traídas, omitidas).

    OMITIDA NO ES ERROR. Una factura de un cliente sin equivalencia en el
    Facturador no se puede reflejar —el depósito la rechaza a propósito, para
    no adivinar de quién es— y eso no va a cambiar mañana. Contarla como error
    todos los días entrena al equipo a ignorar el reporte; contarla aparte deja
    el hueco visible sin gritar. Caso real: ZMAFAN 131, de un cliente cuyo
    contrato terminó y cuya factura además está en proceso de cancelación.
    """
    from ..api.v1.facturas import factura_espejo

    hechas = omitidas = 0
    for folio in folios:
        try:
            cabs = leer_encabezados(empresa, serie, desde_folio=folio - 1, limite=1)
            cab = next((c for c in cabs if c["folio"] == folio), None)
            if not cab:
                continue
            partidas = leer_partidas(empresa, [cab["cve_doc"]])
            factura_espejo(payload=como_payload(empresa, cab,
                                                partidas.get(cab["cve_doc"], [])),
                           db=db, ctx=ctx)
            hechas += 1
        except Exception as e:
            if "equivalencia" in str(e).lower():
                omitidas += 1
                continue
            errores.append(f"{serie} {folio}: {type(e).__name__}: {e}")
    return hechas, omitidas


# ─── El reloj ────────────────────────────────────────────────────────────────

# Última fecha en que se contó contra SAE. En memoria a propósito: si el
# proceso se reinicia, el cuadre vuelve a correr, que es el lado seguro.
_ultimo_cuadre: Optional[dt.date] = None
# Los REP llegan de a poco, no cada medio minuto: correrlos en cada vuelta
# sería pedirle a SAE cuatro consultas para nada. Se lleva su propio paso.
_ultima_cobranza: float = 0.0

def contexto_de_sistema(tenant_id) -> AuthContext:
    """Quien corre la pasada automática: nadie. No hay usuario al que atribuir.

    Es el mismo caso que ya contempla AuthContext para las conexiones (el bot),
    con `user_id=None`: lo que escriba esta pasada no queda firmado por una
    persona, porque ninguna lo pidió.
    """
    return AuthContext(
        user_id=None, auth_user_id="sistema:espejo-sae", email=None,
        tenant_id=tenant_id, role_id=None, role_name="sistema",
        is_owner=False, permissions={"factura:espejo"},
    )


def pasada_programada() -> dict[str, Any]:
    """Una vuelta del reloj, para todas las empresas configuradas.

    Sin `ESPEJO_SAE_TENANT_ID` no corre: no hay manera honesta de adivinar de
    quién es el espejo, y equivocarse sería escribir facturas en el tenant que
    no es.
    """
    from ..core.config import settings
    from ..core.rbac import tenant_session

    if not sae_lectura.disponible() or not settings.ESPEJO_SAE_TENANT_ID:
        return {"corrio": False, "motivo": "sin acceso a SAE o sin tenant configurado"}
    empresas = [e.strip() for e in str(settings.ESPEJO_SAE_EMPRESAS or "").split(",") if e.strip()]
    if not empresas:
        return {"corrio": False, "motivo": "sin empresas configuradas"}

    from ..api.v1.sae import _SERIES_POR_EMPRESA
    global _ultimo_cuadre, _ultima_cobranza
    import time as _time

    hoy = dt.date.today()
    toca_cuadre = _ultimo_cuadre != hoy
    ahora = _time.monotonic()
    toca_cobranza = (ahora - _ultima_cobranza) >= max(60, int(settings.ESPEJO_SAE_COBRANZA_CADA_SEG))
    total = {"corrio": True, "nuevas": 0, "actualizadas": 0, "errores": [],
             "cuadre": None, "cobranza": None}
    for empresa in empresas:
        series = list(_SERIES_POR_EMPRESA.get(empresa, ()))
        if not series:
            continue
        try:
            with tenant_session(settings.ESPEJO_SAE_TENANT_ID) as db:
                ctx = contexto_de_sistema(settings.ESPEJO_SAE_TENANT_ID)
                r = sincronizar(db, ctx, empresa, series)
                # CONTAR CONTRA CONTAR, una vez al día. La marca de agua no ve
                # un hueco por debajo de ella, así que sin esto un faltante se
                # queda invisible para siempre — pasó con siete facturas.
                if toca_cuadre:
                    c = cuadre(db, ctx, empresa, series)
                    total["cuadre"] = total["cuadre"] or {}
                    total["cuadre"][empresa] = {k: c[k] for k in ("faltantes", "reparadas", "omitidas")}
                    total["errores"].extend(c.get("errores", []))
                if toca_cobranza:
                    from . import cobranza_sae
                    cb = cobranza_sae.sincronizar(db, ctx, empresa)
                    total["cobranza"] = total["cobranza"] or {}
                    total["cobranza"][empresa] = {"pagos": cb["pagos"]["enviados"],
                                                  "notas": cb["notas_credito"]["enviados"]}
                    total["errores"].extend(cb.get("errores", []))
            total["nuevas"] += r.get("nuevas", 0)
            total["actualizadas"] += r.get("actualizadas", 0)
            total["errores"].extend(r.get("errores", []))
        except Exception as e:
            total["errores"].append(f"[{empresa}] {type(e).__name__}: {e}")
    if toca_cuadre:
        # se marca aunque alguna empresa haya fallado: reintentarlo en la
        # siguiente pasada sería correrlo cada 30 s el resto del día
        _ultimo_cuadre = hoy
    if toca_cobranza:
        _ultima_cobranza = ahora
    return total


async def reloj(intervalo: int) -> None:
    """Corre una pasada cada `intervalo` segundos, para siempre.

    Vive en el proceso del API porque uvicorn arranca UNO solo (ver el CMD de
    la imagen): no hay dos relojes compitiendo. Si algún día se le ponen
    workers, esto necesita un candado compartido antes que nada.

    La pasada es sincrónica y toca la red, así que va a un hilo: bloquear el
    bucle de eventos dejaría al API sin contestar durante cada vuelta. Y
    NUNCA deja morir el bucle: un error se escribe y se sigue, porque un reloj
    que se detiene en silencio es divergencia callada — la misma lección que
    dejó el espejo del bot cuando murió a medias y nadie lo supo en 12 horas.
    """
    import asyncio
    import logging

    log = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(max(5, int(intervalo)))
        try:
            r = await asyncio.to_thread(pasada_programada)
            if r.get("nuevas") or r.get("actualizadas"):
                log.info("espejo SAE: %s nuevas, %s actualizadas",
                         r.get("nuevas"), r.get("actualizadas"))
            if r.get("cuadre"):
                log.info("espejo SAE · cuadre del día: %s", r["cuadre"])
            if r.get("cobranza") and any(v.get("pagos") or v.get("notas")
                                         for v in r["cobranza"].values()):
                log.info("espejo SAE · cobranza: %s", r["cobranza"])
            for e in (r.get("errores") or [])[:3]:
                log.warning("espejo SAE: %s", e)
        except Exception as e:
            log.exception("espejo SAE: la pasada falló (%s)", type(e).__name__)
