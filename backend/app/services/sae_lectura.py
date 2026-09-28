"""Lectura DIRECTA de Aspel SAE desde el Facturador.

Hasta hoy el único que hablaba con SAE era el bot de WhatsApp, y por eso el
espejo de facturas corre allá: «el bot ya tiene el acceso probado a la BD de
SAE» (facturador_espejo.py). Eso obligaba a que toda pregunta que EXIGE el dato
de este momento —«¿esta orden ya tiene factura AHORA?»— saliera del bot por su
propia puerta, y no del Facturador, que es de donde el dueño quiere que salga
todo (24-sep-2026).

TRES CANDADOS, y ninguno es decorativo:

1. SOLO LECTURA. `consultar()` rechaza cualquier cosa que no sea un único
   SELECT, y el usuario con el que entra no puede escribir. Escribir en SAE
   también lo hace el Facturador desde el 26-sep-2026, pero por OTRA puerta
   (`sae_escritura`, con su propio usuario y su cola), porque ahí vive la regla
   que lo gobierna: una escritura a SAE NUNCA se reintenta —un INSERT repetido
   duplica un artículo o una factura— y esa garantía se sostiene teniendo UN
   SOLO escritor. Mezclar las dos puertas en una haría que cualquier lectura
   pudiera, por un error, escribir.

2. NADA DE SQL DEL LLAMADOR. Las consultas se arman aquí; lo que viene de
   afuera viaja como PARÁMETRO, nunca concatenado. Esto no es una pasarela de
   SQL con URL bonita.

3. SIN CONFIGURACIÓN NO HAY PUERTA. Si faltan las variables, `disponible()`
   dice que no y quien llama contesta «no tengo acceso a SAE» en vez de
   tronar. Un despliegue sin `SAE_SERVER` se comporta exactamente como ayer.

La empresa viaja en el nombre de la tabla (FACTF02, FACTF03…), que es como
Aspel separa las empresas, así que se valida contra una lista blanca: son dos
dígitos y nada más.

DOS SAE (26-sep-2026). Además del SAE 10 (SQL Server) hay un SAE 9 en
Firebird, en otro servidor. Quien quiera leer una empresa del SAE 9 abre
`en_empresa(emp)`: dentro de ese bloque `tabla()` usa el número de Aspel de
ESA empresa (su código en el Facturador es otro: 01 → 91), `consultar()` va a
su archivo .FDB y `motor()` dice "firebird" para que cada lector use su SQL.
Fuera del bloque todo es exactamente como antes: el SAE 10 no se entera.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import re
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from app.core.config import settings

_EMPRESAS_VALIDAS = re.compile(r"^\d{2}$")
_SOLO_SELECT = re.compile(r"^\s*SELECT\s", re.IGNORECASE)


class SAENoDisponible(RuntimeError):
    """No hay acceso a SAE: sin configuración, sin driver o sin red."""


class _EmpresaActiva:
    """La empresa del SAE 9 que se está leyendo, con UNA conexión para todo el
    bloque: cada conexión a Firebird por Tailscale cuesta medio segundo, y una
    pasada hace una docena de consultas."""

    def __init__(self, emp):
        self.emp = emp
        self.conexion = None
        self.columnas: dict[str, set[str]] = {}
        # Si conectar falló, el bloque ya no vuelve a intentarlo: cada intento
        # contra un servidor caído cuesta segundos, y una pasada hace una
        # docena de consultas — el SAE 10 esperaría detrás.
        self.caida: Optional[SAENoDisponible] = None

    def cerrar(self) -> None:
        if self.conexion is not None:
            try:
                self.conexion.close()
            except Exception:
                pass
            self.conexion = None


_ACTIVA: contextvars.ContextVar[Optional[_EmpresaActiva]] = \
    contextvars.ContextVar("sae_empresa_activa", default=None)


@contextmanager
def en_empresa(emp):
    """Lo que se lea dentro de este bloque sale de la empresa `emp`.

    Con una empresa del SAE 10 —o con None— no hace NADA: el SAE 10 sigue su
    camino de siempre. Sólo el SAE 9 (Firebird) necesita el bloque.
    """
    if emp is None or not getattr(emp.servidor, "es_firebird", False):
        yield
        return
    activa = _EmpresaActiva(emp)
    token = _ACTIVA.set(activa)
    try:
        yield
    finally:
        activa.cerrar()
        _ACTIVA.reset(token)


def motor() -> str:
    """"firebird" dentro de `en_empresa` de una empresa del SAE 9; si no, "mssql"."""
    return "firebird" if _ACTIVA.get() is not None else "mssql"


def disponible() -> bool:
    """¿Está configurada la puerta? No prueba la red: eso cuesta segundos."""
    activa = _ACTIVA.get()
    if activa is not None:
        return activa.emp.servidor.configurado()
    return bool(settings.SAE_SERVER and settings.SAE_USER and settings.SAE_PASSWORD)


def tabla(nombre: str, empresa: str) -> str:
    """`FACTF` + `03` -> `FACTF03`, con la empresa validada.

    La empresa NO puede venir cruda a una cadena de SQL: es lo único que se
    concatena, así que es lo único que hay que blindar.

    Dentro de `en_empresa` la empresa que llega es el CÓDIGO del Facturador
    (91) y la tabla lleva el NÚMERO de Aspel (FACTF01). Pedir otra empresa
    dentro del bloque es un error, no una traducción: leería la tabla de una
    empresa desde el archivo de otra.
    """
    emp = str(empresa or "").strip()
    if not _EMPRESAS_VALIDAS.match(emp):
        raise ValueError(f"empresa de SAE inválida: {empresa!r}")
    if not re.fullmatch(r"[A-Z_]+", nombre or ""):
        raise ValueError(f"tabla de SAE inválida: {nombre!r}")
    activa = _ACTIVA.get()
    if activa is not None:
        if emp != activa.emp.codigo:
            raise ValueError(f"la empresa {emp} no es la que se está leyendo "
                             f"({activa.emp.etiqueta})")
        emp = activa.emp.numero
        if not _EMPRESAS_VALIDAS.match(emp):
            raise ValueError(f"número de empresa de SAE inválido: {emp!r}")
    return f"{nombre}{emp}"


def consultar(sql: str, parametros: tuple = (), timeout: Optional[int] = None) -> list[dict[str, Any]]:
    """Un SELECT contra SAE. Devuelve filas como diccionarios.

    Lanza `SAENoDisponible` cuando no hay puerta; deja pasar el error del
    driver cuando la consulta está mal, que es un bug y tiene que verse.
    """
    if not disponible():
        raise SAENoDisponible("el Facturador no tiene configurado el acceso a SAE")
    if not _SOLO_SELECT.match(sql or "") or ";" in (sql or ""):
        raise ValueError("solo se permite un SELECT, sin sentencias encadenadas")
    activa = _ACTIVA.get()
    if activa is not None:
        return _consultar_firebird(activa, sql, parametros, timeout)
    try:
        import pymssql  # se importa aquí: sin acceso a SAE el backend no lo necesita
    except ImportError as e:  # pragma: no cover - depende de la imagen
        raise SAENoDisponible(f"falta el driver de SQL Server: {e}") from e

    servidor, _, puerto = str(settings.SAE_SERVER).partition(",")
    conexion = pymssql.connect(
        server=servidor.strip(),
        port=int(puerto or 1433),
        user=settings.SAE_USER,
        password=settings.SAE_PASSWORD,
        database=settings.SAE_DATABASE,
        timeout=int(timeout or settings.SAE_TIMEOUT),
        login_timeout=int(settings.SAE_TIMEOUT),
        as_dict=True,
    )
    try:
        with conexion.cursor(as_dict=True) as cur:
            cur.execute(sql, parametros)
            return list(cur.fetchall() or [])
    finally:
        conexion.close()


# ─── Firebird (SAE 9) ────────────────────────────────────────────────────────

def _conectar_firebird(activa: _EmpresaActiva, timeout: Optional[int]):
    try:
        import firebirdsql  # aquí y no arriba: sin SAE 9 el backend no lo necesita
    except ImportError as e:  # pragma: no cover - depende de la imagen
        raise SAENoDisponible(f"falta el driver de Firebird: {e}") from e
    srv = activa.emp.servidor
    # Una sonda TCP corta antes del driver: con el servidor caído, o con
    # Tailscale arriba pero el puerto bloqueado, el driver esperaría su timeout
    # completo en cada intento. Cinco segundos dicen «no está» igual de bien.
    import socket
    try:
        socket.create_connection((srv.host, int(srv.puerto or 3050)),
                                 timeout=min(5, int(timeout or srv.timeout or 30))).close()
    except OSError as e:
        raise SAENoDisponible(f"{activa.emp.etiqueta}: {srv.clave} no contesta en "
                              f"{srv.host}:{srv.puerto} ({type(e).__name__}: {e})") from e
    try:
        return firebirdsql.connect(
            host=srv.host, port=int(srv.puerto or 3050),
            database=srv.ruta_de(activa.emp.numero),
            user=srv.usuario, password=srv.password, charset=srv.charset,
            auth_plugin_name=srv.auth or None, wire_crypt=False,
            timeout=int(timeout or srv.timeout or 30),
            # READ ONLY + READ COMMITTED (+ rec_version): leer no detiene la
            # limpieza de versiones viejas que hace Firebird, así que el SAE que
            # la gente usa por TSplus no se alenta mientras el espejo lee. Y es
            # el SERVIDOR el que rechaza cualquier escritura en esa transacción:
            # un tercer candado, además del SELECT único y del usuario.
            isolation_level=getattr(firebirdsql, "ISOLATION_LEVEL_READ_COMMITED_RO", 4),
        )
    except SAENoDisponible:
        raise
    except Exception as e:
        # Sin red o sin permiso no es un bug de la consulta: es «no hay SAE».
        raise SAENoDisponible(f"{activa.emp.etiqueta}: no pude conectar "
                              f"({type(e).__name__}: {e})") from e


def _valor_firebird(v: Any, charset: str) -> Any:
    """Bytes (un BLOB sin subtipo de texto) se leen con el charset de la base."""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode(_CODEC.get(charset.upper(), "cp1252"), errors="replace")
    return v


_CODEC = {"WIN1252": "cp1252", "ISO8859_1": "latin-1", "UTF8": "utf-8", "NONE": "cp1252"}


def _consultar_firebird(activa: _EmpresaActiva, sql: str, parametros: tuple,
                        timeout: Optional[int]) -> list[dict[str, Any]]:
    """El mismo contrato que la de SQL Server: filas como diccionarios.

    Los marcadores se escriben `%s` en todos lados (así los tiene pymssql) y
    aquí se cambian por `?`, que es lo que habla Firebird. Firebird devuelve
    los alias en MAYÚSCULAS: se bajan para que los lectores no distingan.
    Cada consulta cierra su transacción: una transacción larga en Firebird
    detiene la recolección de basura y la base del SAE crece y se alenta.
    """
    if activa.caida is not None:
        raise activa.caida
    if activa.conexion is None:
        try:
            activa.conexion = _conectar_firebird(activa, timeout)
        except SAENoDisponible as e:
            activa.caida = e
            raise
    con = activa.conexion
    cur = None
    try:
        cur = con.cursor()
        cur.execute(sql.replace("%s", "?"), tuple(parametros or ()))
        nombres = [str(d[0]).strip().lower() for d in (cur.description or ())]
        charset = activa.emp.servidor.charset or "ISO8859_1"
        filas = [{n: _valor_firebird(v, charset) for n, v in zip(nombres, fila)}
                 for fila in (cur.fetchall() or [])]
        con.commit()
        return filas
    except Exception:
        # Una conexión que falló a medias no se reusa: la siguiente consulta
        # abre otra limpia.
        activa.cerrar()
        raise
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def columnas(tabla_sae: str) -> set[str]:
    """Las columnas de esa tabla en la base de la empresa activa (SAE 9).

    El esquema del SAE 9 no es idéntico al del 10: algunas columnas nuevas (las
    del REP, la sustitución de CFDI) pueden no existir. Se pregunta al
    catálogo de Firebird una vez por bloque y quien lee pide NULL en lugar de
    una columna que no está, en vez de tronar la pasada entera.
    """
    activa = _ACTIVA.get()
    if activa is None:
        raise RuntimeError("columnas() sólo aplica dentro de en_empresa()")
    if tabla_sae not in activa.columnas:
        filas = consultar(
            "SELECT TRIM(RDB$FIELD_NAME) AS campo FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = %s", (tabla_sae,))
        activa.columnas[tabla_sae] = {str(f.get("campo") or "").strip().upper() for f in filas}
    return activa.columnas[tabla_sae]


def col_o_nulo(alias: str, tabla_sae: str, columna: str, largo: int = 60) -> str:
    """`C.MSJ_CANC` si la columna existe; si no, un NULL tipado (Firebird 2.5
    no acepta un NULL pelón en la lista del SELECT)."""
    if columna.upper() in columnas(tabla_sae):
        return f"{alias}.{columna}"
    return f"CAST(NULL AS VARCHAR({int(largo)}))"


def texto(v: Any) -> str:
    """Un texto de SAE sin relleno. En SQL Server lo hacía RTRIM/LTRIM."""
    return str(v).strip() if v is not None else ""


def texto_der(v: Any) -> str:
    """Sin relleno a la DERECHA nada más, como RTRIM: el CVE_DOC conserva lo
    de la izquierda, que es como lo compara SAE."""
    return str(v).rstrip() if v is not None else ""


def fecha_hora(v: Any) -> str:
    """'AAAA-MM-DD HH:MM:SS', lo que daba CONVERT(varchar(19), X, 120).

    Sirve lo mismo si la columna es TIMESTAMP que si es texto: el esquema del
    SAE 9 no siempre usa el mismo tipo que el 10 para las fechas del CFDI.
    """
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.strftime("%Y-%m-%d 00:00:00")
    return str(v).strip()[:19]


def dinero(v: Any, decimales: int = 2) -> str:
    """El número como lo daba CAST(CAST(ROUND(x, n) AS decimal(18, n)) AS varchar):
    redondeado a mitades hacia arriba y con n decimales exactos."""
    try:
        d = Decimal(str(v if v is not None else 0))
    except Exception:
        d = Decimal(0)
    return str(d.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP))


_TABLA_POR_TIPO = {"factura": "FACTF", "pedido": "FACTP"}


def documentos_de(empresa: str, texto: str, tipo: str = "factura",
                  limite: int = 50) -> list[dict[str, Any]]:
    """Los documentos VIGENTES de SAE cuya OBSERVACION menciona `texto`.

    `tipo` decide la tabla: las facturas viven en FACTF y los pedidos en FACTP,
    y el bot pregunta por los dos cuando va a mover una entrega de semana —el
    folio viaja dentro de la observacion de ambos—.

    Vigente = timbrada de verdad (tiene UUID) y sin la cancelacion pedida al
    SAT. Quien decide es el SAT y no el texto de la observacion, que ya mintio
    en los dos sentidos (21-ago-2026). Un PEDIDO no se timbra, asi que ahi la
    vigencia es solo su STATUS.
    """
    base = _TABLA_POR_TIPO.get(str(tipo or "").lower())
    if not base:
        raise ValueError(f"tipo de documento invalido: {tipo!r}")
    doc_t, obs = tabla(base, empresa), tabla("OBS_DOCF", empresa)
    if base == "FACTF":
        cfdi = tabla("CFDI", empresa)
        extra_sel = ("ISNULL(C.UUID,'') AS uuid, "
                     "LEFT(ISNULL(CONVERT(varchar(30), C.FECHA_CERT), ''), 19) AS fecha_timbrado, "
                     "LEFT(ISNULL(CONVERT(varchar(30), C.FECHA_CANCELA), ''), 19) AS fecha_cancelacion, "
                     "RTRIM(ISNULL(C.MSJ_CANC,'')) AS cancelacion_msj, ")
        extra_join = f"LEFT JOIN {cfdi} C ON C.CVE_DOC = F.CVE_DOC AND C.TIPO_DOC = 'F' "
    else:
        extra_sel = ("'' AS uuid, '' AS fecha_timbrado, '' AS fecha_cancelacion, "
                     "'' AS cancelacion_msj, ")
        extra_join = ""
    filas = consultar(
        f"SELECT TOP {int(limite)} RTRIM(F.CVE_DOC) AS doc, RTRIM(F.STATUS) AS status, "
        "CONVERT(varchar(10), F.FECHA_DOC, 23) AS fecha, "
        "CAST(CAST(ISNULL(F.IMPORTE,0) AS decimal(18,2)) AS varchar(30)) AS total, "
        "CAST(CAST(ISNULL(F.CAN_TOT,0) AS decimal(18,2)) AS varchar(30)) AS subtotal, "
        + extra_sel +
        "LEFT(CAST(O.STR_OBS AS varchar(250)), 250) AS observaciones "
        f"FROM {doc_t} F JOIN {obs} O ON O.CVE_OBS = F.CVE_OBS " + extra_join +
        "WHERE CAST(O.STR_OBS AS varchar(250)) LIKE %s "
        "ORDER BY F.FECHA_DOC DESC",
        (f"%{texto}%",),
    )
    return [_documento(f, tipo) for f in filas]


def documento_por_clave(empresa: str, doc: str, tipo: str = "factura") -> Optional[dict[str, Any]]:
    """Un documento por su CVE_DOC, que en SAE viene con relleno de espacios.

    Lo usa la verificacion de una escritura: «el pedido que acabo de crear,
    ¿esta ahi?». Se compara sin espacios porque 'ZMAFAN       166' y
    'ZMAFAN 166' son el mismo documento escrito distinto.
    """
    base = _TABLA_POR_TIPO.get(str(tipo or "").lower())
    if not base:
        raise ValueError(f"tipo de documento invalido: {tipo!r}")
    doc_t = tabla(base, empresa)
    filas = consultar(
        "SELECT TOP 1 RTRIM(F.CVE_DOC) AS doc, RTRIM(F.STATUS) AS status, "
        "CONVERT(varchar(10), F.FECHA_DOC, 23) AS fecha, "
        "CAST(CAST(ISNULL(F.IMPORTE,0) AS decimal(18,2)) AS varchar(30)) AS total, "
        "CAST(CAST(ISNULL(F.CAN_TOT,0) AS decimal(18,2)) AS varchar(30)) AS subtotal, "
        "'' AS uuid, '' AS fecha_timbrado, '' AS fecha_cancelacion, '' AS cancelacion_msj, "
        "'' AS observaciones "
        f"FROM {doc_t} F WHERE REPLACE(RTRIM(F.CVE_DOC), ' ', '') = %s",
        (str(doc or "").replace(" ", ""),),
    )
    return _documento(filas[0], tipo) if filas else None


def partidas_de(empresa: str, docs: list[str], limite: int = 2000) -> list[dict[str, Any]]:
    """Las partidas de esos documentos: clave, cantidad y su documento.

    Se pide por lote y no una por una: con ochenta documentos, ir de a uno
    tarda minutos. La lista de documentos entra como parametros, uno por
    marcador, nunca concatenada.
    """
    claves = [str(d or "").strip() for d in (docs or []) if str(d or "").strip()]
    if not claves:
        return []
    if len(claves) > 200:
        raise ValueError("demasiados documentos en una sola consulta (maximo 200)")
    par = tabla("PAR_FACTF", empresa)
    marcadores = ", ".join(["%s"] * len(claves))
    filas = consultar(
        f"SELECT TOP {int(limite)} RTRIM(P.CVE_DOC) AS doc, RTRIM(P.CVE_ART) AS clave, "
        "CAST(CAST(ISNULL(P.CANT,0) AS decimal(18,3)) AS varchar(30)) AS cantidad, "
        "CAST(CAST(ISNULL(P.PREC,0) AS decimal(18,4)) AS varchar(30)) AS precio, "
        "CAST(CAST(ISNULL(P.TOT_PARTIDA,0) AS decimal(18,2)) AS varchar(30)) AS importe "
        f"FROM {par} P WHERE REPLACE(RTRIM(P.CVE_DOC), ' ', '') IN ({marcadores}) "
        "ORDER BY P.CVE_DOC, P.NUM_PAR",
        tuple(c.replace(" ", "") for c in claves),
    )
    return [{"doc": " ".join(str(f.get("doc") or "").split()),
             "clave": (f.get("clave") or "").strip(),
             "cantidad": float(f.get("cantidad") or 0),
             "precio": float(f.get("precio") or 0),
             "importe": float(f.get("importe") or 0)} for f in filas]


def _documento(f: dict, tipo: str) -> dict[str, Any]:
    cancelada = bool((f.get("fecha_cancelacion") or "").strip()) or f.get("status") == "C"
    doc = " ".join(str(f.get("doc") or "").split())
    serie, _, folio = doc.rpartition(" ")
    return {
        "doc": doc, "serie": serie or None, "tipo": tipo,
        "folio": int(folio) if folio.isdigit() else None,
        "estado": "CANCELADA" if cancelada else "TIMBRADA",
        "total": float(f.get("total") or 0), "subtotal": float(f.get("subtotal") or 0),
        "uuid": (f.get("uuid") or "").strip() or None,
        "fecha": f.get("fecha"),
        "fecha_timbrado": (f.get("fecha_timbrado") or "").strip() or None,
        "fecha_cancelacion": (f.get("fecha_cancelacion") or "").strip() or None,
        "cancelacion_msj": (f.get("cancelacion_msj") or "").strip() or None,
        "observaciones": (f.get("observaciones") or "").strip(),
        "timbrada": bool((f.get("uuid") or "").strip()),
    }


def facturas_de(empresa: str, texto: str, limite: int = 50) -> list[dict[str, Any]]:
    """Atajo historico: las FACTURAS cuya observacion menciona `texto`."""
    return documentos_de(empresa, texto, tipo="factura", limite=limite)


def partir_documento(doc: Any) -> Optional[tuple[str, int]]:
    """'ZHGO       370' -> ('ZHGO', 370) · 'ZCH5C 12' -> ('ZCH5C', 12).

    Así escribe SAE un CVE_DOC (y el REFER de la CxC, que es el CVE_DOC de la
    factura): la serie a la izquierda, rellena de espacios, y el folio a la
    derecha. LA SERIE TERMINA DONDE EMPIEZA EL FOLIO, y el folio son los
    dígitos FINALES después del ÚLTIMO espacio. Las series pueden llevar
    dígitos (ZCH5C, MIN5C de la empresa 04), así que «letras y luego números»
    no sirve para partir: con esa regla, los REP y las notas de crédito de esas
    series se descartaron EN SILENCIO desde el 24-sep hasta el 26-sep-2026.

    Sin espacio NO se adivina: en 'ZCH5C12' el corte podría ser ZCH5C·12 o
    ZCH5·C12 —y en 'ZCH512', ZCH·512 o ZCH5·12—, y un folio equivocado cuelga
    el pago de la factura de otro. SAE siempre separa, así que un documento
    pegado no viene de SAE y se devuelve None. La única excepción es un
    documento SIN serie, sólo dígitos ('0000000048' -> ('', 48)): ahí no hay
    dónde equivocarse. Los ceros a la izquierda se van (regla del Facturador:
    los folios van sin relleno).
    """
    texto = " ".join(str(doc or "").split())
    if not texto:
        return None
    if texto.isdigit():
        return "", int(texto)
    serie, espacio, folio = texto.rpartition(" ")
    if not espacio or not serie or not folio.isdigit():
        return None
    return serie.replace(" ", "").upper(), int(folio)


def partir_con_series(doc: Any, series=None) -> Optional[tuple[str, int]]:
    """`partir_documento`, y si no alcanza, contra las series QUE SE CONOCEN.

    Hay SAE que guardan el documento con el folio pegado y relleno de ceros
    ('KELLYSLP0000000123'). Sin espacio, `partir_documento` se niega a adivinar
    —con razón—. Pero si se sabe qué series tiene la empresa, el corte deja de
    ser adivinanza: se acepta sólo si UNA sola serie conocida es prefijo y lo
    que sigue son puros dígitos. Dos cortes posibles ('ZCH512' con ZCH y ZCH5
    dadas de alta) siguen sin decidirse.
    """
    partido = partir_documento(doc)
    if partido is not None or not series:
        return partido
    texto = "".join(str(doc or "").split()).upper()
    cortes = set()
    for s in {"".join(str(x or "").split()).upper() for x in series}:
        if s and texto.startswith(s) and texto[len(s):].isdigit():
            cortes.add((s, int(texto[len(s):])))
    return cortes.pop() if len(cortes) == 1 else None


# Leer INVE entero tarda: con ~2,000 artículos por empresa y la red del Mini,
# el timeout de una consulta normal (25 s) no alcanza. Es el mismo margen que
# le daba el bot a `sync_claves_sae.py`.
_TIMEOUT_CATALOGO = 120


def catalogo_inve(empresa: str) -> list[dict[str, Any]]:
    """[{clave, descripcion, activa}] de INVE<empresa>: el catálogo de artículos
    completo, para el espejo `claves_sae`.

    Es el mismo SELECT que usaba el bot en `sync_claves_sae.py` (26-sep-2026).
    Allá DESCR iba envuelta en REPLACE de saltos de línea y tabuladores porque
    sqlcmd devuelve texto renglón por renglón; con pymssql la fila llega
    entera y ese truco ya no hace falta para leer. Los separadores se siguen
    cambiando por espacios, aquí en Python, para que lo guardado sea idéntico a
    lo que depositaba el bot: si no, la primera pasada «actualizaría» todas las
    descripciones con un salto de línea sin que nada haya cambiado.

    `activa` es el STATUS de SAE: 'A' activa, cualquier otra cosa es baja. Una
    lectura que falla LANZA (nunca devuelve una lista vacía por error): quien
    reemplaza el catálogo con esto no puede confundir «SAE no contestó» con
    «SAE no tiene artículos».
    """
    inve = tabla("INVE", empresa)
    if motor() == "firebird":
        # El SAE 9: columnas crudas y la limpieza en Python (abajo ya se hace).
        filas = consultar(
            "SELECT I.CVE_ART AS clave, I.DESCR AS descripcion, I.STATUS AS status "
            f"FROM {inve} I WHERE I.CVE_ART IS NOT NULL",
            timeout=_TIMEOUT_CATALOGO,
        )
    else:
        filas = consultar(
            "SELECT LTRIM(RTRIM(CVE_ART)) AS clave, ISNULL(DESCR,'') AS descripcion, "
            "ISNULL(STATUS,'A') AS status "
            f"FROM {inve} WHERE CVE_ART IS NOT NULL AND LTRIM(RTRIM(CVE_ART)) <> ''",
            timeout=_TIMEOUT_CATALOGO,
        )
    salida = []
    for f in filas:
        clave = str(f.get("clave") or "").strip()
        if not clave:
            continue
        salida.append({
            "clave": clave,
            "descripcion": descripcion_inve(f.get("descripcion")),
            "activa": status_activo(f.get("status")),
        })
    return salida


def descripcion_inve(valor: Any) -> Optional[str]:
    """DESCR de INVE como la guarda el espejo: sin orillas y con los saltos de
    línea y tabuladores cambiados por espacios (lo que hacía el REPLACE del
    bot). Una sola regla para la lectura del catálogo y para lo que confirma el
    escritor, o las dos dirían distinto de la misma clave."""
    return re.sub(r"[\r\n\t]", " ", str(valor or "").strip()) or None


def status_activo(valor: Any) -> bool:
    """STATUS de INVE: 'A' (o vacío) es activa; cualquier otra cosa es baja."""
    return (str(valor or "A").strip().upper() or "A") == "A"


def lineas_de(empresa: str) -> list[dict[str, Any]]:
    """Las líneas de producto de SAE (CLIN<empresa>) que se pueden usar: las de
    baja (STATUS 'B') no se ofrecen, porque asignar una sería dar de alta un
    artículo en una línea que SAE ya retiró."""
    filas = consultar(
        "SELECT RTRIM(CVE_LIN) AS codigo, RTRIM(ISNULL(DESC_LIN,'')) AS nombre "
        f"FROM {tabla('CLIN', empresa)} "
        "WHERE ISNULL(STATUS,'A') <> 'B' ORDER BY CVE_LIN",
    )
    return [{"codigo": str(f.get("codigo") or "").strip(),
             "nombre": str(f.get("nombre") or "").strip()}
            for f in filas if str(f.get("codigo") or "").strip()]


def _pct(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def esquemas_de(empresa: str) -> list[dict[str, Any]]:
    """Los esquemas de impuestos de SAE (IMPU<empresa>) con su IVA y su IEPS en
    PORCENTAJE (16.0 = 16 %), como los guarda SAE: IMPUESTO4 es el IVA e
    IMPUESTO1 el IEPS. El número de esquema (CVE_ESQIMPU) es lo que viaja en un
    alta o un cambio de producto."""
    filas = consultar(
        "SELECT CVE_ESQIMPU AS codigo, RTRIM(ISNULL(DESCRIPESQ,'')) AS descripcion, "
        "ISNULL(IMPUESTO4,0) AS iva, ISNULL(IMPUESTO1,0) AS ieps "
        f"FROM {tabla('IMPU', empresa)} ORDER BY CVE_ESQIMPU",
    )
    salida = []
    for f in filas:
        try:
            codigo = int(f.get("codigo"))
        except (TypeError, ValueError):
            continue
        salida.append({"codigo": codigo,
                       "descripcion": str(f.get("descripcion") or "").strip(),
                       "iva": _pct(f.get("iva")), "ieps": _pct(f.get("ieps"))})
    return salida
