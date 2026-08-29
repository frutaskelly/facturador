"""Export masivo para SAE (fase espejo): layout exacto, folios con relleno,
rastro del export (export_sae_at, SIN estampar folios), y los candados que evitan
duplicar documentos en SAE (re-export, cliente sin clave, partida sin código)."""
import io
import uuid

import pytest
import xlrd
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
from app.services.export_sae import FACTURA_HDR, PEDIDO_HDR, folio_sae

_PURGE = (
    "lineas_factura", "facturas", "lineas_remision", "remisiones", "cliente_externos",
    "producto_clientes", "productos", "series", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"sae-{suffix}", legal_name="Export SAE SA",
                   rfc=f"SAE{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-sae-{suffix}"
        u = User(email=f"sae-{suffix}@t.test", auth_user_id=sub, full_name="sae")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        admin = {"sub": sub, "email": u.email, "tenant_id": t.id}

        serie_f = Serie(tenant_id=t.id, codigo="ZHGO", tipo="FISCAL",
                        tipo_documento="FACTURA", nombre="Balles y Jubran")
        serie_r = Serie(tenant_id=t.id, codigo="RZHGO", tipo="NO_FISCAL",
                        tipo_documento="REMISION", nombre="Balles y Jubran")
        db.add_all([serie_f, serie_r]); db.flush()

        cli = Cliente(tenant_id=t.id, codigo="CL1", legal_name="OPERADORA BALLES",
                      rfc="OBV191007BS1", serie_factura_id=serie_f.id,
                      espejo_sae=True,   # el export FACTURA lo exige (29-ago)
                      serie_remision_id=serie_r.id, metodo_pago_default="PPD",
                      forma_pago_default="99", uso_cfdi_default="G01")
        prod = Producto(tenant_id=t.id, sku="00000001", nombre="ACEITE 20 LT",
                        clave_sat="50151500", unidad_sat="H87")
        db.add_all([cli, prod]); db.flush()
        db.add(ClienteExterno(tenant_id=t.id, sistema="SAE", clave="02:7",
                              clave_normalizada="02 7", cliente_id=cli.id,
                              origen="MANUAL", confianza="CONFIRMADA"))
        db.add(ProductoCliente(tenant_id=t.id, cliente_id=cli.id, producto_id=prod.id,
                               codigo_cliente="ACEI-ACEI-639"))
        db.commit()
        yield {"admin": admin, "cli": str(cli.id), "prod": str(prod.id), "tenant": t.id}
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


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _rem(client, h, env, **over):
    body = {"cliente_facturacion_id": env["cli"], "su_pedido": "24736",
            "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 5,
                        "precio_unitario": 836}]}
    body.update(over)
    r = client.post("/api/v1/remisiones", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_folio_sae_es_serie_mas_10():
    # El relleno de CVE_DOC no es por serie: SIEMPRE serie + número en 10.
    assert folio_sae("ZHGO", 233) == "ZHGO       233"          # 14 chars
    assert folio_sae("ZMAFAN", 7) == "ZMAFAN         7"        # 16 chars
    assert folio_sae("ZEHMOHOS", 1560) == "ZEHMOHOS      1560" # 18 chars


def test_export_factura_layout_y_espejo(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)

    # preview: sugiere serie del cliente y pide folio
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
    assert pv["ok"] is True, pv
    assert pv["empresa"] == "02"
    assert pv["series"] == [{"serie": "ZHGO", "remisiones": 1, "folio_sugerido": None}]

    # export: genera el .xls y estampa el espejo
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "FACTURA",
                          "folios": {"ZHGO": 233}, "fecha": "2026-08-28"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.ms-excel")

    libro = xlrd.open_workbook(file_contents=r.content)
    hoja = libro.sheet_by_name("Facturas")
    assert [c.value for c in hoja.row(0)] == FACTURA_HDR
    fila = [c.value for c in hoja.row(1)]
    assert fila[0] == "ZHGO       233"          # relleno exacto
    assert fila[1] == "7"                       # número de cliente SAE, no CLI-001
    assert fila[2] == "08/28/2026"              # MM/DD/YYYY — la trampa de fechas
    assert fila[4] == "ACEI-ACEI-639"           # CVE_ART del cliente, no el SKU
    assert fila[5] == 5.0 and fila[6] == 836.0
    assert fila[21:24] == ["PPD", "99", "G01"]
    assert fila[26].startswith("OC 24736")      # la llave de conciliación

    # NO estampó nada: el folio del archivo es una propuesta, la verdad la
    # pone el espejo cuando la factura existe en SAE (regla del 29-ago-2026).
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] is None
    assert det["estado"] == "BORRADOR"

    # re-exportar está PERMITIDO (el archivo pudo no subirse nunca), con aviso
    pv2 = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                      json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
    assert pv2["ok"] is True
    assert any("ya salió en un archivo" in a for a in pv2["avisos"])
    r2 = client.post("/api/v1/remisiones/export-sae", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 233}})
    assert r2.status_code == 200

    # cuando la factura SE CONFIRMA (espejo o captura manual), ahí sí: candado
    client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                 json={"factura_sae": "ZHGO 233"})
    r3 = client.post("/api/v1/remisiones/export-sae", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 234}})
    assert r3.status_code == 422
    assert "ZHGO 233" in r3.json()["detail"]

    # y el folio sugerido del siguiente lote sale de la marca confirmada
    rem2 = _rem(client, h, env, su_pedido="24737")
    pv3 = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                      json={"ids": [rem2["id"]], "tipo": "FACTURA"}).json()
    assert pv3["series"][0]["folio_sugerido"] == 234


