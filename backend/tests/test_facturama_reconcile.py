"""Reconciliación de intentos de timbrado PENDIENTE (FacturamaClient.buscar_cfdi).

Ejerce la LÓGICA real contra un httpx.MockTransport (sin red ni sandbox): el
ancla es OrderNumber (= serie+folio de la app), que Facturama NO indexa en su
`keyword` pero SÍ devuelve en el detalle. El contrato de seguridad es que jamás
se autorice re-timbrar sin haber verificado exhaustivamente que el CFDI no existe
(un falso "no existe" generaría un CFDI DUPLICADO ante el SAT).
"""
from datetime import datetime

import httpx

from app.services.facturama import FacturamaClient, FacturamaCredentials


def _make_client(list_items, details):
    """FacturamaClient cuyo _client() habla con un MockTransport en memoria.
    GET /cfdi → `list_items`;  GET /cfdi/{id} → `details[id]`."""
    creds = FacturamaCredentials(user="u", password="p",
                                 base_url="https://apisandbox.facturama.mx")
    client = FacturamaClient(creds)

    def handler(request):
        path = request.url.path
        if path == "/cfdi":
            return httpx.Response(200, json=list_items)
        if path.startswith("/cfdi/"):
            cid = path.rsplit("/", 1)[-1]
            if cid in details:
                return httpx.Response(200, json=details[cid])
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    client._client = lambda: httpx.Client(base_url=creds.base_url, transport=transport)
    return client


_UUID = {"Complement": {"TaxStamp": {"Uuid": "b8b5a3bd-07db-4e85-97e4-fb2393c0204c"}}}


def test_adopta_por_ordernumber_no_por_folio_facturama():
    # Dos CFDI al mismo receptor+emisor+total; solo uno tiene NUESTRO OrderNumber.
    lst = [
        {"Id": "A", "RfcIssuer": "EMI", "Total": 116.0, "Date": "2026-08-13T15:00:00"},
        {"Id": "B", "RfcIssuer": "EMI", "Total": 116.0, "Date": "2026-08-13T15:05:00"},
    ]
    det = {
        "A": {"Id": "A", "OrderNumber": "OTRA9"},
        "B": {"Id": "B", "OrderNumber": "A123", **_UUID},
    }
    ok, cfdi = _make_client(lst, det).buscar_cfdi(
        "A123", receptor_rfc="XAXX010101000", emisor_rfc="EMI", total=116)
    assert ok is True
    assert cfdi is not None and cfdi["Id"] == "B"


def test_inexistente_exhaustivo_autoriza_retimbrar():
    lst = [{"Id": "A", "RfcIssuer": "EMI", "Total": 50.0, "Date": "2026-08-13T15:00:00"}]
    det = {"A": {"Id": "A", "OrderNumber": "OTRA9"}}
    ok, cfdi = _make_client(lst, det).buscar_cfdi(
        "A123", receptor_rfc="X", emisor_rfc="EMI")
    assert ok is True and cfdi is None   # revisó todo y no está → seguro re-timbrar


def test_no_adopta_cfdi_de_otro_emisor():
    # Mismo OrderNumber pero emitido por OTRO tenant de la cuenta compartida.
    lst = [{"Id": "A", "RfcIssuer": "OTRO", "Total": 116.0, "Date": "2026-08-13T15:00:00"}]
    det = {"A": {"Id": "A", "OrderNumber": "A123", **_UUID}}
    ok, cfdi = _make_client(lst, det).buscar_cfdi(
        "A123", receptor_rfc="X", emisor_rfc="EMI")
    assert ok is True and cfdi is None   # excluido por emisor → no es nuestro


