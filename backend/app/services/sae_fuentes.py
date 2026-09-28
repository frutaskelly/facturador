"""De dónde sale cada empresa de SAE: servidor, motor, número y tenant.

Hasta el 26-sep-2026 el Facturador conocía UN solo SAE —el 10, en SQL Server—
y UN solo tenant, y los dos vivían sueltos en la configuración
(`SAE_SERVER`, `ESPEJO_SAE_TENANT_ID`, `ESPEJO_SAE_EMPRESAS`) y en el código
(`_SERIES_POR_EMPRESA`). Apareció un segundo SAE —el 9, en Firebird, en otro
servidor— con empresas de dos RFC distintos, y «la empresa 02» dejó de
identificar nada: hay una 02 en cada servidor.

Este módulo es la única respuesta a «¿qué empresa es ésta?». Tres reglas:

1. EL SAE 10 SE ARMA IGUAL QUE AYER. Sale de las mismas variables, con el
   código igual al número, y nadie que no toque `SAE_FB_*` ve ningún cambio.

2. EL CÓDIGO NO ES EL NÚMERO. `numero` es el de Aspel (el sufijo de la tabla:
   FACTF01); `codigo` es el que guarda el Facturador (`espejo_empresa`,
   `claves_sae.empresa`, la equivalencia '91:CLIENTE'). En el SAE 10 son
   iguales. En el SAE 9 el código va con un 9 delante del último dígito (01 →
   91): así la 02 del SAE 9 nunca pisa a la 02 del SAE 10 dentro del mismo
   tenant, y sigue siendo de dos dígitos —que es lo que esperan las
   validaciones que ya existen—.

3. LAS CREDENCIALES NO VIAJAN EN EL REGISTRO. El servidor lleva usuario y
   contraseña porque los necesita para conectarse, pero se leen de la
   configuración (variables de entorno), nunca de la base ni de un payload.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..core.config import settings

log = logging.getLogger(__name__)

_DOS_DIGITOS = re.compile(r"^\d{2}$")

# Las series de SAE 10 por empresa, las mismas con las que el bot espejaba.
# Vivían en `api/v1/sae.py`; ahí se conserva el nombre para quien lo importe.
SERIES_SAE10 = {
    "02": ("ZHGO", "ZEHMOHOS", "ZMAFAN", "ZEHMOFAC", "ZECA"),
    "03": ("ZEHMOVH",),
    "04": ("ZEHMOTG", "ZSUR", "ZDIF", "ZBPT", "ZCH5C", "ZCS", "MIN5C", "ZVIDA"),
}

# Clientes de mostrador de la empresa 02 del SAE 10: no son clientes de verdad.
PLACEHOLDERS_SAE10 = {"02": frozenset({"1", "2", "3"})}


@dataclass(frozen=True)
class ServidorSAE:
    """Un servidor de SAE y cómo se entra. `clave` es para los mensajes."""
    clave: str                 # "SAE10" · "SAE9"
    motor: str                 # "mssql" · "firebird"
    host: str = ""
    puerto: int = 0
    usuario: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    ruta: str = ""             # Firebird: ruta del .FDB con {nn} en lugar del número
    charset: str = "ISO8859_1"
    auth: str = "Legacy_Auth"
    timeout: int = 30

    @property
    def es_firebird(self) -> bool:
        return self.motor == "firebird"

    def ruta_de(self, numero: str) -> str:
        """La base de ESA empresa: en Firebird cada empresa es su propio archivo."""
        if not _DOS_DIGITOS.match(str(numero or "")):
            raise ValueError(f"empresa de SAE inválida: {numero!r}")
        return self.ruta.replace("{nn}", numero)

    def configurado(self) -> bool:
        if self.es_firebird:
            return bool(self.host and self.usuario and self.password and "{nn}" in self.ruta)
        return bool(settings.SAE_SERVER and settings.SAE_USER and settings.SAE_PASSWORD)


@dataclass(frozen=True)
class EmpresaSAE:
    """Una empresa de un servidor de SAE, ya ligada a su tenant."""
    servidor: ServidorSAE
    numero: str                # el de Aspel: sufijo de tabla, nombre del .FDB
    codigo: str                # el del Facturador: espejo_empresa, equivalencias
    tenant_id: str
    series: tuple = ()         # vacías = se descubren en FACTF desde `desde`
    desde: Optional[dt.date] = None   # nada anterior a esto se espeja
    facturas: bool = True      # la 05 del SAE 10 sólo tiene catálogo
    claves: bool = True        # catálogo de artículos (INVE → claves_sae)
    placeholders: frozenset = frozenset()

    @property
    def etiqueta(self) -> str:
        """'SAE9·01→91' para los mensajes: sin esto un error de «la 02» no dice
        de cuál de las dos 02 habla."""
        if self.codigo == self.numero:
            return f"{self.servidor.clave}·{self.numero}"
        return f"{self.servidor.clave}·{self.numero}→{self.codigo}"


def serie_facturador(serie: str) -> str:
    """La serie como la guarda el Facturador: sin espacios y en mayúsculas.

    El SAE 9 tiene series con espacio adentro ('FOR K', 'QRO AK'). La CxC las
    escribe pegadas al folio ('FOR K     123') y `partir_documento` les quita
    el espacio para poder partirlas, así que la factura se guarda igual o el
    pago nunca encontraría su factura. En el SAE 10 ninguna serie tiene
    espacios: para ellas esto no cambia nada.
    """
    return "".join(str(serie or "").split()).upper()


# ─── El SAE 10: de las variables de siempre ─────────────────────────────────

def _lista(valor) -> list[str]:
    return [e.strip() for e in str(valor or "").split(",") if e.strip()]


def servidor_sae10() -> ServidorSAE:
    servidor, _, puerto = str(settings.SAE_SERVER or "").partition(",")
    return ServidorSAE(clave="SAE10", motor="mssql", host=servidor.strip(),
                       puerto=int(puerto or 1433) if str(puerto or "").strip().isdigit() else 1433,
                       usuario=settings.SAE_USER, password=settings.SAE_PASSWORD,
                       timeout=int(settings.SAE_TIMEOUT))


def empresas_sae10() -> list[EmpresaSAE]:
    """Las empresas del SAE 10, exactamente como las armaba el reloj: las de
    facturas (`ESPEJO_SAE_EMPRESAS`) y las de catálogo
    (`ESPEJO_SAE_CLAVES_EMPRESAS`), en el tenant de `ESPEJO_SAE_TENANT_ID`."""
    tenant = str(settings.ESPEJO_SAE_TENANT_ID or "").strip()
    if not tenant:
        return []
    srv = servidor_sae10()
    de_facturas = _lista(settings.ESPEJO_SAE_EMPRESAS)
    de_claves = _lista(settings.ESPEJO_SAE_CLAVES_EMPRESAS) \
        if int(settings.ESPEJO_SAE_CLAVES_CADA_SEG or 0) > 0 else []
    salida: list[EmpresaSAE] = []
    for emp in dict.fromkeys(de_facturas + de_claves):
        salida.append(EmpresaSAE(
            servidor=srv, numero=emp, codigo=emp, tenant_id=tenant,
            series=tuple(SERIES_SAE10.get(emp, ())),
            facturas=emp in de_facturas, claves=emp in de_claves,
            placeholders=PLACEHOLDERS_SAE10.get(emp, frozenset()),
        ))
    return salida


# ─── El SAE 9: Firebird, de `SAE_FB_*` ──────────────────────────────────────

def servidor_sae9() -> ServidorSAE:
    return ServidorSAE(
        clave="SAE9", motor="firebird", host=str(settings.SAE_FB_HOST or "").strip(),
        puerto=int(settings.SAE_FB_PUERTO or 3050),
        usuario=settings.SAE_FB_USER, password=settings.SAE_FB_PASSWORD,
        ruta=str(settings.SAE_FB_RUTA or ""), charset=settings.SAE_FB_CHARSET or "ISO8859_1",
        auth=settings.SAE_FB_AUTH or "Legacy_Auth", timeout=int(settings.SAE_FB_TIMEOUT or 30),
    )


def _fecha(valor) -> Optional[dt.date]:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return dt.date.fromisoformat(texto[:10])
    except ValueError:
        log.warning("SAE 9: fecha 'desde' inválida %r — se ignora", valor)
        return None


def _si(valor, omision: bool = True) -> bool:
    """true/false de la configuración, sin que 'false' (texto) cuente como sí."""
    if valor is None:
        return omision
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "yes")


def codigo_sae9(numero: str) -> str:
    """01 → 91 · 04 → 94. Ver la regla 2 del módulo."""
    return f"9{str(numero)[-1]}"


def empresas_sae9() -> list[EmpresaSAE]:
    """Las empresas del SAE 9 según `SAE_FB_EMPRESAS` (JSON).

    [{"numero": "01", "tenant": "<uuid>", "series": [], "codigo": "91"}, …]

    `codigo` y `series` son opcionales (por omisión 9x y descubrirlas). Una
    entrada mal escrita se salta CON AVISO y no tumba a las demás: una coma de
    más no puede dejar al SAE 10 sin espejo.
    """
    crudo = str(settings.SAE_FB_EMPRESAS or "").strip()
    if not crudo:
        return []
    try:
        entradas = json.loads(crudo)
    except ValueError as e:
        log.warning("SAE 9: SAE_FB_EMPRESAS no es JSON válido (%s) — no se espeja", e)
        return []
    if not isinstance(entradas, list):
        log.warning("SAE 9: SAE_FB_EMPRESAS debe ser una lista — no se espeja")
        return []
    srv = servidor_sae9()
    desde_general = _fecha(settings.SAE_FB_DESDE)
    salida: list[EmpresaSAE] = []
    codigos: set[str] = set()
    for i, e in enumerate(entradas):
        if not isinstance(e, dict):
            log.warning("SAE 9: la entrada %s no es un objeto — se salta", i)
            continue
        numero = str(e.get("numero") or "").strip()
        tenant = str(e.get("tenant") or "").strip()
        codigo = str(e.get("codigo") or "").strip() or (codigo_sae9(numero) if numero else "")
        if not _DOS_DIGITOS.match(numero) or not _DOS_DIGITOS.match(codigo) or not tenant:
            log.warning("SAE 9: la entrada %s necesita numero y codigo de dos dígitos y "
                        "tenant — se salta", i)
            continue
        crudas = e.get("series")
        if crudas is None:
            crudas = []
        elif isinstance(crudas, str):
            crudas = [crudas]
        if not isinstance(crudas, (list, tuple)):
            log.warning("SAE 9: 'series' de la entrada %s debe ser una lista — se salta", i)
            continue
        # SIN PISO NO ENTRA. «Este año nada más» es parte del acuerdo: una
        # fecha mal escrita no puede abrir la puerta a toda la historia.
        desde = _fecha(e.get("desde")) or desde_general
        if desde is None:
            log.warning("SAE 9: la entrada %s no tiene fecha 'desde' válida (AAAA-MM-DD) "
                        "— se salta", i)
            continue
        if codigo in codigos:
            log.warning("SAE 9: el código %s está repetido — se salta la entrada %s", codigo, i)
            continue
        codigos.add(codigo)
        salida.append(EmpresaSAE(
            servidor=srv, numero=numero, codigo=codigo, tenant_id=tenant,
            series=tuple(str(x) for x in crudas if str(x).strip()), desde=desde,
            facturas=_si(e.get("facturas")), claves=_si(e.get("claves")),
        ))
    return salida


# ─── Consultas ──────────────────────────────────────────────────────────────

def empresas() -> list[EmpresaSAE]:
    """Todas las empresas conocidas, el SAE 10 primero.

    Un código del SAE 9 que choque con uno del SAE 10 EN EL MISMO TENANT se
    descarta con aviso: dejarlo entrar mezclaría los catálogos y los folios de
    las dos empresas en las mismas filas.
    """
    sae10 = empresas_sae10()
    ocupados = {(e.tenant_id, e.codigo) for e in sae10}
    salida = list(sae10)
    try:
        sae9 = empresas_sae9()
    except Exception as e:   # nada del SAE 9 puede dejar sin espejo al SAE 10
        log.warning("SAE 9: no pude leer SAE_FB_EMPRESAS (%s: %s) — no se espeja",
                    type(e).__name__, e)
        sae9 = []
    for e in sae9:
        if (e.tenant_id, e.codigo) in ocupados:
            log.warning("SAE 9: el código %s ya lo usa el SAE 10 en ese tenant — se salta",
                        e.codigo)
            continue
        ocupados.add((e.tenant_id, e.codigo))
        salida.append(e)
    return salida


def empresa_de(tenant_id, codigo: str) -> Optional[EmpresaSAE]:
    """La empresa `codigo` DE ESE tenant, o None. Nunca la de otro tenant."""
    t, c = str(tenant_id or ""), str(codigo or "").strip()
    return next((e for e in empresas() if e.tenant_id == t and e.codigo == c), None)


def del_tenant(tenant_id) -> list[EmpresaSAE]:
    t = str(tenant_id or "")
    return [e for e in empresas() if e.tenant_id == t]


def por_tenant(lista: Optional[list[EmpresaSAE]] = None) -> dict[str, list[EmpresaSAE]]:
    """{tenant: [empresas]} conservando el orden (el SAE 10 primero)."""
    salida: dict[str, list[EmpresaSAE]] = {}
    for e in (empresas() if lista is None else lista):
        salida.setdefault(e.tenant_id, []).append(e)
    return salida