def test_export_factura_sin_folio_es_422(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "FACTURA"})
    assert r.status_code == 422
    assert "folio inicial" in r.json()["detail"]
    # y NO estampó nada (estampa y archivo viajan juntos o nada)
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] is None


def test_export_pedido_lleva_la_oc_del_cliente(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "PEDIDO"})
    assert r.status_code == 200, r.text
    hoja = xlrd.open_workbook(file_contents=r.content).sheet_by_name("Pedidos")
    assert [c.value for c in hoja.row(0)] == PEDIDO_HDR
    fila = [c.value for c in hoja.row(1)]
    assert fila[0] == "24736"          # la OC del cliente; SAE folia al importar
    assert fila[4] == "ACEI-ACEI-639"
    # PEDIDO no estampa espejo: la factura aún no existe
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] is None


def test_export_rechaza_colision_de_folios(client, env, auth_as):
    """El rango propuesto no puede pisar folios que YA existen — en marcas
    confirmadas (espejo/captura manual) o en facturas. Las propuestas de un
    export anterior ya NO reservan folios (pueden no haberse subido nunca)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem1 = _rem(client, h, env)
    client.patch(f"/api/v1/remisiones/{rem1['id']}", headers=h,
                 json={"factura_sae": "ZHGO 233"})
    rem2 = _rem(client, h, env, su_pedido="24737")
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem2["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 233}})
    assert r.status_code == 422
    assert "233 ya existen" in r.json()["detail"]
    det = client.get(f"/api/v1/remisiones/{rem2['id']}", headers=h).json()
    assert det["factura_sae"] is None    # nada se estampó


def test_export_rechaza_remision_con_factura_nativa(client, env, auth_as):
    """El candado crítico: una remisión ya amparada por un CFDI del Facturador
    jamás entra al archivo — importarla en SAE sería un SEGUNDO CFDI real."""
    from app.models import Factura, Remision

    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    db = SessionLocal()
    try:
        f = Factura(tenant_id=env["tenant"], serie="ZHGO", folio=900,
                    cliente_id=env["cli"], estado="TIMBRADA", origen="NATIVA")
        db.add(f); db.flush()
        db.query(Remision).filter(Remision.id == rem["id"]).update(
            {"factura_id": f.id, "estado": "FACTURADA"})
        db.commit()
    finally:
        db.close()
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
    assert pv["ok"] is False
    assert any("NATIVA" in e for e in pv["errores"])


def test_export_repetido_avisa_pero_no_bloquea(client, env, auth_as):
    """Un archivo exportado puede no subirse nunca a SAE (pasó con ZHGO 588):
    re-exportar debe ser posible. El rastro export_sae_at genera el AVISO para
    que el operador no re-importe por accidente lo que sí subió."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    client.post("/api/v1/remisiones/export-sae", headers=h,
                json={"ids": [rem["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 233}})
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
    assert pv["ok"] is True and any("ya salió" in a for a in pv["avisos"])
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 240}})
    assert r.status_code == 200, r.text
    hoja = xlrd.open_workbook(file_contents=r.content).sheet_by_name("Facturas")
    assert hoja.row(1)[0].value == "ZHGO       240"


def test_export_omite_partidas_en_cero(client, env, auth_as):
    """Una devolución total deja la línea con cantidad 0; SAE importaría un
    concepto en cero que el PAC rechaza — se exportan solo las vivas."""
    from app.models import LineaRemision

    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env, lineas=[
        {"producto_id": env["prod"], "cantidad_solicitada": 5, "precio_unitario": 836},
        {"producto_id": env["prod"], "cantidad_solicitada": 2, "precio_unitario": 836},
    ])
    db = SessionLocal()
    try:
        db.query(LineaRemision).filter(
            LineaRemision.remision_id == rem["id"], LineaRemision.numero_linea == 2
        ).update({"cantidad_solicitada": 0})
        db.commit()
    finally:
        db.close()
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 233}})
    assert r.status_code == 200
    hoja = xlrd.open_workbook(file_contents=r.content).sheet_by_name("Facturas")
    assert hoja.nrows == 2                            # encabezado + 1 partida viva


