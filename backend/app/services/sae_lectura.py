"""Lectura DIRECTA de Aspel SAE desde el Facturador.

Hasta hoy el único que hablaba con SAE era el bot de WhatsApp, y por eso el
espejo de facturas corre allá: «el bot ya tiene el acceso probado a la BD de
SAE» (facturador_espejo.py). Eso obligaba a que toda pregunta que EXIGE el dato
de este momento —«¿esta orden ya tiene factura AHORA?»— saliera del bot por su
propia puerta, y no del Facturador, que es de donde el dueño quiere que salga
todo (24-sep-2026).

TRES CANDADOS, y ninguno es decorativo:

1. SOLO LECTURA. `consultar()` rechaza cualquier cosa que no sea un único
   SELECT. Las escrituras a SAE siguen siendo del bot y de su cola, porque
   ahí vive la regla que las gobierna: una escritura a SAE NUNCA se reintenta
   —un INSERT repetido duplica un pedido o una factura— y esa garantía se
   sostiene teniendo UN SOLO escritor. Dos escritores no se coordinan con un
   comentario.

2. NADA DE SQL DEL LLAMADOR. Las consultas se arman aquí; lo que viene de
   afuera viaja como PARÁMETRO, nunca concatenado. Esto no es una pasarela de
   SQL con URL bonita.

3. SIN CONFIGURACIÓN NO HAY PUERTA. Si faltan las variables, `disponible()`
   dice que no y quien llama contesta «no tengo acceso a SAE» en vez de
   tronar. Un despliegue sin `SAE_SERVER` se comporta exactamente como ayer.

La empresa viaja en el nombre de la tabla (FACTF02, FACTF03…), que es como
Aspel separa las empresas, así que se valida contra una lista blanca: son dos
dígitos y nada más.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.core.config import settings

_EMPRESAS_VALIDAS = re.compile(r"^\d{2}$")
_SOLO_SELECT = re.compile(r"^\s*SELECT\s", re.IGNORECASE)


class SAENoDisponible(RuntimeError):
    """No hay acceso a SAE: sin configuración, sin driver o sin red."""


def disponible() -> bool:
    """¿Está configurada la puerta? No prueba la red: eso cuesta segundos."""
    return bool(settings.SAE_SERVER and settings.SAE_USER and settings.SAE_PASSWORD)


def tabla(nombre: str, empresa: str) -> str:
    """`FACTF` + `03` -> `FACTF03`, con la empresa validada.

    La empresa NO puede venir cruda a una cadena de SQL: es lo único que se
    concatena, así que es lo único que hay que blindar.
    """
    emp = str(empresa or "").strip()
    if not _EMPRESAS_VALIDAS.match(emp):
        raise ValueError(f"empresa de SAE inválida: {empresa!r}")
    if not re.fullmatch(r"[A-Z_]+", nombre or ""):
        raise ValueError(f"tabla de SAE inválida: {nombre!r}")
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


def facturas_de(empresa: str, texto: str, limite: int = 50) -> list[dict[str, Any]]:
    """Las facturas VIGENTES de SAE cuya OBSERVACIÓN menciona `texto`.

    Es la consulta que el bot hacía por su cuenta para decidir si una entrega
    ya está facturada. Vigente = timbrada de verdad (tiene UUID) y sin la
    cancelación pedida al SAT; quien decide es el SAT y no el texto de la
    observación, que ya mintió en los dos sentidos (21-ago-2026).
    """
    factf, obs, cfdi = tabla("FACTF", empresa), tabla("OBS_DOCF", empresa), tabla("CFDI", empresa)
    filas = consultar(
        f"SELECT TOP {int(limite)} RTRIM(F.CVE_DOC) AS doc, RTRIM(F.STATUS) AS status, "
        "CONVERT(varchar(10), F.FECHA_DOC, 23) AS fecha, "
        "CAST(CAST(ISNULL(F.IMPORTE,0) AS decimal(18,2)) AS varchar(30)) AS total, "
        "CAST(CAST(ISNULL(F.CAN_TOT,0) AS decimal(18,2)) AS varchar(30)) AS subtotal, "
        "ISNULL(C.UUID,'') AS uuid, "
        "LEFT(ISNULL(CONVERT(varchar(30), C.FECHA_CERT), ''), 19) AS fecha_timbrado, "
        "LEFT(ISNULL(CONVERT(varchar(30), C.FECHA_CANCELA), ''), 19) AS fecha_cancelacion, "
        "RTRIM(ISNULL(C.MSJ_CANC,'')) AS cancelacion_msj, "
        "LEFT(CAST(O.STR_OBS AS varchar(250)), 250) AS observaciones "
        f"FROM {factf} F JOIN {obs} O ON O.CVE_OBS = F.CVE_OBS "
        f"LEFT JOIN {cfdi} C ON C.CVE_DOC = F.CVE_DOC AND C.TIPO_DOC = 'F' "
        "WHERE CAST(O.STR_OBS AS varchar(250)) LIKE %s "
        "ORDER BY F.FECHA_DOC DESC",
        (f"%{texto}%",),
    )
    salida = []
    for f in filas:
        cancelada = bool((f.get("fecha_cancelacion") or "").strip()) or f.get("status") == "C"
        doc = " ".join(str(f.get("doc") or "").split())
        serie, _, folio = doc.rpartition(" ")
        salida.append({
            "doc": doc, "serie": serie or None,
            "folio": int(folio) if folio.isdigit() else None,
            "estado": "CANCELADA" if cancelada else "TIMBRADA",
            "total": float(f.get("total") or 0), "subtotal": float(f.get("subtotal") or 0),
            "uuid": (f.get("uuid") or "").strip() or None,
            "fecha": f.get("fecha"), "fecha_timbrado": (f.get("fecha_timbrado") or "").strip() or None,
            "fecha_cancelacion": (f.get("fecha_cancelacion") or "").strip() or None,
            "cancelacion_msj": (f.get("cancelacion_msj") or "").strip() or None,
            "observaciones": (f.get("observaciones") or "").strip(),
            "timbrada": bool((f.get("uuid") or "").strip()),
        })
    return salida