def test_total_distinto_no_excluye():
    # El total NUNCA excluye (un redondeo daría un falso "no existe" → duplicado).
    lst = [{"Id": "A", "RfcIssuer": "EMI", "Total": 116.001, "Date": "2026-08-13T15:00:00"}]
    det = {"A": {"Id": "A", "OrderNumber": "A123", **_UUID}}
    ok, cfdi = _make_client(lst, det).buscar_cfdi(
        "A123", receptor_rfc="X", emisor_rfc="EMI", total=115.99)
    assert ok is True and cfdi is not None and cfdi["Id"] == "A"


def test_ventana_de_fecha_excluye_viejos():
    lst = [{"Id": "A", "RfcIssuer": "EMI", "Total": 1.0, "Date": "2020-01-01T00:00:00"}]
    det = {"A": {"Id": "A", "OrderNumber": "A123", **_UUID}}
    ok, cfdi = _make_client(lst, det).buscar_cfdi(
        "A123", receptor_rfc="X", emisor_rfc="EMI", desde=datetime(2026, 8, 1))
    assert ok is True and cfdi is None   # fuera de ventana → exhaustivo, no existe


def test_truncado_no_afirma_inexistencia():
    # Más candidatos que el cap y ninguno matchea → NO se puede afirmar "no existe".
    lst = [{"Id": str(i), "RfcIssuer": "EMI", "Total": 1.0,
            "Date": "2026-08-13T15:00:00"} for i in range(5)]
    det = {str(i): {"Id": str(i), "OrderNumber": f"X{i}"} for i in range(5)}
    ok, cfdi = _make_client(lst, det).buscar_cfdi(
        "NOPE", receptor_rfc="X", emisor_rfc="EMI", cap=3)
    assert ok is False and cfdi is None   # inverificable → el llamador NO re-timbra


def test_ignora_cfdi_no_active_aunque_su_detalle_falle():
    # Un CFDI cancelado/invalid del MISMO cliente (detalle 401) NO debe bloquear
    # la reconciliación: se filtra por Status y no se pide su detalle.
    lst = [
        {"Id": "X", "RfcIssuer": "EMI", "Total": 0.0, "Status": "invalid",
         "Date": "2026-08-13T15:00:00"},
        {"Id": "B", "RfcIssuer": "EMI", "Total": 116.0, "Status": "active",
         "Date": "2026-08-13T15:05:00"},
    ]
    det = {"B": {"Id": "B", "OrderNumber": "A123", **_UUID}}  # 'X' sin detalle → 404
    ok, cfdi = _make_client(lst, det).buscar_cfdi(
        "A123", receptor_rfc="X", emisor_rfc="EMI")
    assert ok is True and cfdi is not None and cfdi["Id"] == "B"


def test_no_active_no_rompe_inexistencia_exhaustiva():
    lst = [{"Id": "X", "RfcIssuer": "EMI", "Total": 0.0, "Status": "canceled",
            "Date": "2026-08-13T15:00:00"}]
    ok, cfdi = _make_client(lst, {}).buscar_cfdi(   # 'X' se filtra, no se pide detalle
        "A123", receptor_rfc="X", emisor_rfc="EMI")
    assert ok is True and cfdi is None   # exhaustivo pese al CFDI cancelado ilegible


def test_detalle_ilegible_no_reconcilia():
    # Si el detalle de un candidato no se puede leer, no se afirma nada.
    lst = [{"Id": "A", "RfcIssuer": "EMI", "Total": 1.0, "Date": "2026-08-13T15:00:00"}]
    ok, cfdi = _make_client(lst, {}).buscar_cfdi(   # sin detalle → 404
        "A123", receptor_rfc="X", emisor_rfc="EMI")
    assert ok is False and cfdi is None


def test_lista_no_200_no_verifica():
    creds = FacturamaCredentials(user="u", password="p",
                                 base_url="https://apisandbox.facturama.mx")
    client = FacturamaClient(creds)
    client._client = lambda: httpx.Client(
        base_url=creds.base_url,
        transport=httpx.MockTransport(lambda req: httpx.Response(500, json={})))
    ok, cfdi = client.buscar_cfdi("A123", receptor_rfc="X")
    assert ok is False and cfdi is None
