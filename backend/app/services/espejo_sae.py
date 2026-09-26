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
import logging
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models.factura import Factura
from ..schemas.factura import FacturaEspejoIn, LineaFacturaEspejoIn
from . import sae_fuentes, sae_lectura
from .sae_fuentes import serie_facturador

_LOTE_PARTIDAS = 40


def _num(v) -> str:
    return str(v if v is not None else 0).strip() or "0"


def marca_de_agua(db: Session, empresa: str, serie: str) -> int:
    """El folio más alto que el espejo ya tiene de esa serie. 0 si no hay nada."""
    valor = (db.query(func.max(Factura.folio))
             .filter(Factura.origen == "ESPEJO_SAE",
                     Factura.espejo_empresa == empresa,
                     Factura.serie == serie_facturador(serie))
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
    if sae_lectura.motor() == "firebird":
        filas = _encabezados_firebird(factf, cfdi, obs, cond, params, limite)
    else:
        filas = _encabezados_sqlserver(factf, cfdi, obs, cond, params, limite)
    salida = []
    for r in filas:
        cve = str(r.get("cve_doc") or "").strip()
        if not cve:
            continue
        cancelada = (str(r.get("status") or "").strip().upper() == "C"
                     or bool(str(r.get("fecha_cancela") or "").strip()))
        uuid_f = str(r.get("uuid") or "").strip()
        salida.append({
            "cve_doc": cve, "serie": serie_facturador(r.get("serie")),
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


def _encabezados_sqlserver(factf: str, cfdi: str, obs: str, cond: list, params: list,
                           limite: int) -> list[dict[str, Any]]:
    return sae_lectura.consultar(
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


def _encabezados_firebird(factf: str, cfdi: str, obs: str, cond: list, params: list,
                          limite: int) -> list[dict[str, Any]]:
    """Los mismos encabezados desde el SAE 9 (Firebird 2.5).

    Columnas crudas y el formato en Python: así da igual si el SAE 9 guarda la
    fecha de cancelación como fecha o como texto, y una columna que no exista
    en esa versión (la sustitución de CFDI, el mensaje de cancelación) llega
    vacía en vez de tronar. Firebird compara textos sin el relleno de la
    derecha, así que el JOIN por CVE_DOC no necesita RTRIM y usa el índice.
    """
    msj = sae_lectura.col_o_nulo("C", cfdi, "MSJ_CANC", 255)
    rel = sae_lectura.col_o_nulo("C", cfdi, "UUID_REL", 60)
    crudas = sae_lectura.consultar(
        f"SELECT FIRST {int(limite)} F.CVE_DOC AS cve_doc, F.SERIE AS serie, "
        "F.FOLIO AS folio, F.CVE_CLPV AS cliente_sae, F.FECHA_DOC AS fecha, "
        "F.CAN_TOT AS subtotal, F.IMPORTE AS total, F.IMP_TOT4 AS iva, F.IMP_TOT1 AS ieps, "
        "F.STATUS AS status, C.UUID AS uuid, C.FECHA_CANCELA AS fecha_cancela, "
        f"O.STR_OBS AS observaciones, {msj} AS cancelacion_msj, {rel} AS uuid_sustitucion "
        f"FROM {factf} F "
        f"LEFT JOIN {cfdi} C ON C.CVE_DOC = F.CVE_DOC AND C.TIPO_DOC = 'F' "
        f"LEFT JOIN {obs} O ON O.CVE_OBS = F.CVE_OBS "
        f"WHERE {' AND '.join(cond)} ORDER BY F.FOLIO",
        tuple(params),
    )
    lx = sae_lectura
    return [{
        "cve_doc": lx.texto_der(r.get("cve_doc")), "serie": lx.texto(r.get("serie")),
        "folio": r.get("folio"), "cliente_sae": lx.texto(r.get("cliente_sae")),
        "fecha": lx.fecha_hora(r.get("fecha")),
        "subtotal": lx.dinero(r.get("subtotal")), "total": lx.dinero(r.get("total")),
        "iva": lx.dinero(r.get("iva")), "ieps": lx.dinero(r.get("ieps")),
        "status": lx.texto(r.get("status")), "uuid": lx.texto(r.get("uuid")),
        "fecha_cancela": lx.fecha_hora(r.get("fecha_cancela")),
        "observaciones": lx.texto(r.get("observaciones"))[:250],
        "cancelacion_msj": lx.texto(r.get("cancelacion_msj")),
        "uuid_sustitucion": lx.texto(r.get("uuid_sustitucion")),
    } for r in crudas]


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
        for r in (_partidas_firebird(par, inve, lote, marcadores)
                  if sae_lectura.motor() == "firebird" else sae_lectura.consultar(
            "SELECT RTRIM(A.CVE_DOC) AS doc, LTRIM(RTRIM(A.CVE_ART)) AS clave, "
            "CAST(CAST(A.CANT AS decimal(18,4)) AS varchar(30)) AS cantidad, "
            "CAST(CAST(A.PREC AS decimal(18,4)) AS varchar(30)) AS precio, "
            "CAST(CAST(ISNULL(A.TOT_PARTIDA,0) AS decimal(18,4)) AS varchar(30)) AS importe, "
            "RTRIM(ISNULL(I.DESCR,'')) AS descripcion "
            f"FROM {par} A LEFT JOIN {inve} I ON RTRIM(I.CVE_ART)=RTRIM(A.CVE_ART) "
            f"WHERE RTRIM(A.CVE_DOC) IN ({marcadores}) "
            "ORDER BY A.CVE_DOC, TRY_CAST(A.NUM_PAR AS INT)",
            tuple(lote),
        )):
            clave = str(r.get("clave") or "").strip()
            salida.setdefault(str(r.get("doc") or "").strip(), []).append({
                "clave": clave or None,
                "cantidad": _num(r.get("cantidad")),
                "precio_unitario": _num(r.get("precio")),
                "importe": _num(r.get("importe")),
                "descripcion": (str(r.get("descripcion") or "").strip() or clave or "PARTIDA SAE")[:1000],
            })
    return salida


def _partidas_firebird(par: str, inve: str, lote: list, marcadores: str) -> list[dict]:
    """Las partidas desde el SAE 9, con el formato que daba el SQL del 10."""
    lx = sae_lectura
    return [{
        "doc": lx.texto_der(r.get("doc")), "clave": lx.texto(r.get("clave")),
        "cantidad": lx.dinero(r.get("cantidad"), 4), "precio": lx.dinero(r.get("precio"), 4),
        "importe": lx.dinero(r.get("importe"), 4), "descripcion": lx.texto(r.get("descripcion")),
    } for r in lx.consultar(
        "SELECT A.CVE_DOC AS doc, A.CVE_ART AS clave, A.CANT AS cantidad, A.PREC AS precio, "
        "A.TOT_PARTIDA AS importe, I.DESCR AS descripcion "
        f"FROM {par} A LEFT JOIN {inve} I ON I.CVE_ART = A.CVE_ART "
        f"WHERE A.CVE_DOC IN ({marcadores}) ORDER BY A.CVE_DOC, A.NUM_PAR",
        tuple(lote))]


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
        if sae_lectura.motor() == "firebird":
            # Firebird agrupa sin el relleno de la derecha, pero por si dos
            # REFER difieren sólo en eso se suman aquí, ya limpios.
            for r in sae_lectura.consultar(
                "SELECT D.REFER AS doc, SUM(D.IMPORTE) AS abonado "
                f"FROM {cuen} D WHERE D.TIPO_MOV = 'A' AND D.SIGNO = -1 "
                f"AND D.REFER IN ({marcadores}) GROUP BY D.REFER",
                tuple(lote),
            ):
                try:
                    doc = str(r.get("doc") or "").strip()
                    abonos[doc] = abonos.get(doc, 0.0) + float(
                        sae_lectura.dinero(r.get("abonado"), 4))
                except (TypeError, ValueError):
                    continue
            continue
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
    # Las llaves son la serie COMO LA GUARDA EL FACTURADOR (sin espacios):
    # así la parte `partir_documento` y así la busca quien pregunta.
    buscadas = {serie_facturador(s) for s in series}
    out: dict[str, list[int]] = {}
    sql = (f"SELECT DISTINCT D.REFER AS doc FROM {cuen} D "
           "WHERE D.TIPO_MOV = 'A' AND D.FECHA_APLI >= %s"
           if sae_lectura.motor() == "firebird" else
           f"SELECT DISTINCT RTRIM(REFER) AS doc FROM {cuen} "
           "WHERE TIPO_MOV='A' AND FECHA_APLI >= %s")
    for r in sae_lectura.consultar(sql, (desde.isoformat(),)):
        # Se parte el documento y se compara la serie ENTERA (26-sep-2026).
        # Antes se preguntaba «¿empieza con la serie?», que depende de que
        # ninguna serie sea prefijo de otra: hoy no lo es, pero bastaba dar de
        # alta una ZEHMO junto a ZEHMOHOS para que los pagos de una se
        # perdieran detrás de la otra sin decir nada.
        partido = sae_lectura.partir_con_series(r.get("doc"), buscadas)
        if partido and partido[0] in buscadas:
            out.setdefault(partido[0], []).append(partido[1])
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
                dias_cancelaciones: int = 3, limite: int = 500,
                desde: Optional[dt.date] = None) -> dict[str, Any]:
    """Una pasada: trae lo nuevo y revisa lo que pudo haber cambiado.

    Devuelve el conteo por serie. No lanza por una factura mala: una sola
    factura rara no puede dejar sin espejo a las otras doscientas, así que su
    error se cuenta y se sigue. Lo que sí sube es un fallo de SAE, porque
    entonces la pasada entera no significa nada y decir «0 nuevas» sería
    mentir.

    `desde` es el PISO: nada con fecha anterior entra al espejo. El SAE 9 se
    espeja desde el 1-ene-2026 («este año nada más»); sin piso, la primera
    pasada de una serie nueva —marca de agua en 0— se traería años de
    historia. El SAE 10 no lleva piso: sigue exactamente como estaba.
    """
    from ..api.v1.facturas import factura_espejo   # perezoso: la ruta importa servicios

    hoy = dt.date.today()
    res: dict[str, Any] = {"empresa": empresa, "nuevas": 0, "actualizadas": 0,
                           "errores": [], "por_serie": {}}

    piso = desde
    desde = hoy - dt.timedelta(days=max(0, int(dias_cancelaciones)))
    if piso and desde < piso:
        desde = piso
    try:
        abonos_por_serie = folios_con_abonos(empresa, desde, list(series))
    except Exception as e:     # sin CxC la pasada sigue: peor es no espejar nada
        abonos_por_serie = {}
        res["errores"].append(f"abonos: {type(e).__name__}: {e}")

    for serie in series:
        # `serie` es como la escribe SAE (con espacios, si los tiene): así se
        # le pregunta a SAE. `serie_f` es como la guarda el Facturador.
        serie_f = serie_facturador(serie)
        marca = marca_de_agua(db, empresa, serie)
        cabs = leer_encabezados(empresa, serie, desde_folio=marca or None,
                                desde_fecha=piso, limite=limite)

        # LO QUE PUDO CAMBIAR: una factura ya reflejada que se cancela NO cambia
        # de folio, así que la marca de agua no la ve nunca. Se comparan los
        # estados de una ventana corta y solo se re-depositan las que difieren.
        vistos = {c["folio"] for c in cabs}
        previas = leer_encabezados(empresa, serie, desde_fecha=desde, limite=limite)
        en_espejo = {
            f.folio: (f.estado, float(f.saldo_insoluto) if f.saldo_insoluto is not None else None)
            for f in db.query(Factura).filter(
                Factura.origen == "ESPEJO_SAE", Factura.espejo_empresa == empresa,
                Factura.serie == serie_f, Factura.fecha >= desde).all()
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
        con_abono = abonos_por_serie.get(serie_f, [])
        ya = vistos | {c["folio"] for c in cambiadas}
        candidatos = [c for c in previas if c["folio"] in con_abono and c["folio"] not in ya]
        saldos_nuevos = _saldos(empresa, candidatos)
        repago = [c for c in candidatos
                  if _saldo_cambio(en_espejo.get(c["folio"], (None, None))[1],
                                   saldos_nuevos.get(c["cve_doc"]))]

        pendientes = cabs + cambiadas + repago
        if not pendientes:
            res["por_serie"][serie_f] = {"nuevas": 0, "actualizadas": 0, "marca": marca}
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
        res["por_serie"][serie_f] = {"nuevas": nuevas, "actualizadas": actualizadas,
                                     "marca": marca}
        _guardar_si_sae9(db, ctx)
    return res


def _guardar_si_sae9(db: Session, ctx: AuthContext) -> None:
    """En el SAE 9, lo depositado de cada serie se guarda al terminarla.

    Su lectura va por Tailscale a otro país: un corte de red en la serie
    tercera no puede deshacer lo que ya entró de las dos primeras (la carga
    inicial trae meses). El commit mata el GUC del tenant (`is_local`), así que
    se vuelve a armar —el mismo patrón que `_commit_seguro` de facturas—. El
    SAE 10 no pasa por aquí: su pasada sigue siendo una sola transacción.
    """
    if sae_lectura.motor() != "firebird" or db is None or not hasattr(db, "commit"):
        return
    from ..core.db import set_role_tenant

    db.commit()
    set_role_tenant(db, ctx.tenant_id)


def folios_en_sae(empresa: str, serie: str, desde: Optional[dt.date] = None) -> set[int]:
    """Todos los folios que SAE tiene de esa serie. Una columna, nada más.

    Con `desde` sólo los de esa fecha en adelante: el cuadre del SAE 9 cuenta
    contra lo que se espeja (este año), no contra toda su historia — si no,
    cada día «faltarían» cientos y el cuadre gritaría que algo se rompió.
    """
    factf = sae_lectura.tabla('FACTF', empresa)
    if sae_lectura.motor() == "firebird":
        sql, params = f"SELECT F.FOLIO AS folio FROM {factf} F WHERE F.SERIE = %s", [serie]
    else:
        sql, params = f"SELECT F.FOLIO AS folio FROM {factf} F WHERE RTRIM(F.SERIE) = %s", [serie]
    if desde:
        sql += " AND F.FECHA_DOC >= %s"
        params.append(desde.isoformat())
    return {
        int(r["folio"]) for r in sae_lectura.consultar(sql, tuple(params))
        if str(r.get("folio") or "").strip().isdigit() or isinstance(r.get("folio"), int)
    }


def descubrir_series(empresa: str, desde: Optional[dt.date]) -> list[str]:
    """Las series con facturas desde esa fecha, tal como las escribe SAE.

    Para el SAE 9 no hay una lista escrita a mano como la del 10: sus series
    se conocieron el 26-sep-2026 por los XML del disco y ésa no es una fuente
    completa. Se le pregunta a FACTF qué series usó este año. Una factura SIN
    serie no se puede reflejar (el Facturador parte todo por serie y folio),
    así que no se ofrece.
    """
    factf = sae_lectura.tabla("FACTF", empresa)
    sql, params = f"SELECT DISTINCT F.SERIE AS serie FROM {factf} F", []
    if desde:
        sql += " WHERE F.FECHA_DOC >= %s"
        params.append(desde.isoformat())
    vistas: dict[str, str] = {}
    for r in sae_lectura.consultar(sql, tuple(params)):
        # Tal cual la guarda SAE, menos el relleno de la DERECHA (que Firebird
        # ignora al comparar): un espacio doble o uno a la izquierda se
        # conservan, o `F.SERIE = ?` ya no la encontraría.
        crudo = str(r.get("serie") or "").rstrip()
        llave = serie_facturador(crudo)
        if llave and crudo.strip().upper() != "STAND." and llave not in vistas:
            vistas[llave] = crudo
    return sorted(vistas.values(), key=serie_facturador)


def cuadre(db: Session, ctx: AuthContext, empresa: str, series: list[str],
           reparar: bool = True, tope: int = 25,
           desde: Optional[dt.date] = None, parcial: bool = False) -> dict[str, Any]:
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
            en_sae = folios_en_sae(empresa, serie, desde) if desde else folios_en_sae(empresa, serie)
        except Exception as e:
            res["errores"].append(f"{serie}: {type(e).__name__}: {e}")
            continue
        en_espejo = {
            f.folio for f in db.query(Factura.folio).filter(
                Factura.origen == "ESPEJO_SAE", Factura.espejo_empresa == empresa,
                Factura.serie == serie_facturador(serie)).all()
        }
        faltan = sorted(en_sae - en_espejo)
        info = {"sae": len(en_sae), "espejo": len(en_espejo), "faltan": len(faltan),
                "folios": faltan[:50], "reparadas": 0, "omitidas": 0}
        res["faltantes"] += len(faltan)
        if faltan and reparar and parcial and len(faltan) > tope:
            # SAE 9: sus huecos son facturas de clientes que se dieron de alta
            # después de la carga, no algo roto. Se traen de a `tope` por vuelta
            # —las más viejas primero— y lo demás queda contado a la vista.
            info["reparadas"], info["omitidas"] = _traer_folios(
                db, ctx, empresa, serie, faltan[:tope], res["errores"])
            info["pendientes"] = len(faltan) - tope
            res["reparadas"] += info["reparadas"]
            res["omitidas"] += info["omitidas"]
        elif faltan and reparar and len(faltan) <= tope:
            info["reparadas"], info["omitidas"] = _traer_folios(
                db, ctx, empresa, serie, faltan, res["errores"])
            res["reparadas"] += info["reparadas"]
            res["omitidas"] += info["omitidas"]
        elif faltan and reparar:
            res["errores"].append(
                f"{serie}: faltan {len(faltan)} facturas, más del tope de {tope} — "
                f"eso no es un hueco, es que algo se rompió; no las traigo a escondidas")
        res["series"][serie_facturador(serie)] = info
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
# El catálogo de artículos cambia todavía menos y leerlo entero cuesta: su
# propio paso también (ESPEJO_SAE_CLAVES_CADA_SEG, 4 h por omisión). Vacío (o
# None) = todavía no se lee en este proceso, y entonces toca: con 0.0 el primer
# turno dependería de cuánto lleva prendida la máquina (el reloj monotónico
# cuenta desde el arranque), y tras un reinicio el catálogo esperaría hasta 4 h.
# POR TENANT: el botón de Gerardo no puede reiniciar el paso del catálogo del
# SAE 10, que es de otro tenant (revisión del 26-sep-2026).
_ultimas_claves: Optional[dict] = None
# El SAE 9 lleva su propio paso (SAE_FB_CADA_SEG), también por tenant, y su
# propio día de cuadre: si compartiera el del SAE 10, el 10 cuadra en la
# primera vuelta del día, marca «hoy», y el 9 —que no corre en esa vuelta— se
# quedaría sin cuadre casi todos los días.
_ultima_fb: Optional[dict] = None
_ultimo_cuadre_fb: dict = {}
# El catálogo del SAE 9 también con su propio paso: sólo se lee en las vueltas
# del SAE 9 (si no, con el servidor caído se reintentaría cada 30 s) y no puede
# depender de que su turno de 4 h caiga justo en una de ellas.
_ultimas_claves_fb: dict = {}
# Los errores de la última vuelta del SAE 9 de cada tenant. El SAE 9 se lee
# cada 5 min y el reporte sale cada 30 s: sin esto, su error se vería en rojo
# medio minuto y después lo taparía una pasada que ni lo intentó.
_errores_fb: dict = {}
# La cobranza del SAE 9 se relee completa (desde su piso) una vez al día por
# empresa: así entra lo que se omitió por un cliente que se dio de alta
# después, sin releer el año entero cada cinco minutos.
_cobranza_completa: dict = {}


def _marcas(valor) -> dict:
    return valor if isinstance(valor, dict) else {}


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
    """Una vuelta del reloj, para todas las empresas de todos los SAE.

    Cada vuelta trae las facturas nuevas; con su propio paso, además, el cuadre
    (una vez al día), la cobranza (REP y notas, cada 5 min) y el catálogo de
    artículos (INVE → `claves_sae`, cada 4 h y en su propia lista de empresas,
    que incluye la 05). El botón «Sincronizar SAE» fuerza los tres.

    Sin `ESPEJO_SAE_TENANT_ID` el SAE 10 no corre: no hay manera honesta de
    adivinar de quién es el espejo, y equivocarse sería escribir facturas en
    el tenant que no es. El SAE 9 lleva el tenant en cada empresa
    (`SAE_FB_EMPRESAS`) y su propio paso (`SAE_FB_CADA_SEG`, en 0 apagado).

    POR TENANT (26-sep-2026): el botón se reclama y la pasada se reporta en
    cada tenant que tiene empresas, y los pasos del catálogo y del SAE 9 son
    de cada tenant. Así Gerardo tiene su propio «SAE actualizado» y su botón
    no mueve los relojes de nadie más.
    """
    from ..core.config import settings

    global _ultimo_cuadre, _ultima_cobranza, _ultimas_claves, _ultima_fb
    import time as _time

    sae10 = (sae_fuentes.empresas_sae10()
             if sae_lectura.disponible() and settings.ESPEJO_SAE_TENANT_ID else [])
    cada_fb = int(settings.SAE_FB_CADA_SEG or 0)
    sae9 = ([e for e in sae_fuentes.empresas() if e.servidor.es_firebird
             and e.servidor.configurado()] if cada_fb > 0 else [])
    activas = sae10 + sae9
    if not activas:
        if not sae_lectura.disponible() or not settings.ESPEJO_SAE_TENANT_ID:
            return {"corrio": False, "motivo": "sin acceso a SAE o sin tenant configurado"}
        return {"corrio": False, "motivo": "sin empresas configuradas"}

    hoy = dt.date.today()
    ahora = _time.monotonic()
    cada_claves = int(settings.ESPEJO_SAE_CLAVES_CADA_SEG or 0)
    toca_cuadre = _ultimo_cuadre != hoy
    toca_cobranza = (ahora - _ultima_cobranza) >= max(60, int(settings.ESPEJO_SAE_COBRANZA_CADA_SEG))
    marcas_claves, marcas_fb = _marcas(_ultimas_claves), _marcas(_ultima_fb)
    # Un servidor del SAE 9 que no contestó en esta vuelta no se vuelve a
    # intentar con sus otras empresas: se espera a la siguiente.
    caidos: set = set()

    total: dict[str, Any] = {"corrio": True, "nuevas": 0, "actualizadas": 0, "errores": [],
                             "cuadre": None, "cobranza": None, "claves": None}
    marcar_cuadre = marcar_cobranza = False
    for tenant, emps in sae_fuentes.por_tenant(activas).items():
        t = str(tenant)
        tiene10 = any(not e.servidor.es_firebird for e in emps)
        tiene9 = any(e.servidor.es_firebird for e in emps)
        uc, uf, ucf = marcas_claves.get(t), marcas_fb.get(t), _ultimas_claves_fb.get(t)
        toca_claves = cada_claves > 0 and (uc is None or (ahora - uc) >= max(60, cada_claves))
        toca_claves_fb = cada_claves > 0 and (ucf is None or (ahora - ucf) >= max(60, cada_claves))
        toca_fb = tiene9 and (uf is None or (ahora - uf) >= max(60, cada_fb))
        # EL BOTÓN «SINCRONIZAR SAE» LO ATIENDE ESTE MISMO RELOJ (24-sep-2026).
        # Se reclama ANTES de la pasada para que lo que pide entre en esta
        # misma vuelta: es el refresco completo — cuadre, cobranza, catálogo y
        # también el SAE 9 aunque no le toque todavía.
        solicitud = _reclamar_solicitud(tenant)
        banderas = {"cuadre": toca_cuadre or bool(solicitud),
                    "cobranza": toca_cobranza or bool(solicitud),
                    "claves": (toca_claves or bool(solicitud)) and cada_claves > 0,
                    "fb": tiene9 and (toca_fb or bool(solicitud)),
                    "cuadre_fb": _ultimo_cuadre_fb.get(t) != hoy or bool(solicitud),
                    "claves_fb": (toca_claves_fb or bool(solicitud)) and cada_claves > 0}
        # Los relojes del SAE 10 se marcan cuando TOCA, como siempre —corriera
        # o no alguna empresa—, y sólo por el tenant del SAE 10: el botón de un
        # tenant que sólo tiene SAE 9 no reinicia la cobranza de nadie más.
        if tiene10:
            marcar_cuadre = marcar_cuadre or banderas["cuadre"]
            marcar_cobranza = marcar_cobranza or banderas["cobranza"]
        parcial = _pasada_del_tenant(tenant, emps, banderas, bool(solicitud), caidos)
        corrio_claves = parcial.pop("_corrio_claves", False)
        if parcial.pop("_corrio_claves_fb", False):
            _ultimas_claves_fb[t] = ahora
        corrio_fb = parcial.pop("_corrio_fb", False)
        corrio_cuadre_fb = parcial.pop("_corrio_cuadre_fb", False)
        errores_fb = parcial.pop("_errores_fb", [])
        leyo = parcial.pop("_leyo", False)
        if corrio_claves:
            marcas_claves[t] = ahora
        if corrio_fb:
            # Al TERMINAR, no al empezar: si el SAE 9 tardó (caído, carga
            # inicial), entre dos pasadas suyas quedan SAE_FB_CADA_SEG segundos
            # en que el SAE 10 corre sin esperarlo.
            marcas_fb[t] = _time.monotonic()
            _errores_fb[t] = list(errores_fb)
        elif tiene9 and _errores_fb.get(t):
            # El error del SAE 9 se queda a la vista hasta su siguiente vuelta.
            parcial["errores"] = parcial["errores"] + [
                e for e in _errores_fb[t] if e not in parcial["errores"]]
        if corrio_cuadre_fb:
            _ultimo_cuadre_fb[t] = hoy
        if solicitud:
            parcial["solicitud"] = str(solicitud)
            total["solicitud"] = str(solicitud)
        total["nuevas"] += parcial["nuevas"]
        total["actualizadas"] += parcial["actualizadas"]
        total["errores"].extend(parcial["errores"])
        for k in ("cuadre", "cobranza", "claves"):
            if parcial.get(k) is not None:
                total[k] = {**(total[k] or {}), **parcial[k]}
        # El reporte sale SIEMPRE que hay SAE 10, con o sin botón: la fecha de
        # «SAE actualizado» que pinta la UI sale de aquí, también en las pasadas
        # automáticas. Y una solicitud reclamada y nunca reportada deja la
        # pantalla «Sincronizando…» hasta que el backend la expira a la hora.
        # Un tenant de puro SAE 9 reporta cuando de verdad leyó algo: una
        # vuelta que no lo intentó no es «SAE actualizado».
        if tiene10 or solicitud or leyo:
            _reportar(tenant, solicitud, parcial)

    if marcar_cuadre:
        # se marca aunque alguna empresa haya fallado: reintentarlo en la
        # siguiente pasada sería correrlo cada 30 s el resto del día
        _ultimo_cuadre = hoy
    if marcar_cobranza:
        _ultima_cobranza = ahora
    # igual que el cuadre: una empresa caída espera a la siguiente vuelta de 4 h
    # (o al botón), no se reintenta cada 30 s
    _ultimas_claves = marcas_claves
    _ultima_fb = marcas_fb
    return total


def _pasada_del_tenant(tenant, emps: list, banderas: dict, con_boton: bool,
                       caidos: Optional[set] = None) -> dict[str, Any]:
    """Las empresas de UN tenant: facturas, cuadre, cobranza y catálogo.

    Para el SAE 10 son exactamente las llamadas de siempre, en el mismo orden;
    el SAE 9 hace las mismas, dentro de `en_empresa` y con su piso de fecha.
    """
    from ..core.rbac import tenant_session
    from . import cobranza_sae

    caidos = set() if caidos is None else caidos
    parcial: dict[str, Any] = {"corrio": True, "nuevas": 0, "actualizadas": 0, "errores": [],
                               "cuadre": None, "cobranza": None, "claves": None}
    for emp in emps:
        fb = emp.servidor.es_firebird
        if fb and not banderas["fb"]:
            continue
        if fb:
            parcial["_corrio_fb"] = parcial["_leyo"] = True
            if emp.servidor.clave in caidos:
                aviso = (f"[{emp.etiqueta}] {emp.servidor.clave} no contestó en esta vuelta; "
                         "se reintenta en la siguiente")
                parcial["errores"].append(aviso)
                parcial.setdefault("_errores_fb", []).append(aviso)
                continue
        if not emp.facturas:
            continue
        # El SAE 10 conserva su etiqueta de siempre en los errores ('[02]').
        etiqueta = emp.etiqueta if fb else emp.codigo
        antes = len(parcial["errores"])
        try:
            with sae_lectura.en_empresa(emp):
                # Sólo el SAE 9 descubre sus series: una empresa del SAE 10 sin
                # series escritas se salta, como siempre.
                series = list(emp.series) or (_series_de(emp, forzar=con_boton) if fb else [])
                if not series:
                    continue
                with tenant_session(tenant) as db:
                    ctx = contexto_de_sistema(tenant)
                    r = (sincronizar(db, ctx, emp.codigo, series, desde=emp.desde)
                         if emp.desde else sincronizar(db, ctx, emp.codigo, series))
                    # CONTAR CONTRA CONTAR, una vez al día. La marca de agua no
                    # ve un hueco por debajo de ella, así que sin esto un
                    # faltante se queda invisible para siempre — pasó con siete
                    # facturas.
                    if banderas["cuadre_fb" if fb else "cuadre"]:
                        parcial["_corrio_cuadre_fb" if fb else "_corrio_cuadre"] = True
                        # El SAE 9 repara de a poco en vez de todo-o-nada: sus
                        # huecos vienen de clientes dados de alta después, no de
                        # algo roto, y con el tope de 25 no se recuperarían nunca.
                        c = (cuadre(db, ctx, emp.codigo, series, desde=emp.desde,
                                    tope=_TOPE_CUADRE_FB, parcial=True)
                             if fb else cuadre(db, ctx, emp.codigo, series))
                        parcial["cuadre"] = parcial["cuadre"] or {}
                        parcial["cuadre"][emp.codigo] = {
                            k: c[k] for k in ("faltantes", "reparadas", "omitidas")}
                        parcial["errores"].extend(c.get("errores", []))
                    if banderas["cobranza"] and not fb:
                        cb = cobranza_sae.sincronizar(db, ctx, emp.codigo)
                        _anotar_cobranza(parcial, emp.codigo, cb)
                # La cobranza del SAE 9 va en SU transacción: un error de red a
                # media cobranza no puede deshacer las facturas ya guardadas.
                # Corre en cada vuelta suya (ya van con su paso de 5 min).
                if fb:
                    llave = (emp.servidor.clave, emp.numero)
                    completa = con_boton or _cobranza_completa.get(llave) != dt.date.today()
                    with tenant_session(tenant) as db:
                        cb = cobranza_sae.sincronizar(db, contexto_de_sistema(tenant), emp.codigo,
                                                      desde_minimo=emp.desde, series=series,
                                                      completa=completa)
                    _anotar_cobranza(parcial, emp.codigo, cb)
                    if completa:
                        _cobranza_completa[llave] = dt.date.today()
            parcial["nuevas"] += r.get("nuevas", 0)
            parcial["actualizadas"] += r.get("actualizadas", 0)
            parcial["errores"].extend(r.get("errores", []))
        except sae_lectura.SAENoDisponible as e:
            if fb:
                caidos.add(emp.servidor.clave)
            parcial["errores"].append(f"[{etiqueta}] {type(e).__name__}: {e}")
        except Exception as e:
            parcial["errores"].append(f"[{etiqueta}] {type(e).__name__}: {e}")
        if fb:
            parcial.setdefault("_errores_fb", []).extend(parcial["errores"][antes:])

    de10 = [e.codigo for e in emps if e.claves and not e.servidor.es_firebird]
    de9 = [e.codigo for e in emps if e.claves and e.servidor.es_firebird
           and banderas["fb"] and e.servidor.clave not in caidos]
    if banderas["claves"] and de10:
        parcial["_corrio_claves"] = True
        parcial["claves"] = sincronizar_claves(tenant, de10, parcial["errores"])
    if banderas.get("claves_fb") and de9:
        parcial["_corrio_claves_fb"] = parcial["_leyo"] = True
        antes = len(parcial["errores"])
        parcial["claves"] = {**(parcial["claves"] or {}),
                             **sincronizar_claves(tenant, de9, parcial["errores"])}
        parcial.setdefault("_errores_fb", []).extend(parcial["errores"][antes:])
    return parcial


# El cuadre del SAE 9 repara hasta esto por vuelta (ver `cuadre(parcial=True)`).
_TOPE_CUADRE_FB = 200


def _anotar_cobranza(parcial: dict, empresa: str, cb: dict) -> None:
    parcial["cobranza"] = parcial["cobranza"] or {}
    parcial["cobranza"][empresa] = {"pagos": cb["pagos"]["enviados"],
                                    "notas": cb["notas_credito"]["enviados"],
                                    "descartados": len(cb.get("descartados") or [])}
    parcial["errores"].extend(cb.get("errores", []))


# Las series del SAE 9 se descubren en FACTF y se guardan un rato: preguntarlo
# en cada vuelta es una consulta de más por empresa, y una serie nueva no nace
# cada cinco minutos. El botón las vuelve a preguntar.
_SERIES_TTL_SEG = 6 * 3600
_series_cache: dict[tuple, tuple[float, list[str]]] = {}


def _series_de(emp, forzar: bool = False) -> list[str]:
    import time as _time

    llave = (emp.servidor.clave, emp.numero)
    guardado = _series_cache.get(llave)
    if guardado and not forzar and (_time.monotonic() - guardado[0]) < _SERIES_TTL_SEG:
        return guardado[1]
    series = descubrir_series(emp.codigo, emp.desde)
    _series_cache[llave] = (_time.monotonic(), series)
    return series


def _lista(valor: Any) -> list[str]:
    """'02, 03,,04' -> ['02', '03', '04']."""
    return [e.strip() for e in str(valor or "").split(",") if e.strip()]


def sincronizar_claves(tenant_id, empresas: list[str], errores: list) -> dict[str, Any]:
    """El catálogo de artículos de cada empresa: INVE de SAE → `claves_sae`.

    Lo hacía el bot (`sync_claves_sae.py`, launchd a las 7:30 y 15:30) y lo
    mandaba por HTTP; desde el 26-sep-2026 lo lee el Facturador. La lógica del
    depósito es LA MISMA que la de la ruta (`claves_sae.reemplazar_catalogo`).

    CADA EMPRESA ES SU PROPIA TRANSACCIÓN: si la 05 no contesta, la 02 ya quedó
    al día. Y tres cosas que NO se hacen nunca desde aquí:

      · Vaciar el catálogo por una lectura fallida: SAE caído lanza ANTES de
        tocar la tabla, y un INVE vacío se rechaza como lectura mala.
      · Forzar el candado de «encoge a menos de la mitad»: se reporta en los
        errores de la pasada y lo decide una persona.
      · Tragarse el error: va a `errores`, que es lo que pinta la fecha de
        «SAE actualizado» en rojo.
    """
    from ..core.rbac import tenant_session
    from . import claves_sae

    out: dict[str, Any] = {}
    for empresa in empresas:
        try:
            # Una empresa del SAE 9 se lee de SU archivo; una del 10 (o una que
            # el registro no conoce) sigue el camino de siempre.
            with sae_lectura.en_empresa(sae_fuentes.empresa_de(tenant_id, empresa)), \
                    tenant_session(tenant_id) as db:
                r = claves_sae.sincronizar_catalogo(db, tenant_id, empresa)
            out[empresa] = {"recibidas": r.recibidas, "creadas": r.creadas,
                            "actualizadas": r.actualizadas, "eliminadas": r.eliminadas}
        except Exception as e:  # una empresa no tumba a las demás
            out[empresa] = {"error": f"{type(e).__name__}: {e}"[:300]}
            errores.append(f"[claves {empresa}] {type(e).__name__}: {e}")
    return out


def _reclamar_solicitud(tenant_id) -> Optional[Any]:
    """¿Alguien presionó «Sincronizar SAE»? Reclamarla la marca EN_CURSO."""
    from ..api.v1.facturas import reclamar_espejo_sync
    from ..core.rbac import tenant_session

    try:
        with tenant_session(tenant_id) as db:
            sol = reclamar_espejo_sync(db=db, ctx=contexto_de_sistema(tenant_id))
            # el id se lee DENTRO de la sesión: afuera el objeto queda suelto
            return getattr(sol, "id", None) if sol else None
    except Exception as e:
        logging.getLogger(__name__).warning(
            "espejo SAE: no pude reclamar la solicitud (%s: %s)", type(e).__name__, e)
        return None


def _reportar(tenant_id, solicitud, total: dict) -> None:
    """Cierra la solicitud del botón, o registra la pasada automática."""
    from ..api.v1.facturas import reportar_espejo_sync
    from ..core.rbac import tenant_session
    from ..schemas.factura import EspejoSyncReporteIn

    try:
        resumen = {k: total.get(k) for k in ("nuevas", "actualizadas", "cuadre", "cobranza",
                                             "claves")}
        with tenant_session(tenant_id) as db:
            reportar_espejo_sync(
                payload=EspejoSyncReporteIn(solicitud_id=solicitud,
                                            ok=not total.get("errores"),
                                            resultado=resumen),
                db=db, ctx=contexto_de_sistema(tenant_id))
    except Exception as e:
        # SE ESCRIBE, no se traga. De este reporte sale la fecha de «SAE
        # actualizado» que la UI pinta: si falla en silencio, la pantalla se
        # queda con una fecha vieja y nadie se entera de que el espejo lleva
        # horas sin reportar. Un espejo que no reporta es divergencia callada.
        logging.getLogger(__name__).warning(
            "espejo SAE: no pude reportar la pasada (%s: %s)", type(e).__name__, e)


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
            if r.get("claves"):
                log.info("espejo SAE · catálogo de artículos: %s", r["claves"])
            for e in (r.get("errores") or [])[:3]:
                log.warning("espejo SAE: %s", e)
        except Exception as e:
            log.exception("espejo SAE: la pasada falló (%s)", type(e).__name__)
