"""Cliente HTTP para Facturama (PAC para timbrado CFDI 4.0).

Thin wrapper sobre la API de Facturama. El ambiente lo decide `FACTURAMA_BASE_URL`:
  - sandbox  → https://apisandbox.facturama.mx  (default, timbres de prueba)
  - producción → https://api.facturama.mx       (timbres REALES ante el SAT)

Guard duro: para apuntar a un host que NO sea el sandbox se exige además
`FACTURAMA_ALLOW_PRODUCTION=true`. Así nunca se timbra real "por accidente":
hace falta cambiar la URL **y** levantar el flag de forma explícita.

Si no hay credenciales (FACTURAMA_USER / FACTURAMA_PASSWORD) los métodos
levantan `FacturamaConfigError` al ejecutar (modo stub para CI/tests offline).

Uso:
    client = FacturamaClient.from_settings(settings)
    if client.configured:
        result = client.create_cfdi(payload)
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_SANDBOX_HOST = "apisandbox.facturama.mx"


def _importe_igual(a, b, tol: float = 0.005) -> bool:
    """Igualdad de importes con tolerancia de medio centavo (los totales de la
    lista de Facturama son float; el de la app es Decimal)."""
    try:
        return abs(float(a) - float(b)) < tol
    except (TypeError, ValueError):
        return False


def _parse_fecha(s) -> Optional[datetime]:
    """Fecha naive (hora local MX) que Facturama devuelve en la lista, p. ej.
    '2026-08-13T15:28:01'. None si no se puede parsear."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:19])
    except (TypeError, ValueError):
        return None


def _fecha_ge(fecha_str, desde) -> bool:
    """¿La fecha del CFDI es >= `desde`? Permisivo: si el CFDI no trae fecha
    parseable NO se excluye (un falso descarte causaría un re-timbrado duplicado).
    `desde` puede venir tz-aware (created_at UTC); se compara naive y el llamador
    ya aplicó un margen amplio que absorbe el desfase de zona (~6 h)."""
    dt = _parse_fecha(fecha_str)
    if dt is None:
        return True
    d = desde.replace(tzinfo=None) if getattr(desde, "tzinfo", None) else desde
    try:
        return dt >= d
    except TypeError:
        return True


def _fecha_orden(fecha_str):
    """Clave de orden: más reciente primero; sin fecha → al final."""
    dt = _parse_fecha(fecha_str)
    if dt is None:
        return float("inf")
    try:
        return -dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return float("inf")


class FacturamaError(Exception):
    pass


class FacturamaConfigError(FacturamaError):
    pass


@dataclass
class FacturamaCredentials:
    user: str
    password: str
    base_url: str = "https://apisandbox.facturama.mx"
    allow_production: bool = False

    @classmethod
    def from_settings(cls, settings) -> Optional["FacturamaCredentials"]:
        u = getattr(settings, "FACTURAMA_USER", None)
        p = getattr(settings, "FACTURAMA_PASSWORD", None)
        url = getattr(settings, "FACTURAMA_BASE_URL", "https://apisandbox.facturama.mx")
        allow_prod = bool(getattr(settings, "FACTURAMA_ALLOW_PRODUCTION", False))
        if not u or not p:
            return None
        return cls(user=u, password=p, base_url=url.rstrip("/"), allow_production=allow_prod)