def test_remision_estampada_no_se_factura_nativa(client, env, auth_as):
    """Una remisión con factura_sae (confirmada por el espejo o capturada a
    mano) ya está amparada por un CFDI de SAE — facturarla nativa serían dos
    CFDI reales por la misma venta."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    client.patch(f"/api/v1/remisiones/{rem['id']}", headers=h,
                 json={"factura_sae": "ZHGO 233"})
    r = client.post("/api/v1/facturas/desde-remisiones", headers=h,
                    json={"remision_ids": [rem["id"]]})
    assert r.status_code == 409
    assert "SAE" in r.json()["detail"]


def test_partida_sin_codigo_del_cliente_detiene_el_lote(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:6]
        nuevo = Producto(tenant_id=env["tenant"], sku=f"9{suffix}", nombre="SIN CODIGO",
                         clave_sat="50300000", unidad_sat="KGM")
        db.add(nuevo); db.commit()
        nuevo_id = str(nuevo.id)
    finally:
        db.close()
    rem = _rem(client, h, env, lineas=[
        {"producto_id": env["prod"], "cantidad_solicitada": 5, "precio_unitario": 836},
        {"producto_id": nuevo_id, "cantidad_solicitada": 2, "precio_unitario": 10},
    ])
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
    assert pv["ok"] is False
    assert any("sin código del cliente" in e for e in pv["errores"])


def test_empresa_sae_se_decide_por_sucursal(client, env, auth_as):
    """Un cliente con clave en DOS empresas SAE (EHMO: 02 Pachuca y 03
    Villahermosa): la equivalencia CON sucursal decide para las remisiones de
    esa sucursal; las demás caen a la genérica (sin sucursal). Solo si nada
    decide, el lote se detiene."""
    from app.models import ClienteExterno, Sucursal

    auth_as(env["admin"]); h = _hdr(env["admin"])
    db = SessionLocal()
    try:
        suc = Sucursal(tenant_id=env["tenant"], cliente_id=env["cli"], nombre="Tabasco")
        db.add(suc); db.flush()
        db.add(ClienteExterno(tenant_id=env["tenant"], sistema="SAE", clave="03:1",
                              clave_normalizada="03 1", cliente_id=env["cli"],
                              sucursal_id=suc.id, origen="MANUAL", confianza="CONFIRMADA"))
        db.commit()
        suc_id = str(suc.id)
    finally:
        db.close()

    # sin sucursal → cae a la genérica 02
    rem_02 = _rem(client, h, env)
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem_02["id"]], "tipo": "PEDIDO"}).json()
    assert pv["ok"] is True, pv
    assert pv["empresa"] == "02"

    # sucursal Tabasco → la 03
    rem_03 = _rem(client, h, env, su_pedido="9901", sucursal_id=suc_id)
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem_03["id"]], "tipo": "PEDIDO"}).json()
    assert pv["ok"] is True, pv
    assert pv["empresa"] == "03"

    # y mezclarlas sigue deteniendo el lote (SAE importa por empresa)
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem_02["id"], rem_03["id"]], "tipo": "PEDIDO"}).json()
    assert pv["ok"] is False
    assert any("mezcla empresas" in e for e in pv["errores"])


def test_folio_sugerido_continua_tras_un_export_sin_confirmar(client, env, auth_as):
    """Dos lotes seguidos NO deben proponer el mismo rango: el folio PROPUESTO
    por un export reciente (aún sin factura confirmada) alimenta el sugerido
    del siguiente — sin estampar nada como factura."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem1 = _rem(client, h, env)
    client.post("/api/v1/remisiones/export-sae", headers=h,
                json={"ids": [rem1["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 500}})
    det = client.get(f"/api/v1/remisiones/{rem1['id']}", headers=h).json()
    assert det["factura_sae"] is None            # sigue sin factura
    rem2 = _rem(client, h, env, su_pedido="24990")
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem2["id"]], "tipo": "FACTURA"}).json()
    assert pv["series"][0]["folio_sugerido"] == 501


def test_export_factura_exige_cliente_en_espejo(client, env, auth_as):
    from app.models import Cliente as _C

    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    db = SessionLocal()
    try:
        db.query(_C).filter(_C.id == env["cli"]).update({"espejo_sae": False})
        db.commit()
        pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                         json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
        assert pv["ok"] is False
        assert any("espejo SAE" in e for e in pv["errores"])
    finally:
        db.query(_C).filter(_C.id == env["cli"]).update({"espejo_sae": True})
        db.commit(); db.close()
