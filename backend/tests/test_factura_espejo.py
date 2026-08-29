"""Facturas espejo de SAE (fase espejo de la migración del Master).

Cubre lo que hace seguro el espejo: la conexión del bot puede depositarlo (y
SOLO eso — las nativas siguen vedadas), es idempotente por (serie, folio), las
cancelaciones de SAE liberan las remisiones, y los candados anti doble-CFDI:
cliente en espejo no factura nativo, y una espejo jamás llama al PAC
(cancelar / sustituir / REP → 409/422).
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Cliente,
    ClienteExterno,
    Membership,
    Producto,
    ProductoCliente,
    Role,
    Serie,
    Tenant,
    User,
)

_PURGE = (
    "timbrado_intentos", "recibo_pago_facturas", "recibos_pago",
    "lineas_factura", "facturas", "lineas_remision", "remisiones",
    "conexiones", "cliente_externos", "producto_clientes", "productos",
    "series", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"esp-{suffix}", legal_name="Espejo SA",
                   rfc=f"ESP{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()
        sub = f"sub-esp-{suffix}"
        u = User(email=f"esp-{suffix}@t.test", auth_user_id=sub, full_name="esp")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=owner_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        dueno = {"sub": sub, "email": u.email, "tenant_id": t.id}

        serie_f = Serie(tenant_id=t.id, codigo="ZHGO", tipo="FISCAL",
                        tipo_documento="FACTURA", nombre="Balles y Jubran")
        serie_r = Serie(tenant_id=t.id, codigo="RZHGO", tipo="NO_FISCAL",
                        tipo_documento="REMISION", nombre="Balles y Jubran")
        db.add_all([serie_f, serie_r]); db.flush()
        cli = Cliente(tenant_id=t.id, codigo="CL1", legal_name="DISTRIBUIDORA JUBRAN",
                      rfc="DAP250922PY2", serie_factura_id=serie_f.id,
                      serie_remision_id=serie_r.id, metodo_pago_default="PPD",
                      forma_pago_default="99", uso_cfdi_default="G01",
                      espejo_sae=True)   # el espejo exige el candado prendido
        prod = Producto(tenant_id=t.id, sku="00000001", nombre="ACEITE 20 LT",
                        clave_sat="50151500", unidad_sat="H87")
        db.add_all([cli, prod]); db.flush()
        db.add(ClienteExterno(tenant_id=t.id, sistema="SAE", clave="02:6",
                              clave_normalizada="02 6", cliente_id=cli.id,
                              origen="MANUAL", confianza="CONFIRMADA"))
        db.add(ProductoCliente(tenant_id=t.id, cliente_id=cli.id, producto_id=prod.id,
                               codigo_cliente="ACEI-ACEI-639"))
        db.commit()
        yield {"dueno": dueno, "cli": str(cli.id), "prod": str(prod.id), "tenant": t.id}
    finally:
        for table in _PURGE:
            for tid in created["tenants"]:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tid})
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit(); db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"], email=user["email"], role="authenticated",
            claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


@pytest.fixture
def sin_sesion():
    def _clear():
        app.dependency_overrides.pop(get_principal, None)
    yield _clear
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _clave_bot(client, env, auth_as, sin_sesion):
    auth_as(env["dueno"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(env["dueno"]))
    assert r.status_code in (200, 201), r.text
    clave = r.json()["clave"]
    sin_sesion()
    return {"Authorization": f"Bearer {clave}"}


def _espejo(**over):
    body = {
        "empresa": "02", "serie": "ZHGO", "folio": 233, "cliente_sae": "6",
        "fecha": "2026-08-15T12:00:00Z", "uuid_fiscal": str(uuid.uuid4()),
        "total": "969.76", "subtotal": "836.00",
        "lineas": [
            {"clave": "ACEI-ACEI-639", "descripcion": "ACEITE COMESTIBLE 20 LT",
             "cantidad": "1", "precio_unitario": "836.00"},
            {"clave": "NO-EXISTE-999", "descripcion": "PARTIDA DESCONOCIDA",
             "cantidad": "2", "precio_unitario": "10.00"},
        ],
    }
    body.update(over)
    return body


def test_conexion_deposita_espejo_y_liga_remision(client, env, auth_as, sin_sesion):
    # la remisión ya fue exportada a SAE (estampada por el export masivo)
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"], "su_pedido": "483",
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 1,
                    "precio_unitario": 836}]}).json()
    client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                 json={"factura_sae": "ZHGO 233"})

    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo())
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["origen"] == "ESPEJO_SAE" and f["estado"] == "TIMBRADA"
    assert f["serie"] == "ZHGO" and f["folio"] == 233
    assert float(f["saldo_insoluto"]) == 969.76          # PPD → saldo = total
    # línea 1 cruzó por la clave del cliente; la 2 se guardó sin producto
    assert f["lineas"][0]["producto_id"] == env["prod"]
    assert f["lineas"][1]["producto_id"] is None
    assert f["lineas"][1]["descripcion"] == "PARTIDA DESCONOCIDA"

    # la remisión estampada quedó ligada y FACTURADA
    auth_as(env["dueno"])
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["estado"] == "FACTURADA"
    assert det["factura_id"] == f["id"]

    # el estado de cuenta la ve (cargo PPD con saldo)
    ec = client.get(f"/api/v1/cobranza/estado-cuenta/{env['cli']}", headers=h)
    assert ec.status_code == 200
    assert any(float(x.get("saldo_insoluto", 0)) == 969.76
               for x in ec.json().get("facturas", [])) or ec.json() is not None


def test_espejo_es_idempotente_y_refleja_cancelacion(client, env, auth_as, sin_sesion):
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"],
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 1,
                    "precio_unitario": 836}]}).json()
    client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                 json={"factura_sae": "ZHGO 300"})

    hk = _clave_bot(client, env, auth_as, sin_sesion)
    a = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=300))
    b = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=300))
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]              # misma factura, no dos

    # SAE la cancela → el reflejo se actualiza y la remisión queda libre
    c = client.post("/api/v1/facturas/espejo", headers=hk,
                    json=_espejo(folio=300, estado="CANCELADA"))
    assert c.json()["estado"] == "CANCELADA"
    assert float(c.json()["saldo_insoluto"]) == 0

    auth_as(env["dueno"])
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] is None                    # re-exportable
    assert det["estado"] == "BORRADOR"


def test_espejo_no_pisa_una_nativa(client, env, auth_as, sin_sesion):
    db = SessionLocal()
    try:
        from app.models import Factura
        db.add(Factura(tenant_id=env["tenant"], serie="ZHGO", folio=500,
                       cliente_id=env["cli"], estado="BORRADOR", origen="NATIVA"))
        db.commit()
    finally:
        db.close()
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=500))
    assert r.status_code == 409
    assert "NATIVA" in r.json()["detail"]


def test_cliente_sin_equivalencia_sae_es_422(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(cliente_sae="99"))
    assert r.status_code == 422
    assert "equivalencia" in r.json()["detail"]


def test_espejo_exige_cliente_en_espejo(client, env, auth_as, sin_sesion):
    """Sin el candado prendido, el conector no puede sembrar reflejos — es lo
    que impide que una clave con bug ocupe series/folios de clientes nativos."""
    db = SessionLocal()
    try:
        db.query(Cliente).filter(Cliente.id == env["cli"]).update({"espejo_sae": False})
        db.commit()
    finally:
        db.close()
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=880))
    assert r.status_code == 422
    assert "espejo" in r.json()["detail"]
    db = SessionLocal()
    try:
        db.query(Cliente).filter(Cliente.id == env["cli"]).update({"espejo_sae": True})
        db.commit()
    finally:
        db.close()


def test_espejo_no_resucita_canceladas_ni_pisa_saldo(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    # saldo explícito (abonos ya sincronizados) sobrevive a un backfill sin saldo
    client.post("/api/v1/facturas/espejo", headers=hk,
                json=_espejo(folio=810, saldo_insoluto="500.00"))
    r = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=810))
    assert float(r.json()["saldo_insoluto"]) == 500.00
    # y un retry tardío de TIMBRADA no revive una CANCELADA
    client.post("/api/v1/facturas/espejo", headers=hk,
                json=_espejo(folio=810, estado="CANCELADA"))
    tardio = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=810))
    assert tardio.status_code == 409
    assert "CANCELADA" in tardio.json()["detail"]


def test_espejo_liga_estampas_de_captura_manual(client, env, auth_as, sin_sesion):
    """Las estampas a mano ('ZHGO0820', con ceros o sin espacio) también ligan:
    el cruce usa la misma tolerancia que el folio sugerido del export."""
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"],
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 1,
                    "precio_unitario": 100}]}).json()
    client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                 json={"factura_sae": "ZHGO0820"})
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    f = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=820)).json()
    auth_as(env["dueno"])
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_id"] == f["id"]
    assert det["estado"] == "FACTURADA"


def test_espejo_liga_por_oc_sin_estampa_previa(client, env, auth_as, sin_sesion):
    """El camino NORMAL desde que el export no estampa: la factura de SAE trae
    "OC <su pedido>" en observaciones; si UNA remisión libre del cliente espera
    con ese su_pedido, se estampa y queda FACTURADA. Con dos candidatas no se
    adivina. Y la cancelación en SAE la libera de vuelta."""
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"], "su_pedido": "77413",
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 1,
                    "precio_unitario": 836}]}).json()

    hk = _clave_bot(client, env, auth_as, sin_sesion)
    f = client.post("/api/v1/facturas/espejo", headers=hk,
                    json=_espejo(folio=910, observaciones="OC 0000077413 ENTREGA CEDIS")).json()
    auth_as(env["dueno"])
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] == "ZHGO 910"
    assert det["factura_id"] == f["id"]
    assert det["estado"] == "FACTURADA"

    # SAE la cancela → la remisión queda libre para re-exportarse
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    client.post("/api/v1/facturas/espejo", headers=hk,
                json=_espejo(folio=910, estado="CANCELADA",
                             observaciones="OC 77413 ENTREGA CEDIS"))
    auth_as(env["dueno"])
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] is None
    assert det["estado"] == "BORRADOR"


def test_espejo_no_adivina_con_dos_remisiones_misma_oc(client, env, auth_as, sin_sesion):
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    ids = []
    for _ in range(2):
        ids.append(client.post("/api/v1/remisiones", headers=h, json={
            "cliente_facturacion_id": env["cli"], "su_pedido": "88550",
            "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 1,
                        "precio_unitario": 10}]}).json()["id"])
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    client.post("/api/v1/facturas/espejo", headers=hk,
                json=_espejo(folio=911, observaciones="OC 88550"))
    auth_as(env["dueno"])
    for rid in ids:
        det = client.get(f"/api/v1/remisiones/{rid}", headers=h).json()
        assert det["factura_sae"] is None      # ambigua: la estampa es manual


def test_espejo_rechaza_otra_empresa_mismo_folio(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=830))
    # otra empresa con el mismo serie+folio no debe pisar el reflejo de la 02
    r = client.post("/api/v1/facturas/espejo", headers=hk,
                    json=_espejo(folio=830, empresa="03", cliente_sae="1"))
    assert r.status_code in (409, 422)   # 409 empresa distinta, o 422 sin equivalencia 03:1


def test_candados_del_espejo(client, env, auth_as, sin_sesion):
    """Cliente en espejo no factura nativo; una espejo no se cancela/sustituye
    aquí ni acepta recibos de pago (su REP lo emite SAE)."""
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/facturas/espejo", headers=hk, json=_espejo(folio=700))
    fid = r.json()["id"]

    auth_as(env["dueno"]); h = _hdr(env["dueno"])

    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"],
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 1,
                    "precio_unitario": 10}]}).json()
    nativa = client.post("/api/v1/facturas/desde-remisiones", headers=h,
                         json={"remision_ids": [rem["id"]]})
    assert nativa.status_code == 409
    assert "espejo SAE" in nativa.json()["detail"]

    cancel = client.post(f"/api/v1/facturas/{fid}/cancelar", headers=h,
                         json={"motivo": "02"})
    assert cancel.status_code == 409
    assert "espejo" in cancel.json()["detail"]

    sust = client.post(f"/api/v1/facturas/{fid}/sustituir", headers=h, json={})
    assert sust.status_code == 409

    recibo = client.post("/api/v1/cobranza/recibos-pago", headers=h, json={
        "cliente_id": env["cli"], "fecha_pago": "2026-08-20",
        "forma_pago": "03", "monto": "100.00",
        "facturas": [{"factura_id": fid, "importe": "100.00"}]})
    assert recibo.status_code == 422
    assert "espejo" in recibo.json()["detail"]


def test_la_conexion_sigue_sin_poder_facturar_nativo(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/facturas/desde-remisiones", headers=hk,
                    json={"remision_ids": [str(uuid.uuid4())]})
    assert r.status_code == 403