class FacturamaClient:
    """Wrapper minimal: timbrar, cancelar, descargar PDF/XML (sandbox o producción)."""

    def __init__(self, creds: Optional[FacturamaCredentials], timeout: float = 30.0):
        self._creds = creds
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings) -> "FacturamaClient":
        return cls(FacturamaCredentials.from_settings(settings))

    @property
    def configured(self) -> bool:
        return self._creds is not None

    @property
    def is_sandbox(self) -> bool:
        """True si apunta al host de pruebas (o si no hay credenciales)."""
        return not self._creds or _SANDBOX_HOST in self._creds.base_url

    @property
    def is_production(self) -> bool:
        return not self.is_sandbox

    @property
    def env_label(self) -> str:
        return "sandbox" if self.is_sandbox else "producción"

    def _client(self) -> httpx.Client:
        if not self._creds:
            raise FacturamaConfigError(
                "Facturama no configurado: define FACTURAMA_USER y FACTURAMA_PASSWORD en .env"
            )
        if _SANDBOX_HOST not in self._creds.base_url and not self._creds.allow_production:
            # Guard: apuntar fuera del sandbox exige el flag explícito de producción.
            raise FacturamaError(
                f"Timbrado bloqueado: FACTURAMA_BASE_URL no es sandbox ({_SANDBOX_HOST}). "
                f"Para producción define FACTURAMA_ALLOW_PRODUCTION=true."
            )
        return httpx.Client(
            base_url=self._creds.base_url,
            auth=(self._creds.user, self._creds.password),
            timeout=self._timeout,
            headers={"Content-Type": "application/json"},
        )

    # ─── CFDI 4.0 ──────────────────────────────────────────────────────────
    def create_cfdi(self, payload: dict) -> dict:
        with self._client() as c:
            r = c.post("/3/cfdis", json=payload)
            if r.status_code >= 400:
                # Solo serie/folio + error del PAC: el payload completo trae PII
                # fiscal del receptor (RFC, nombre, domicilio) y no va a logs.
                log.error(
                    "Facturama /3/cfdis %s | Serie=%s Folio=%s | RESPONSE=%s",
                    r.status_code, payload.get("Serie"), payload.get("Folio"), r.text[:1000],
                )
                raise FacturamaError(f"create_cfdi failed: {r.status_code} {r.text}")
            return r.json()

    def create_cfdi_pago(self, payload: dict) -> dict:
        """Timbra un Complemento de Pago 2.0 (CFDI tipo P). POST /3/cfdis
        (API Lite, misma que las facturas; /2/cfdis quedó deprecado)."""
        with self._client() as c:
            r = c.post("/3/cfdis", json=payload)
            if r.status_code >= 400:
                log.error(
                    "Facturama /3/cfdis (REP) %s | Serie=%s Folio=%s | RESPONSE=%s",
                    r.status_code, payload.get("Serie"), payload.get("Folio"), r.text[:1000],
                )
                raise FacturamaError(f"create_cfdi_pago failed: {r.status_code} {r.text}")
            return r.json()

    def buscar_cfdi(
        self,
        order_number: str,
        *,
        receptor_rfc: str,
        emisor_rfc: Optional[str] = None,
        total=None,
        desde=None,
        cap: int = 100,
    ) -> tuple[bool, Optional[dict]]:
        """Reconcilia un intento de timbrado PENDIENTE: ¿el CFDI que ese intento
        pudo crear antes de morir (crash entre create_cfdi y el commit) ya existe
        en Facturama? Ancla en `OrderNumber` (= serie+folio de la app, inyectado al
        crear el CFDI).

        Facturama NO indexa OrderNumber en su `keyword` (solo Folio + RFC/Nombre
        del receptor), pero SÍ lo devuelve en el detalle del CFDI. Por eso se ACOTA
        por RFC del receptor (keyword) + emisor (RfcIssuer, para no adoptar el CFDI
        de otro tenant en cuentas multi-emisor) + fecha, y se CONFIRMA el
        OrderNumber exacto leyendo el detalle de cada candidato.

        Contrato (para nunca re-timbrar a ciegas → nunca duplicar ante el SAT):
          (True,  cfdi) → existe: adoptar, JAMÁS re-timbrar.
          (True,  None) → se verificó EXHAUSTIVAMENTE que no existe: seguro re-timbrar.
          (False, None) → no se pudo verificar (red/API, un detalle ilegible, o
                          demasiados candidatos para revisar): el llamador NO debe
                          re-timbrar (mejor un 409 y reintentar que un duplicado).

        `total` NUNCA excluye candidatos (un redondeo distinto daría un falso "no
        existe" → duplicado); solo prioriza para que el `cap` conserve los más
        probables. `desde` (fecha mínima) sí acota, con margen amplio.
        """
        objetivo = str(order_number).strip()
        if not objetivo:
            return False, None
        emisor = str(emisor_rfc).strip().upper() if emisor_rfc else None
        try:
            with self._client() as c:
                r = c.get("/cfdi", params={"type": "issued", "keyword": str(receptor_rfc)})
                if r.status_code != 200:
                    return False, None
                data = r.json()
                items = data if isinstance(data, list) else []
                cands = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    # Solo CFDI VIGENTES: un timbrado que sí cuajó (el caso peligroso
                    # a reconciliar) siempre queda "active"; los cancelados/invalid
                    # jamás son el objetivo y además su detalle puede no ser legible
                    # (p. ej. 401), lo que bloquearía la reconciliación. Status vacío
                    # → se incluye (permisivo, no descartar el objetivo por una etiqueta).
                    estado = str(it.get("Status") or "").strip().lower()
                    if estado and estado != "active":
                        continue
                    if emisor and str(it.get("RfcIssuer") or "").strip().upper() != emisor:
                        continue
                    if desde is not None and not _fecha_ge(it.get("Date"), desde):
                        continue
                    cands.append(it)
                # Prioriza: mismo total primero, luego más recientes (para que el
                # cap conserve los candidatos más probables). El orden no afecta la
                # correctitud —el OrderNumber confirma—, solo qué se revisa si hay
                # más candidatos que `cap`.
                cands.sort(key=lambda it: (
                    0 if (total is not None and _importe_igual(it.get("Total"), total)) else 1,
                    _fecha_orden(it.get("Date")),
                ))
                truncado = len(cands) > cap
                for it in cands[:cap]:
                    cid = it.get("Id")
                    if not cid:
                        continue
                    d = c.get(f"/cfdi/{cid}", params={"type": "issued"})
                    if d.status_code != 200:
                        return False, None  # candidato no verificable → no re-timbrar
                    det = d.json()
                    if str(det.get("OrderNumber") or "").strip() == objetivo:
                        return True, det
                # Sin match: solo es un "no existe" fiable si se revisaron TODOS los
                # candidatos; si el cap dejó fuera algunos, no se puede afirmar.
                return (False, None) if truncado else (True, None)
        except Exception:  # noqa: BLE001 — sin verificación no hay re-timbrado
            return False, None

    def cancel_cfdi(self, cfdi_id: str, motive: str, uuid_replacement: Optional[str] = None) -> dict:
        params = {"type": "issued", "motive": motive}
        if uuid_replacement:
            params["uuidReplacement"] = uuid_replacement
        with self._client() as c:
            r = c.delete(f"/cfdi/{cfdi_id}", params=params)
            if r.status_code >= 400:
                raise FacturamaError(f"cancel_cfdi failed: {r.status_code} {r.text}")
            return r.json() if r.text else {}

    # ─── Validación de RFC en el SAT ──────────────────────────────────────
    def validar_rfc(self, rfc: str) -> dict:
        """Consulta el estado de un RFC en el SAT vía Facturama.

        GET /customers/status?rfc=... → {Rfc, FormatoCorrecto, Activo, Localizado}
        (más campos como la lista 69-B si aplican, que se pasan tal cual).
        Consume 1 folio de Facturama por llamada (es un botón manual).
        """
        with self._client() as c:
            r = c.get("/customers/status", params={"rfc": rfc})
            if r.status_code >= 400:
                raise FacturamaError(f"validar_rfc failed: {r.status_code} {r.text[:500]}")
            return r.json()

    def validar_completo(self, rfc: str, name: str, zip_code: str, fiscal_regime: str) -> dict:
        """Valida que Nombre, Código Postal y Régimen Fiscal coincidan con lo
        que el SAT tiene registrado para ese RFC.

        POST /customers/validate → {Rfc, ExistRfc, MatchName, MatchZipCode,
        MatchFiscalRegime}. A diferencia de `validar_rfc` (que solo checa el
        RFC en sí), esto atrapa un CP o régimen mal capturado ANTES de que el
        timbrado lo rechace. Consume 1 folio de Facturama por llamada.
        """
        body = {"Rfc": rfc, "Name": name, "ZipCode": zip_code, "FiscalRegime": fiscal_regime}
        with self._client() as c:
            r = c.post("/customers/validate", json=body)
            if r.status_code >= 400:
                log.error("Facturama /customers/validate %s | RESPONSE=%s", r.status_code, r.text[:1000])
                raise FacturamaError(f"validar_completo failed: {r.status_code} {r.text[:500]}")
            return r.json()

    # ─── CSD (sellos digitales) — multi-emisor / api-lite ─────────────────
    def subir_csd(
        self, rfc: str, certificate_b64: str, private_key_b64: str, password: str
    ) -> dict:
        """Sube el CSD del emisor a Facturama (multi-emisor).

        POST /api-lite/csds con el .cer y .key en base64 + contraseña de la
        llave privada. Levanta FacturamaError si la respuesta es >=400.
        """
        body = {
            "Rfc": rfc,
            "Certificate": certificate_b64,
            "PrivateKey": private_key_b64,
            "PrivateKeyPassword": password,
        }
        with self._client() as c:
            r = c.post("/api-lite/csds", json=body)
            if r.status_code >= 400:
                log.error("Facturama /api-lite/csds %s | RESPONSE=%s", r.status_code, r.text[:1000])
                raise FacturamaError(f"subir_csd failed: {r.status_code} {r.text[:500]}")
            # Facturama a veces responde 200 con cuerpo vacío (sin JSON) — éxito
            # igual; no hay nada que parsear ni que el llamador necesite leer.
            if not r.text.strip():
                return {"Rfc": rfc}
            return r.json()

    def listar_csds(self) -> list:
        """Lista los CSD cargados en Facturama. [] si error o no 200.

        Incluye el certificado y la llave privada tal cual los devuelve
        Facturama — uso interno (p. ej. `_csd_match` en onboarding.py). Para
        exponer al cliente HTTP usar `csd_public_fields`, que retira los
        campos sensibles.
        """
        try:
            with self._client() as c:
                r = c.get("/api-lite/csds")
                if r.status_code != 200:
                    return []
                data = r.json()
                return data if isinstance(data, list) else []
        except (FacturamaError, Exception):  # noqa: BLE001 — listado tolerante
            return []

    # ─── Descarga de PDF/XML del CFDI ya timbrado ──────────────────────────
    def download_pdf(self, cfdi_id: str) -> bytes:
        with self._client() as c:
            r = c.get(f"/cfdi/pdf/issued/{cfdi_id}")
            if r.status_code >= 400:
                raise FacturamaError(f"download_pdf failed: {r.status_code} {r.text[:200]}")
            return base64.b64decode(r.json().get("Content", ""))

    def download_xml(self, cfdi_id: str) -> bytes:
        with self._client() as c:
            r = c.get(f"/cfdi/xml/issued/{cfdi_id}")
            if r.status_code >= 400:
                raise FacturamaError(f"download_xml failed: {r.status_code} {r.text[:200]}")
            return base64.b64decode(r.json().get("Content", ""))


