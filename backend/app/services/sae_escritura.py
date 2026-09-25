"""ESCRITURA en Aspel SAE desde el Facturador: altas y cambios de producto.

Hasta el 26-sep-2026 el único que escribía en SAE era el bot, con
`aplicar_altas_sae.py`: reclamaba de la cola del Facturador y hacía el INSERT.
El Facturador ya decidía QUÉ se escribía; el bot solo tecleaba. Decisión del
dueño: «el Facturador debe poder escribir en el SAE, y el bot delega». Este
módulo es ese escritor, y el bot deja de serlo.

LA REGLA QUE NO CAMBIA: **nunca se reintenta una escritura a SAE.** Un INSERT
repetido duplica el artículo. La regla nunca dijo que el escritor tuviera que
ser el bot, sino que fuera UNO; por eso, con este reloj encendido, la puerta
por la que reclamaba el bot (`GET /productos/alta-sae/pendiente`) ya no le
entrega nada. De ahí salen las mismas decisiones que ya probó el bot:

  1. Cada empresa es su propia transacción, con su candado de existencia. Si
     la 03 falla, la 02 se queda hecha y se REPORTA así (PARCIAL), sin
     deshacerla ni repetirla.
  2. Una empresa que ya tenía la clave NO es un error en un alta: se reporta
     como `ya_existia`, que es la verdad.
  3. Si SAE no contesta antes de empezar, no se escribe nada y la solicitud se
     cierra como ERROR «se puede volver a pedir». Si el cierre en el Facturador
     falla DESPUÉS de escribir, se grita en la bitácora y la solicitud se queda
     EN_CURSO hasta que expire con «revisa en SAE»: nunca se vuelve a intentar.

TRES CANDADOS MÁS, heredados de la lectura:

  · Usuario APARTE (`SAE_ESCRITURA_USER`): el de lectura sigue sin poder
    escribir, y el de escritura solo necesita INVE y PRECIO_X_PROD.
  · Nada de SQL armado con datos: todo valor viaja como PARÁMETRO. Lo único que
    se concatena es el nombre de la tabla, y ése pasa por `sae_lectura.tabla`,
    que exige dos dígitos de empresa.
  · Campos en lista blanca. Un cambio sólo toca DESCR, LIN_PROD, UNI_MED +
    CVE_UNIDAD, CVE_ESQIMPU, CVE_PRODSERV y STATUS→'A'. Dar de BAJA una clave
    no existe aquí a propósito: regla del dueño, nunca por iniciativa propia.
    Y el precio tampoco: el alcance que se autorizó es alta y cambios de
    producto, no listas de precios.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.services import sae_lectura

log = logging.getLogger(__name__)

# Las empresas de SAE donde se puede escribir. Una fuera de aquí no se toca.
EMPRESAS = ("02", "03", "04", "05")

# Unidad del Facturador → (UNI_MED de SAE, clave de unidad del SAT). Es el mapa
# con el que el bot daba de alta, más los plurales. Una unidad que no está aquí
# NO cae a PIEZA en silencio (eso ya creó productos mal): se reporta error.
UNIDADES = {
    "PIEZA": ("PZ", "H87"), "PIEZAS": ("PZ", "H87"), "PZA": ("PZ", "H87"), "PZ": ("PZ", "H87"),
    "KILOGRAMO": ("KG", "KGM"), "KILOGRAMOS": ("KG", "KGM"), "KILO": ("KG", "KGM"),
    "KG": ("KG", "KGM"), "KGS": ("KG", "KGM"),
    "CAJA": ("CJ", "XBX"), "CAJAS": ("CJ", "XBX"), "CJ": ("CJ", "XBX"),
    "LITRO": ("LT", "LTR"), "LITROS": ("LT", "LTR"), "LT": ("LT", "LTR"),
    "PAQUETE": ("PQ", "XPK"), "PAQUETES": ("PQ", "XPK"), "PQ": ("PQ", "XPK"),
}

# Los campos que un CAMBIO puede traer. Es la lista blanca de arriba.
CAMPOS_CAMBIO = ("descripcion", "linea", "unidad", "esquema", "sat", "reactivar")

_RE_CLAVE = re.compile(r"^[A-Z0-9\-]{1,16}$")
_RE_LINEA = re.compile(r"^[A-Z0-9]{1,10}$")
_RE_SAT = re.compile(r"^\d{8}$")


class SAEEscrituraNoDisponible(RuntimeError):
    """No hay puerta de escritura: sin configuración, sin driver o sin red."""


def disponible() -> bool:
    """¿Está configurada la puerta de escritura? No prueba la red."""
    return bool(settings.SAE_SERVER and settings.SAE_ESCRITURA_USER
                and settings.SAE_ESCRITURA_PASSWORD)


def activo() -> bool:
    """¿El Facturador es HOY el escritor de SAE?

    Es la misma pregunta que responde la puerta del bot: si esto es verdad, el
    bot ya no puede reclamar. Necesita las tres cosas: credenciales, reloj y el
    tenant de quien es el SAE.
    """
    return bool(disponible() and settings.SAE_ESCRITURA_INTERVALO_SEG
                and settings.ESPEJO_SAE_TENANT_ID)


def ascii_mayus(s: Any) -> str:
    """MAYÚSCULAS sin acentos y Ñ→N: como el bot escribía claves y descripciones."""
    t = unicodedata.normalize("NFD", str(s or "").upper())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def normalizar_clave(clave: Any) -> Optional[str]:
    """La clave como vive en SAE (CVE_ART), o None si no se puede escribir tal cual.

    No se «arregla» una clave ilegible quitándole caracteres: eso crearía en SAE
    una clave distinta de la que alguien pidió.
    """
    c = re.sub(r"\s+", "", ascii_mayus(clave))
    return c if _RE_CLAVE.match(c) else None


def validar_cambios(cambios: dict) -> dict:
    """Limpia un CAMBIO y lo devuelve normalizado. Lanza ValueError si algo no
    sirve: se rechaza al pedirlo, no media hora después, al escribir."""
    fuera = [k for k in cambios if k not in CAMPOS_CAMBIO]
    if fuera:
        raise ValueError(f"Campos que no se cambian en SAE: {', '.join(sorted(fuera))}")
    out: dict[str, Any] = {}
    if cambios.get("descripcion") is not None:
        d = ascii_mayus(cambios["descripcion"]).strip()
        if not d:
            raise ValueError("La descripción no puede ir vacía")
        out["descripcion"] = d[:60]
    if cambios.get("linea") is not None:
        lin = ascii_mayus(cambios["linea"]).strip()
        if not _RE_LINEA.match(lin):
            raise ValueError(f"Línea de SAE inválida: {cambios['linea']!r}")
        out["linea"] = lin
    if cambios.get("unidad") is not None:
        u = ascii_mayus(cambios["unidad"]).strip()
        if u not in UNIDADES:
            raise ValueError(f"Unidad no reconocida: {cambios['unidad']!r} "
                             f"(válidas: pieza, kilo, caja, litro, paquete)")
        out["unidad"] = u
    if cambios.get("esquema") is not None:
        try:
            esq = int(cambios["esquema"])
        except (TypeError, ValueError):
            raise ValueError(f"Esquema de impuestos inválido: {cambios['esquema']!r}") from None
        if not 1 <= esq <= 99:
            raise ValueError(f"Esquema de impuestos inválido: {esq}")
        out["esquema"] = esq
    if cambios.get("sat") is not None:
        sat = re.sub(r"\D", "", str(cambios["sat"]))
        if not _RE_SAT.match(sat):
            raise ValueError(f"Clave SAT inválida (son 8 dígitos): {cambios['sat']!r}")
        out["sat"] = sat
    if cambios.get("reactivar"):
        out["reactivar"] = True
    if not out:
        raise ValueError("No hay nada que cambiar")
    return out


# ── La conexión ──────────────────────────────────────────────────────────────

def _conectar():
    """Una conexión de ESCRITURA, en modo transacción (autocommit apagado)."""
    if not disponible():
        raise SAEEscrituraNoDisponible("el Facturador no tiene configurada la escritura a SAE")
    try:
        import pymssql
    except ImportError as e:  # pragma: no cover - depende de la imagen
        raise SAEEscrituraNoDisponible(f"falta el driver de SQL Server: {e}") from e
    servidor, _, puerto = str(settings.SAE_SERVER).partition(",")
    return pymssql.connect(
        server=servidor.strip(),
        port=int(puerto or 1433),
        user=settings.SAE_ESCRITURA_USER,
        password=settings.SAE_ESCRITURA_PASSWORD,
        database=settings.SAE_DATABASE,
        timeout=int(settings.SAE_TIMEOUT),
        login_timeout=int(settings.SAE_TIMEOUT),
        autocommit=False,
    )


def contesta() -> bool:
    """¿SAE contesta con el usuario de escritura? Se pregunta ANTES de tocar
    nada: si no contesta, la solicitud se cierra sin haber escrito y se puede
    volver a pedir sin riesgo."""
    try:
        con = _conectar()
    except Exception as e:  # noqa: BLE001 — cualquier falla es «no contesta»
        log.warning("SAE (escritura) no contesta: %s: %s", type(e).__name__, e)
        return False
    try:
        with con.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchall()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("SAE (escritura) no contesta: %s: %s", type(e).__name__, e)
        return False
    finally:
        con.close()


# ── Alta ─────────────────────────────────────────────────────────────────────

def alta(empresa: str, clave: str, datos: dict) -> dict[str, Any]:
    """Crea el artículo en UNA empresa. Devuelve lo que va al reporte:
    {"ok": True, "clave": ...} · {"ok": True, "ya_existia": True, ...} ·
    {"ok": False, "error": ...}.

    Los campos son los MISMOS que llenaba el bot (`_sql_alta`): dejarlos en NULL
    rompe el almacén —sin TIP_COSTEO no costea y sin FAC_CONV/NUM_MON falla la
    conversión de unidades—. El precio entra en 0 a propósito (regla del dueño):
    se captura después, confirmado.
    """
    uni_h = ascii_mayus(datos.get("unidad") or "PIEZA").strip() or "PIEZA"
    if uni_h not in UNIDADES:
        return {"ok": False, "error": f"unidad no reconocida: {uni_h}"}
    uni, sat_uni_def = UNIDADES[uni_h]
    sat_uni = (str(datos.get("sat_unidad") or "").strip() or sat_uni_def)[:10]
    desc = ascii_mayus(datos.get("descripcion") or "").strip()[:60]
    if not desc:
        return {"ok": False, "error": "sin descripción"}
    linea = ascii_mayus(datos.get("linea") or "ABARR").strip()[:10]
    try:
        esq = int(datos.get("esquema") or 2)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"esquema inválido: {datos.get('esquema')!r}"}
    sat = (str(datos.get("sat") or "").strip() or "01010101")[:20]

    inve = sae_lectura.tabla("INVE", empresa)
    precio = sae_lectura.tabla("PRECIO_X_PROD", empresa)
    con = _conectar()
    try:
        with con.cursor() as cur:
            # UPDLOCK+HOLDLOCK: el candado de existencia y el INSERT son una
            # sola cosa. Sin él, dos escritores podrían ver «no existe» a la vez.
            cur.execute(f"SELECT 1 FROM {inve} WITH (UPDLOCK, HOLDLOCK) "
                        "WHERE LTRIM(RTRIM(CVE_ART)) = %s", (clave,))
            if cur.fetchall():
                con.rollback()
                return {"ok": True, "clave": clave, "ya_existia": True}
            cur.execute(
                f"INSERT INTO {inve} (CVE_ART, DESCR, LIN_PROD, UNI_MED, UNI_EMP, UNI_ALT,"
                " CVE_ESQIMPU, STATUS, EXIST, MAN_IEPS, CUOTA_IEPS, APL_MAN_IEPS, CVE_PRODSERV,"
                " CVE_UNIDAD, UUID, TIP_COSTEO, CON_SERIE, CON_LOTE, CON_PEDIMENTO, TIPO_ELE,"
                " NUM_MON, FAC_CONV, BLK_CST_EXT, TIEM_SURT, STOCK_MIN, STOCK_MAX, COMP_X_REC,"
                " PEND_SURT, COSTO_PROM, ULT_COSTO, CVE_OBS, APART, PESO, VOLUMEN, VTAS_ANL_C,"
                " VTAS_ANL_M, COMP_ANL_C, COMP_ANL_M) VALUES "
                "(%s, %s, %s, %s, '1', %s, %s, 'A', 0, 'N', 0, 'C', %s, %s,"
                " UPPER(CONVERT(varchar(50), NEWID())), 'P', 'N', 'N', 'N', 'P', 1, 1, 'N',"
                " 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
                (clave, desc, linea, uni, uni, esq, sat, sat_uni),
            )
            cur.execute(
                f"INSERT INTO {precio} (CVE_ART, CVE_PRECIO, PRECIO, PRECIOCIMP, UUID) "
                "VALUES (%s, 3, 0, 0, UPPER(CONVERT(varchar(50), NEWID())))",
                (clave,),
            )
        con.commit()
    except Exception as e:  # noqa: BLE001 — el error ES el resultado de esta empresa
        try:
            con.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:180]}"}
    finally:
        con.close()
    return {"ok": True, "clave": clave}


# ── Cambio ───────────────────────────────────────────────────────────────────

def cambio(empresa: str, clave: str, cambios: dict) -> dict[str, Any]:
    """Aplica un CAMBIO ya validado (`validar_cambios`) en UNA empresa.

    Un UPDATE que no toca ninguna fila es un ERROR que se dice («la clave no
    existe en esa empresa»), no un éxito callado. Uno que toca más de una se
    deshace: hay claves duplicadas en esa empresa y eso lo decide una persona.
    """
    try:
        cambios = validar_cambios(cambios)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    inve = sae_lectura.tabla("INVE", empresa)
    sets: list[str] = []
    params: list[Any] = []
    con = _conectar()
    try:
        with con.cursor() as cur:
            if "linea" in cambios:
                cur.execute(f"SELECT 1 FROM {sae_lectura.tabla('CLIN', empresa)} "
                            "WHERE RTRIM(CVE_LIN) = %s", (cambios["linea"],))
                if not cur.fetchall():
                    return {"ok": False, "error": f"la línea {cambios['linea']} no existe en la empresa {empresa}"}
            if "esquema" in cambios:
                cur.execute(f"SELECT 1 FROM {sae_lectura.tabla('IMPU', empresa)} "
                            "WHERE CVE_ESQIMPU = %s", (cambios["esquema"],))
                if not cur.fetchall():
                    return {"ok": False, "error": f"el esquema {cambios['esquema']} no existe en la empresa {empresa}"}

            if "descripcion" in cambios:
                sets.append("DESCR = %s"); params.append(cambios["descripcion"])
            if "linea" in cambios:
                sets.append("LIN_PROD = %s"); params.append(cambios["linea"])
            if "unidad" in cambios:
                uni, sat_uni = UNIDADES[cambios["unidad"]]
                sets.append("UNI_MED = %s"); params.append(uni)
                sets.append("CVE_UNIDAD = %s"); params.append(sat_uni)
            if "esquema" in cambios:
                sets.append("CVE_ESQIMPU = %s"); params.append(cambios["esquema"])
            if "sat" in cambios:
                sets.append("CVE_PRODSERV = %s"); params.append(cambios["sat"])
            if cambios.get("reactivar"):
                sets.append("STATUS = 'A'")

            cur.execute(f"UPDATE {inve} SET {', '.join(sets)} WHERE LTRIM(RTRIM(CVE_ART)) = %s",
                        tuple(params + [clave]))
            n = cur.rowcount
        if n == 0:
            con.rollback()
            return {"ok": False, "error": f"la clave {clave} no existe en la empresa {empresa}"}
        if n > 1:
            con.rollback()
            return {"ok": False, "error": f"la clave {clave} está {n} veces en la empresa {empresa}: no se tocó"}
        con.commit()
    except Exception as e:  # noqa: BLE001
        try:
            con.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:180]}"}
    finally:
        con.close()
    return {"ok": True, "clave": clave, "campos": sorted(cambios)}


# ── El reloj ─────────────────────────────────────────────────────────────────

# Tope por pasada: si algo encola en bucle, que no se vuelva 500 escrituras a
# SAE sin que nadie lo vea (el mismo tope que tenía el conector del bot).
_TOPE_POR_PASADA = 20


def _aplicar(sol: dict) -> tuple[dict, Optional[str]]:
    """Corre una solicitud ya reclamada. Devuelve (por_empresa, motivo)."""
    empresas = [str(e) for e in (sol["empresas"] or []) if str(e) in EMPRESAS]
    clave = normalizar_clave(sol["clave"])
    if not clave:
        return ({e: {"ok": False, "error": "clave ilegible"} for e in empresas},
                f"La clave {sol['clave']!r} no se puede escribir en SAE tal cual")
    if not contesta():
        return ({e: {"ok": False, "error": "SAE no contestó"} for e in empresas},
                "SAE no contestó: no se escribió nada, se puede volver a pedir")
    por_empresa: dict[str, dict] = {}
    for empresa in empresas:
        if sol["tipo"] == "CAMBIO":
            por_empresa[empresa] = cambio(empresa, clave, sol["datos"] or {})
        else:
            por_empresa[empresa] = alta(empresa, clave, sol["datos"] or {})
    fallas = [f"{e}: {r.get('error')}" for e, r in por_empresa.items() if not r.get("ok")]
    return por_empresa, ("; ".join(fallas)[:300] or None)


def pasada() -> dict[str, Any]:
    """Vacía la cola: reclama una, la escribe, la cierra; hasta el tope.

    Reclamar y cerrar son dos transacciones del Facturador distintas, con la
    escritura a SAE en medio y FUERA de las dos: si el proceso muere entre
    medias, la solicitud queda EN_CURSO y expira como «revisa en SAE» — la
    única salida honesta cuando no se sabe si el INSERT entró.
    """
    from ..api.v1.productos import cerrar_solicitud_sae, reclamar_siguiente_sae
    from ..core.rbac import tenant_session

    if not activo():
        return {"corrio": False, "motivo": "la escritura a SAE está apagada"}
    tenant_id = settings.ESPEJO_SAE_TENANT_ID
    hechas: list[dict] = []
    for _ in range(_TOPE_POR_PASADA):
        with tenant_session(tenant_id) as db:
            sol = reclamar_siguiente_sae(db, tenant_id)
            if sol is None:
                break
            # se copia DENTRO de la sesión: afuera el objeto queda suelto
            sol = {"id": sol.id, "tipo": sol.tipo, "clave": sol.clave,
                   "datos": dict(sol.datos or {}), "empresas": list(sol.empresas or [])}
        por_empresa, motivo = _aplicar(sol)
        try:
            with tenant_session(tenant_id) as db:
                cerrada = cerrar_solicitud_sae(db, tenant_id, sol["id"], por_empresa, motivo)
                estado = cerrada.estado
        except Exception:
            # Ya se escribió (o no) en SAE y el cierre no entró. NO se vuelve a
            # escribir: la bitácora es lo que permite cerrarlo a mano.
            log.exception("SAE (escritura): %s %s quedó %s en SAE pero NO se pudo cerrar "
                          "la solicitud %s — NO se reintenta; expirará como «revisa en SAE»",
                          sol["tipo"], sol["clave"], por_empresa, sol["id"])
            break
        log.info("SAE (escritura): %s %s → %s %s", sol["tipo"], sol["clave"], estado, por_empresa)
        hechas.append({"id": str(sol["id"]), "tipo": sol["tipo"], "clave": sol["clave"],
                       "estado": estado})
    return {"corrio": True, "hechas": hechas, "cuando": datetime.now(timezone.utc).isoformat()}


async def reloj(intervalo: int) -> None:
    """Una pasada cada `intervalo` segundos, para siempre (como el espejo).

    La pasada toca la red, así que va a un hilo; y un error se escribe y se
    sigue: un reloj que se detiene en silencio es una cola que nadie atiende.
    """
    import asyncio

    while True:
        await asyncio.sleep(max(5, int(intervalo)))
        try:
            await asyncio.to_thread(pasada)
        except Exception as e:  # noqa: BLE001
            log.exception("SAE (escritura): la pasada falló (%s)", type(e).__name__)