def csd_serial_number(certificate_b64: str) -> Optional[str]:
    """Número de serie legible del CSD (convención SAT: el entero ASN.1 del
    serialNumber, leído como bytes ASCII, es el número que muestra el SAT)."""
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(base64.b64decode(certificate_b64))
        n = cert.serial_number
        return n.to_bytes((n.bit_length() + 7) // 8, "big").decode("ascii")
    except Exception:  # noqa: BLE001 — best-effort, no debe romper el listado
        return None


def csd_public_fields(csds: list) -> list[dict]:
    """Campos seguros de mostrar al cliente: RFC, serie y vigencia.

    Nunca incluir `Certificate`/`PrivateKey`/`PrivateKeyPassword` — son la
    llave privada del emisor y su contraseña; Facturama los devuelve en el
    listado pero no deben llegar al navegador.
    """
    out = []
    for c in csds or []:
        if not isinstance(c, dict):
            continue
        cert_b64 = c.get("Certificate") or ""
        out.append(
            {
                "Rfc": c.get("Rfc"),
                "SerialNumber": csd_serial_number(cert_b64) if cert_b64 else None,
                "ExpirationDate": c.get("CsdExpirationDate"),
            }
        )
    return out


def startup_warnings(settings) -> list[str]:
    """Inconsistencias de configuración de Facturama detectables al arranque.

    No lanza excepciones (el guard real vive en _client()); solo devuelve avisos
    para registrarlos en el log y hacer visible una configuración peligrosa antes
    de timbrar — sobre todo combinaciones de producción mal puestas.
    """
    base_url = getattr(settings, "FACTURAMA_BASE_URL", "") or ""
    is_prod = _SANDBOX_HOST not in base_url
    allow_prod = bool(getattr(settings, "FACTURAMA_ALLOW_PRODUCTION", False))
    fake_cancel = bool(getattr(settings, "FACTURAMA_FAKE_CANCEL", False))
    has_creds = bool(getattr(settings, "FACTURAMA_USER", "")) and bool(
        getattr(settings, "FACTURAMA_PASSWORD", "")
    )
    multiemisor = bool(getattr(settings, "FACTURAMA_MULTIEMISOR", False))

    warnings: list[str] = []
    if is_prod and not allow_prod:
        warnings.append(
            "FACTURAMA_BASE_URL apunta a PRODUCCIÓN pero FACTURAMA_ALLOW_PRODUCTION=false: "
            "el timbrado quedará BLOQUEADO hasta poner el flag en true."
        )
    if is_prod and fake_cancel:
        warnings.append(
            "PRODUCCIÓN con FACTURAMA_FAKE_CANCEL=true: las cancelaciones NO se "
            "enviarán al SAT (el CFDI seguirá vigente ante el SAT aunque la app lo "
            "marque CANCELADA). Pon FACTURAMA_FAKE_CANCEL=false en producción."
        )
    # En multi-emisor el emisor se arma por-tenant (no se usa ISSUER_RFC global),
    # así que su ausencia es lo ESPERADO; solo se avisa en modo single-emisor.
    if is_prod and not multiemisor and not getattr(settings, "FACTURAMA_ISSUER_RFC", ""):
        warnings.append(
            "PRODUCCIÓN sin FACTURAMA_ISSUER_RFC: se usará el CSD por defecto de la "
            "cuenta Facturama. Verifica que sea el emisor correcto."
        )
    if is_prod and allow_prod and not has_creds:
        warnings.append(
            "PRODUCCIÓN habilitada pero faltan FACTURAMA_USER / FACTURAMA_PASSWORD."
        )
    return warnings
