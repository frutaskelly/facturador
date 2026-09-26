"""Bandeja de OC + equivalencias de cliente.

Cubre lo que hace que la ingesta desatendida sea segura: idempotencia (un
reintento del bot no duplica), la regla de ambigüedad (dos pistas que se
contradicen NO eligen cliente), que una SUGERIDA no decida sola, el aprendizaje
al corregir desde la bandeja, y el aislamiento entre tenants.

El modelo del negocio, que estas pruebas dan por bueno: un CLIENTE es la razón
social a la que se factura (EHMO, MAFAN, Balles, Jubran); una SUCURSAL es su
operación regional (Pachuca, Tabasco) y es de donde salen serie y lista de
precios; y un PUNTO DE ENTREGA (un hospital, un plantel) es a dónde se descarga
dentro de esa sucursal. Balles y Jubran son dos razones sociales que comparten
puntos de entrega, así que un punto de entrega NUNCA identifica al cliente.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from .conftest import crear_sucursal
from app.models import Almacen, Cliente, Membership, Producto, Role, Sucursal, Tenant, User

_PURGE = (
    "grupos_whatsapp",
    "oc_recibidas", "cliente_externos", "lineas_remision", "remisiones",
    "movimientos_inventario", "lotes_inventario", "producto_alias",
    "precios", "listas_precios", "productos", "almacenes", "cliente_sucursales", "sucursales", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        def _tenant(s):
            t = Tenant(slug=f"oc-{s}-{suffix}", legal_name=f"OC {s} SA",
                       rfc=f"O{s.upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
            db.add(t); db.flush(); created["tenants"].append(t.id); return t

        tenant_a, tenant_b = _tenant("a"), _tenant("b")
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()

        def _user(tenant, role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        admin_a = _user(tenant_a, admin_role, "oc-admin-a")
        admin_b = _user(tenant_b, admin_role, "oc-admin-b")

        ehmo = Cliente(tenant_id=tenant_a.id, codigo="EH", legal_name="GRUPO EHMO",
                       rfc="GOA180712SF5")
        mafan = Cliente(tenant_id=tenant_a.id, codigo="MA", legal_name="MAFAN",
                        rfc="MCM170118UJ6")
        # Dos razones sociales que comparten puntos de entrega, serie y precios.
        balles = Cliente(tenant_id=tenant_a.id, codigo="BA", legal_name="OPERADORA BALLES",
                         rfc="OBV191007BS1")
        jubran = Cliente(tenant_id=tenant_a.id, codigo="JU", legal_name="DISTRIBUIDORA JUBRAN",
                         rfc="DAP250922PY2")
        db.add_all([ehmo, mafan, balles, jubran]); db.flush()
        # La SUCURSAL es la operación regional; el hospital es un punto DENTRO.
        suc = crear_sucursal(db, tenant_id=tenant_a.id, cliente_id=ehmo.id, codigo="TAB",
                             nombre="Tabasco")
        # EHMO con DOS plazas, como en la realidad: así "sin sucursal" sigue
        # existiendo para él (el fallback de plaza única no aplica) y los
        # tests que arman una orden sin destino conservan su semántica.
        crear_sucursal(db, tenant_id=tenant_a.id, cliente_id=ehmo.id, codigo="PAC",
                       nombre="Pachuca")
        # La MISMA plaza Hidalgo surte a Balles y a Jubran (modelo nuevo).
        suc_balles = crear_sucursal(db, tenant_id=tenant_a.id, cliente_id=balles.id, codigo="HGO",
                                    nombre="Hidalgo")
        suc_jubran = suc_balles
        from app.models import ClienteSucursal
        db.add(ClienteSucursal(tenant_id=tenant_a.id, cliente_id=jubran.id, sucursal_id=suc_balles.id))
        prod = Producto(tenant_id=tenant_a.id, sku="OC-P", nombre="Jitomate Saladet",
                        clave_sat="01010101", unidad_sat="KGM")
        alm = Almacen(tenant_id=tenant_a.id, codigo="OC-BG", nombre="Bodega OC")
        db.add_all([prod, alm]); db.flush()
        db.commit()
        yield {"admin_a": admin_a, "admin_b": admin_b,
               "ehmo": str(ehmo.id), "mafan": str(mafan.id), "suc": str(suc.id),
               "balles": str(balles.id), "jubran": str(jubran.id),
               "suc_balles": str(suc_balles.id), "suc_jubran": str(suc_jubran.id),
               "prod": str(prod.id), "alm": str(alm.id)}
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


def _oc(**over):
    body = {
        "canal": "WHATSAPP",
        "origen_externo": f"WA:grupo@g.us:{uuid.uuid4().hex[:6]}",
        "folio_externo": "1188",
        "remitente": "PEDIDOS FyV HOSPITALES",
        "rfc": "GOA180712SF5",
        "perfil": "villahermosa",
        "ubicacion": "JUAN GRAHAM",
        "lineas": [{"descripcion": "JITOMATE SALADET", "cantidad": "25", "unidad": "KG"}],
    }
    body.update(over)
    return body


def _externo(client, h, sistema, clave, cliente_id, **kw):
    body = {"sistema": sistema, "clave": clave, "cliente_id": cliente_id}
    body.update(kw)
    return client.post("/api/v1/clientes/externos", headers=h, json=body)


# ─── equivalencias ───────────────────────────────────────────────────────────

def test_resolver_por_rfc_y_sucursal_por_nombre(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    assert _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"]).status_code == 201

    r = client.post("/api/v1/clientes/resolver", headers=h, json={
        "pistas": [{"sistema": "RFC", "clave": "goa 180712 sf5"}],   # sucio a propósito
        "ubicacion_texto": "Tabasco",       # el documento nombra la SUCURSAL
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cliente_id"] == env["ehmo"]
    assert body["via"] == "RFC"
    assert body["ambiguo"] is False
    assert body["sucursal_id"] == env["suc"]


def test_sugerida_no_resuelve_sola(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    assert _externo(client, h, "NOMBRE", "BALLES", env["ehmo"],
                    origen="BOT", confianza="SUGERIDA").status_code == 201
    r = client.post("/api/v1/clientes/resolver", headers=h,
                    json={"pistas": [{"sistema": "NOMBRE", "clave": "BALLES"}]})
    assert r.json()["cliente_id"] is None


def test_reapuntar_equivalencia_es_idempotente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    a = _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["ehmo"])
    b = _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["mafan"])
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]        # misma fila, reapuntada
    listado = client.get("/api/v1/clientes/externos", headers=h,
                         params={"sistema": "PROYECTO"}).json()
    assert len(listado) == 1 and listado[0]["cliente_id"] == env["mafan"]


def test_sucursal_de_otro_cliente_rechazada(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    r = _externo(client, h, "UBICACION", "x:GRAHAM", env["mafan"], sucursal_id=env["suc"])
    assert r.status_code == 422


# ─── ingesta ────────────────────────────────────────────────────────────────

def test_ingesta_resuelve_cliente_y_guarda_el_punto_de_entrega(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc())
    assert r.status_code == 201, r.text
    oc = r.json()
    assert oc["cliente_id"] == env["ehmo"]
    assert oc["punto_entrega"] == "JUAN GRAHAM"
    # Un hospital NO es una sucursal: hasta que alguien diga a cuál pertenece,
    # el destino queda abierto y la bandeja lo pide.
    assert oc["sucursal_id"] is None
    assert "JUAN GRAHAM" in oc["motivo"]
    assert oc["estado"] == "PENDIENTE"          # aún no existe su remisión
    assert oc["ambiguo"] is False
    # El cruce de productos se sugiere al vuelo, sin persistirse.
    assert oc["lineas"][0]["candidatos"][0]["producto_id"] == env["prod"]


def test_ingesta_es_idempotente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    body = _oc()
    a = client.post("/api/v1/oc-recibidas", headers=h, json=body)
    b = client.post("/api/v1/oc-recibidas", headers=h, json=body)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    total = client.get("/api/v1/oc-recibidas", headers=h).json()["total"]
    assert total == 1


def test_reintento_completa_el_link_del_documento(client, env, auth_as):
    """Una OC que ya generó remisión no se toca en un reintento… salvo el
    puntero al documento original: si llegó sin `archivo_url` (pasó con 183
    órdenes de la migración del master), el reenvío del bot lo completa. Con
    el link ya puesto, no se pisa."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["archivo_url"] is None
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    body = {"almacen_id": env["alm"], "lineas": [{
        "producto_id": env["prod"], "cantidad": "25", "precio_unitario": "18.50",
        "texto_original": "JITOMATE SALADET"}]}
    hecho = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision",
                        headers=h, json=body).json()
    assert hecho["remision_id"]

    url = "https://drive.google.com/file/d/abc123/view"
    again = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=oc["origen_externo"], archivo_url=url,
        archivo_nombre="Requisición 13 de agosto.pdf")).json()
    assert again["archivo_url"] == url
    assert again["archivo_nombre"] == "Requisición 13 de agosto.pdf"
    # La captura sigue intacta: misma remisión, mismo estado.
    assert again["remision_id"] == hecho["remision_id"]
    assert again["estado"] == "ASIGNADA"

    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=oc["origen_externo"],
        archivo_url="https://drive.google.com/file/d/otro/view")).json()
    assert otra["archivo_url"] == url


def test_el_lote_de_la_partida_se_guarda(client, env, auth_as):
    """EXTRA / REPOSICIÓN viajan con la partida y quedan en el payload.

    De ese dato depende el dinero: una reposición se surte y no se cobra, y
    hasta hoy solo vivía en la celda del Master de EHMO. Sin él, archivar la
    hoja hacía que una reposición se empezara a cobrar. No entra en la
    comparación de cambios, así que un payload viejo sin `lote` no despierta
    ninguna incidencia."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=[
        {"descripcion": "JITOMATE SALADET", "cantidad": "25", "unidad": "KG"},
        {"descripcion": "CHILE POBLANO", "cantidad": "8", "unidad": "KG",
         "lote": "REPOSICION"}]))
    assert r.status_code == 201, r.text
    lineas = r.json()["payload"]["lineas"]
    assert lineas[0].get("lote") is None
    assert lineas[1]["lote"] == "REPOSICION"


def _lineas(n, desde=1):
    return [{"descripcion": f"PRODUCTO {i}", "cantidad": "5", "unidad": "KG"}
            for i in range(desde, desde + n)]


def test_el_catalogo_de_ubicaciones_sale_de_la_bandeja(client, env, auth_as):
    """Hasta hoy salía del Master: de cada renglón, la ubicación y el prefijo
    de su folio. De él hereda una OC creada a mano su proyecto y su lista de
    precios, y sin él nace con el proyecto por omisión y cotiza contra la lista
    equivocada — sin dar error. Es el único del retiro que muerde en silencio.

    Si una ubicación usó dos prefijos, gana el más reciente."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="VH-39PAL-LUN", ubicacion="palenque"))
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39ACT-LUN", ubicacion="ACTOPAN"))
    # la misma ubicación, después, con otro prefijo: manda el reciente
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="CE-39PAL-MAR", ubicacion="Palenque"))

    r = client.get("/api/v1/oc-recibidas/ubicaciones", headers=h)
    assert r.status_code == 200, r.text
    por_nombre = {u["ubicacion"]: u["prefijo"] for u in r.json()["ubicaciones"]}
    assert por_nombre["ACTOPAN"] == "HO"
    assert por_nombre["PALENQUE"] == "CE"             # el más reciente
    assert "palenque" not in por_nombre               # normalizado a mayúsculas


def test_la_misma_entrega_con_otro_folio_no_se_registra_dos_veces(client, env, auth_as):
    """SSP, 17-ago-2026: la misma foto se reenvió sin caption, cayó en LUNES en
    vez de JUEVES —esa tabla no trae columna de día— y creó una gemela con 40
    de 40 productos idénticos. $29,604 contados dos veces.

    Es el único candado que compara CONTENIDO y no identificadores: por eso
    atrapa lo que los otros cuatro no ven — otro folio, otro día, otro
    hospital incluso.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    r1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39SSP-JUE", ubicacion="SSP", fecha_entrega="2026-09-24",
        lineas=_lineas(8)))
    assert r1.status_code == 201, r1.text

    # la misma foto, otro día y otro folio: mismos productos y cantidades
    r2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39SSP-LUN", ubicacion="SSP", fecha_entrega="2026-09-21",
        lineas=_lineas(8)))
    assert r2.status_code == 409, r2.text
    assert "HO-39SSP-JUE" in r2.json()["detail"]

    # `forzar` entra: si de verdad se entregó lo mismo dos veces, una persona lo dice
    r3 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39SSP-LUN", ubicacion="SSP", fecha_entrega="2026-09-21",
        lineas=_lineas(8), forzar=True))
    assert r3.status_code == 201, r3.text


def test_el_antigemela_copia_sus_tres_propiedades_del_original(client, env, auth_as):
    """Las tres que no se deducen de su nombre y deciden cuándo dispara:
    igualdad EXACTA (un gramo y no dispara), mínimo de CINCO partidas (dos
    entregas chicas coinciden por casualidad), y la ventana es la SEMANA del
    folio, no el hospital ni el día."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    # 1. menos de cinco partidas: no actúa
    chico = dict(ubicacion="CHICO", fecha_entrega="2026-09-24", lineas=_lineas(4))
    assert client.post("/api/v1/oc-recibidas", headers=h,
                       json=_oc(folio_externo="HO-40CHI-LUN", **chico)).status_code == 201
    assert client.post("/api/v1/oc-recibidas", headers=h,
                       json=_oc(folio_externo="HO-40CHI-MAR", **chico)).status_code == 201

    # 2. un gramo de diferencia y no dispara
    base = _lineas(6)
    assert client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-41GRA-LUN", ubicacion="GRAMO", lineas=base)).status_code == 201
    casi = [dict(l) for l in base]
    casi[0] = dict(casi[0], cantidad="5.001")
    assert client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-41GRA-MAR", ubicacion="GRAMO", lineas=casi)).status_code == 201

    # 3. otra semana: fuera de la ventana, no dispara
    assert client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-42GRA-LUN", ubicacion="GRAMO", lineas=base)).status_code == 201


def test_un_documento_no_es_gemelo_de_si_mismo(client, env, auth_as):
    """Cada día de una foto llega como su propia orden. Una foto que pide lo
    mismo el lunes y el martes no es una gemela: es el mismo documento. La foto
    REENVIADA —el caso de SSP, $29,604— es otro archivo, y ésa sí se frena."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    base = _lineas(6)
    for folio in ("VH-38ROV-LUN-B", "VH-38ROV-MAR-B"):
        r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
            folio_externo=folio, ubicacion="ROVIROSA", archivo_nombre="foto-lunes-martes.jpg",
            lineas=base))
        assert r.status_code == 201, r.text
    otra_foto = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="VH-38ROV-MIE-B", ubicacion="ROVIROSA", archivo_nombre="reenvio.jpg",
        lineas=base))
    assert otra_foto.status_code == 409, otra_foto.text
    assert "idéntica" in otra_foto.json()["detail"]


def test_la_misma_entrega_con_otro_numero_de_semana_no_se_registra_dos_veces(client, env, auth_as):
    """El 13-sep-2026 cambió el corte de semana y las entregas del 14 al 18
    llegaron una vez como semana 37 y otra como 38. Medido el 24-sep sobre la
    bandeja real: 17 órdenes dobles, 16 remisiones duplicadas, y una persona
    cancelándolas a mano una por una.

    La hoja lo evitaba porque casa por (hospital, día, fecha); aquí la llave
    lleva el folio adentro, y cuando el folio cambia las dos llaves dejan de
    coincidir. Este candado cierra esa diferencia.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    comun = dict(ubicacion="AMATAN", fecha_entrega="2026-09-14", lineas=_lineas(21))

    r1 = client.post("/api/v1/oc-recibidas", headers=h,
                     json=_oc(folio_externo="VH-37AMA-LUN", **comun))
    assert r1.status_code == 201, r1.text

    r2 = client.post("/api/v1/oc-recibidas", headers=h,
                     json=_oc(folio_externo="VH-38AMA-LUN", **comun))
    assert r2.status_code == 409, r2.text
    assert "VH-37AMA-LUN" in r2.json()["detail"]

    # con `forzar` entra: si de verdad son dos entregas, una persona lo dice
    r3 = client.post("/api/v1/oc-recibidas", headers=h,
                     json=_oc(folio_externo="VH-38AMA-LUN", forzar=True, **comun))
    assert r3.status_code == 201, r3.text


def test_dos_clientes_en_el_mismo_punto_y_dia_no_se_frenan(client, env, auth_as):
    """COSTALES DIF recibe el mismo día de dos clientes distintos —DI-32EHM y
    DI-32MAF— y frenarlos sería un falso positivo sobre algo normal. Por eso el
    candado exige que los dos folios sean el MISMO salvo la semana, y no solo
    que coincidan punto y fecha."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    comun = dict(ubicacion="COSTALES DIF", fecha_entrega="2026-08-14", lineas=_lineas(3))
    assert client.post("/api/v1/oc-recibidas", headers=h,
                       json=_oc(folio_externo="DI-32EHM", **comun)).status_code == 201
    r = client.post("/api/v1/oc-recibidas", headers=h,
                    json=_oc(folio_externo="DI-32MAF", **comun))
    assert r.status_code == 201, r.text


def test_un_reenvio_mutilado_no_reemplaza_la_entrega_completa(client, env, auth_as):
    """Así se perdió el pedido de OTOMÍ el 17-ago-2026: 27 renglones
    reemplazados por 4. Un reenvío que trae mucho menos de lo ya registrado casi
    nunca es una corrección — son unos extras, un segundo pedido o una foto a
    medias.

    El umbral se copia TAL CUAL del original, con sus huecos: `previos >= 8` y
    `nuevos < previos/2`, contando renglones.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    comun = dict(ubicacion="HOSPITAL OTOMI", fecha_entrega="2026-09-24")

    r1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39OTO-JUE", lineas=_lineas(27), **comun))
    assert r1.status_code == 201, r1.text

    # el reenvío mutilado: 4 donde había 27
    r2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39OTO-JUE-R", lineas=_lineas(4, 100), **comun))
    assert r2.status_code == 409, r2.text
    assert "27" in r2.json()["detail"] and "OTOMI" in r2.json()["detail"].upper()

    # con `forzar` entra: quien miró la foto manda
    r3 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39OTO-JUE-R", lineas=_lineas(4, 100), forzar=True, **comun))
    assert r3.status_code == 201, r3.text


def test_el_antirreemplazo_respeta_sus_dos_huecos_conocidos(client, env, auth_as):
    """Los dos agujeros del umbral son deliberados y se copian sin «mejorarlos»:
    una entrega de menos de 8 productos no está protegida, y un reenvío
    mutilado a la mitad JUSTA pasa. El día que se muevan, que sea con datos."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    # 7 previos: por debajo del mínimo, no protege
    chico = dict(ubicacion="HOSPITAL CHICO", fecha_entrega="2026-09-24")
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39CHI-JUE", lineas=_lineas(7), **chico))
    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39CHI-JUE-R", lineas=_lineas(1, 50), **chico))
    assert r.status_code == 201, r.text

    # la mitad justa de 10 es 5, y `nuevos < previos*0.5` es falso: pasa
    medio = dict(ubicacion="HOSPITAL MEDIO", fecha_entrega="2026-09-24")
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39MED-JUE", lineas=_lineas(10), **medio))
    r2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39MED-JUE-R", lineas=_lineas(5, 60), **medio))
    assert r2.status_code == 201, r2.text


def test_una_entrega_aparte_no_dispara_el_antirreemplazo(client, env, auth_as):
    """Las «aparte» traen poco por definición: contarlas sería frenar lo normal."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    comun = dict(ubicacion="HOSPITAL APARTE", fecha_entrega="2026-09-24")
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39APA-JUE", lineas=_lineas(20), **comun))
    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo="HO-39APA-JUE-2", lineas=_lineas(2, 70), **comun))
    assert r.status_code == 201, r.text


def test_el_contador_de_sufijos_aparte_sale_de_la_bandeja(client, env, auth_as):
    """Una entrega APARTE del mismo hospital y día lleva sufijo para no chocar
    con la principal. Ese contador sale hoy del Master de EHMO, y es la pieza
    que hace únicos esos folios — sin ella los otros candados no sirven.

    Es el modo de falla que el retiro INTRODUCE por su cuenta: sin la hoja, el
    contador arrancaría en 2 siempre y la segunda entrega aparte del día
    BORRARÍA a la primera, porque comparten `origen_externo`.

    Dos reglas, las mismas del original: el mismo archivo reprocesado reusa su
    sufijo, y si no, el siguiente libre.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    base = "HO-39ACT-LUN"

    def sufijos(archivo=None):
        p = {"base": base}
        if archivo:
            p["archivo"] = archivo
        r = client.get("/api/v1/oc-recibidas/sufijos-aparte", headers=h, params=p)
        assert r.status_code == 200, r.text
        return r.json()

    # sin ninguna aparte todavía, la primera es la 2
    assert sufijos()["siguiente"] == 2 and sufijos()["usados"] == []

    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo=f"{base}-2", archivo_nombre="foto-a.jpg"))
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo=f"{base}-3", archivo_nombre="foto-b.jpg"))

    assert sufijos()["usados"] == [2, 3]
    assert sufijos()["siguiente"] == 4               # un archivo nuevo toma el libre
    assert sufijos("foto-a.jpg")["reuso"] == 2       # reprocesar la misma foto reusa
    assert sufijos("foto-a.jpg")["siguiente"] == 2
    assert sufijos("foto-z.jpg")["reuso"] is None    # una foto que nunca se vio, no

    # el folio base a secas no cuenta como aparte
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(folio_externo=base))
    assert sufijos()["usados"] == [2, 3]


def test_los_candados_ven_el_ancla_determinista_del_bot(client, env, auth_as):
    """El bot manda `EHMO:<perfil>:<folio>`: el mismo folio trae la MISMA ancla.

    Las pruebas de los candados usaban anclas al azar y por eso no vieron que
    folio repetido y antirreemplazo excluían la propia ancla: con la del bot,
    el choque caía en la rama de actualización y pisaba la orden. Con el ancla
    determinista, los dos frenan; `forzar` —una persona que ya miró— los salta;
    y el reenvío de siempre (misma fecha, mismo tamaño) entra.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    folio = "VH-38ROV-LUN-B"
    ancla = f"EHMO:villahermosa:{folio}"
    base = dict(origen_externo=ancla, folio_externo=folio, ubicacion="HOSPITAL ROVIROSA",
                fecha_entrega="2026-09-21")

    r1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=_lineas(10), **base))
    assert r1.status_code == 201, r1.text

    # el reenvío de siempre: misma fecha, mismo tamaño → entra
    igual = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=_lineas(10), **base))
    assert igual.status_code in (200, 201), igual.text

    # folio repetido: la misma ancla con OTRA fecha → se frena
    otra_fecha = client.post("/api/v1/oc-recibidas", headers=h,
                             json=_oc(lineas=_lineas(10), **dict(base, fecha_entrega="2026-09-28")))
    assert otra_fecha.status_code == 409, otra_fecha.text
    assert "otra fecha" in otra_fecha.json()["detail"]

    # antirreemplazo: la misma ancla mutilada (10 → 2) → se frena
    mutilada = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=_lineas(2), **base))
    assert mutilada.status_code == 409, mutilada.text
    assert "10 registrados" in mutilada.json()["detail"]

    # una edición que pidió una persona llega con forzar → pasa
    editada = client.post("/api/v1/oc-recibidas", headers=h,
                          json=_oc(lineas=_lineas(2), forzar=True, **dict(base, fecha_entrega="2026-09-22")))
    assert editada.status_code in (200, 201), editada.text


def test_un_ancla_que_no_sale_del_folio_sigue_pudiendo_reenviarse(client, env, auth_as):
    """Fuera de EHMO el ancla es el documento: su reenvío con otra fecha es una
    corrección del mismo documento, no un choque. Eso no cambia."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    base = dict(origen_externo="WA:grupo@g.us:77001", folio_externo="77001",
                ubicacion="HOSPITAL OTRO", fecha_entrega="2026-09-21")
    assert client.post("/api/v1/oc-recibidas", headers=h,
                       json=_oc(lineas=_lineas(10), **base)).status_code == 201
    r = client.post("/api/v1/oc-recibidas", headers=h,
                    json=_oc(lineas=_lineas(10), **dict(base, fecha_entrega="2026-09-23")))
    assert r.status_code in (200, 201), r.text


def test_una_aparte_descartada_sigue_ocupando_su_sufijo(client, env, auth_as):
    """Una OC descartada sigue ocupando su `origen_externo`: la ingesta la
    devuelve intacta y no guarda lo nuevo. Si el contador la diera por libre,
    la siguiente aparte del día caería sobre ella y desaparecería sin aviso.
    La hoja le daba el sufijo siguiente, porque el descarte no borra el renglón.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    base = "VH-38PAL-MIE-B"

    def sufijos(archivo=None):
        p = {"base": base}
        if archivo:
            p["archivo"] = archivo
        r = client.get("/api/v1/oc-recibidas/sufijos-aparte", headers=h, params=p)
        assert r.status_code == 200, r.text
        return r.json()

    a = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo=f"{base}-2", archivo_nombre="foto-a.jpg")).json()
    r = client.post(f"/api/v1/oc-recibidas/{a['id']}/descartar", headers=h,
                    params={"motivo": "prueba"})
    assert r.status_code == 200 and r.json()["estado"] == "DESCARTADA"

    assert sufijos()["usados"] == [2]
    assert sufijos("foto-b.jpg")["siguiente"] == 3     # una foto nueva no cae sobre la descartada
    assert sufijos("foto-a.jpg")["reuso"] == 2         # la misma foto respeta su descarte

    b = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo=f"{base}-3", archivo_nombre="foto-b.jpg"))
    assert b.status_code == 201 and b.json()["estado"] != "DESCARTADA", b.text


def test_un_folio_repetido_con_otra_fecha_no_pisa_la_orden_anterior(client, env, auth_as):
    """El primero de los cinco candados del Master de EHMO, mudado aquí.

    El folio de EHMO es determinista (hospital + semana + día), así que dos
    entregas distintas pueden generar el mismo. En la hoja ese choque ABORTA;
    aquí, sin candado, la ingesta no duplicaba: SOBRESCRIBÍA la orden anterior,
    en silencio. Es el único de los cinco cuyo hueco borra en vez de duplicar.

    Se compara la fecha de ENTREGA, no el contenido: un reenvío corregido de la
    misma entrega trae la misma fecha y tiene que seguir entrando.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    base = dict(folio_externo="VH-39PAL-MIE", fecha_entrega="2026-09-23")
    r1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(**base))
    assert r1.status_code == 201, r1.text

    # el mismo documento otra vez: entra, es el reenvío de siempre
    igual = client.post("/api/v1/oc-recibidas", headers=h,
                        json=_oc(origen_externo=r1.json()["origen_externo"], **base))
    assert igual.status_code in (200, 201), igual.text

    # otra entrega que generó el MISMO folio, con otra fecha: se frena
    choque = dict(base, fecha_entrega="2026-09-30")
    r2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(**choque))
    assert r2.status_code == 409, r2.text
    assert "otra fecha de entrega" in r2.json()["detail"]

    # y la primera sigue intacta: el candado frena ANTES de escribir
    sigue = client.get(f"/api/v1/oc-recibidas/{r1.json()['id']}", headers=h).json()
    assert sigue["payload"]["fecha_entrega"] == "2026-09-23"

    # con `forzar` —una persona dijo «es otra»— entra y se registra aparte
    r3 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(forzar=True, **choque))
    assert r3.status_code == 201, r3.text
    assert r3.json()["id"] != r1.json()["id"]


def test_sin_fecha_de_entrega_el_candado_no_frena(client, env, auth_as):
    """Un candado que bloquea por falta de dato bloquea lo bueno, y aquí lo
    bueno es la entrega de un hospital. Sin fecha en alguno de los dos lados,
    deja pasar."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    r1 = client.post("/api/v1/oc-recibidas", headers=h,
                     json=_oc(folio_externo="VH-40CUN-LUN"))          # sin fecha
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/v1/oc-recibidas", headers=h,
                     json=_oc(folio_externo="VH-40CUN-LUN", fecha_entrega="2026-10-05"))
    assert r2.status_code == 201, r2.text


def test_reenvio_sin_link_no_borra_el_que_ya_tenia(client, env, auth_as):
    """Una OC PENDIENTE que se vuelve a espejar sin `archivo_url` conserva el
    suyo. La conciliación del bot corre cada 6 h y manda el payload sin enlace
    (`espejar_folios_bandeja` lo deja en None): con la asignación incondicional,
    cada pasada blanqueaba el «Ver la OC original» de todo lo que aún no tenía
    remisión. Un enlace nuevo sí pisa al viejo."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    url = "https://drive.google.com/file/d/abc123/view"
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        archivo_url=url, archivo_nombre="OC 1188.pdf")).json()
    assert oc["archivo_url"] == url and oc["remision_id"] is None

    igual = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=oc["origen_externo"])).json()          # sin link, como el espejo
    assert igual["archivo_url"] == url
    assert igual["archivo_nombre"] == "OC 1188.pdf"

    nuevo = "https://drive.google.com/file/d/xyz789/view"
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=oc["origen_externo"], archivo_url=nuevo)).json()
    assert otra["archivo_url"] == nuevo


def test_listado_filtra_por_fecha_de_recepcion(client, env, auth_as):
    """El flujo diario es "lo que llegó hoy": el rango va sobre recibida_at y
    `fecha_hasta` es INCLUSIVO — "hasta el 28" no puede dejar fuera la tarde."""
    from datetime import date, timedelta

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    creada = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    # "hoy" según el RELOJ DEL SERVIDOR (recibida_at), no date.today(): cerca de
    # medianoche difieren y el test fallaba solo en esa ventana.
    hoy = date.fromisoformat(creada["recibida_at"][:10])
    ayer, manana = hoy - timedelta(days=1), hoy + timedelta(days=1)

    def total(**qs):
        return client.get("/api/v1/oc-recibidas", headers=h, params=qs).json()["total"]

    assert total(fecha_desde=str(hoy), fecha_hasta=str(hoy)) == 1   # inclusivo
    assert total(fecha_hasta=str(ayer)) == 0                        # antes de que llegara
    assert total(fecha_desde=str(manana)) == 0                      # todavía no existe


def test_pistas_contradictorias_no_eligen_cliente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    _externo(client, h, "PROYECTO", "ehmo:DIF", env["mafan"])

    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo", proyecto="DIF", ubicacion=None))
    oc = r.json()
    assert oc["ambiguo"] is True
    assert oc["cliente_id"] is None
    assert oc["estado"] == "PENDIENTE"
    assert "clientes distintos" in oc["motivo"]


def test_sin_pistas_conocidas_queda_pendiente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["cliente_id"] is None and oc["estado"] == "PENDIENTE"
    assert "Ninguna pista" in oc["motivo"]


# ─── bandeja: corregir, aprender, crear la remisión ──────────────────────────

def test_asignar_manual_aprende_las_pistas(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["cliente_id"] is None

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={
        "cliente_id": env["ehmo"], "sucursal_id": env["suc"], "aprender": True})
    assert r.status_code == 200, r.text
    assert r.json()["cliente_id"] == env["ehmo"]
    assert r.json()["resuelto_via"] == "MANUAL"

    # La siguiente OC igual ya no pregunta: aprendió el RFC (cliente) y que ese
    # hospital se descarga en la sucursal de Tabasco (destino).
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert otra["cliente_id"] == env["ehmo"]
    assert otra["sucursal_id"] == env["suc"]
    assert otra["punto_entrega"] == "JUAN GRAHAM"

    externos = client.get("/api/v1/clientes/externos", headers=h,
                          params={"cliente_id": env["ehmo"]}).json()
    sistemas = {e["sistema"] for e in externos}
    assert {"RFC", "UBICACION"} <= sistemas
    ubic = next(e for e in externos if e["sistema"] == "UBICACION")
    assert ubic["sucursal_id"] == env["suc"]
    assert ubic["clave"] == "villahermosa:JUAN GRAHAM"   # namespaceado por perfil


def test_crear_remision_liga_y_no_se_puede_dos_veces(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    # El hospital pertenece a la sucursal de Tabasco; eso lo dice una persona.
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})

    body = {"almacen_id": env["alm"], "lineas": [{
        "producto_id": env["prod"], "cantidad": "25", "precio_unitario": "18.50",
        "texto_original": "JITOMATE SALADET"}]}
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json=body)
    assert r.status_code == 200, r.text
    hecho = r.json()
    assert hecho["estado"] == "ASIGNADA"
    assert hecho["remision_id"] and hecho["remision_folio"]

    rem = client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()
    assert rem["estado"] == "BORRADOR"
    assert rem["canal"] == "API"
    assert rem["sucursal_id"] == env["suc"]
    # El punto de entrega ENCABEZA las observaciones: es lo que el equipo lee
    # para saber a dónde llevarla, y de aquí pasa a las de la factura.
    assert (rem["notas"] or "").startswith("JUAN GRAHAM")
    assert "OC 1188" in (rem["notas"] or "")
    assert rem["nota_entrega"] == "JUAN GRAHAM"
    assert float(rem["subtotal"]) == 462.5

    # Segundo intento: la orden ya tiene remisión, no se generan dos folios.
    assert client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json=body
    ).status_code == 409


def test_pedido_ya_capturado_a_mano_no_genera_otra_remision(client, env, auth_as):
    """El caso de la OC 25297: la remisión se capturó a mano («OC 25297») y al
    procesar la bandeja días después salió un segundo folio con el mismo
    pedido. El cruce es por dígitos: «OC 1188» y «1188» son el mismo pedido."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    manual = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["ehmo"], "almacen_id": env["alm"],
        "su_pedido": "OC 1188",
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": "25",
                    "precio_unitario": "18.50"}],
    })
    assert manual.status_code == 201, manual.text

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    body = {"almacen_id": env["alm"], "lineas": [{
        "producto_id": env["prod"], "cantidad": "25", "precio_unitario": "18.50"}]}
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json=body)
    assert r.status_code == 409, r.text
    assert manual.json()["folio_interno"] in r.json()["detail"]

    # Cancelada la capturada a mano, la orden sí puede generar su remisión.
    ok = client.post(f"/api/v1/remisiones/{manual.json()['id']}/cancelar", headers=h)
    assert ok.status_code == 200, ok.text
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json=body)
    assert r.status_code == 200, r.text


def test_pedido_con_formato_se_repite_y_no_bloquea(client, env, auth_as):
    """EHMO y Río Libre no mandan folio: mandan «HO-34VIL-MIE», donde el número
    es la SEMANA y las letras la plaza, el punto de entrega y el día. Ese texto
    se repite entre entregas —RRIO7 y RRIO21, ambas facturadas, comparten
    «CEN-35HUA-FYV»— así que no identifica un pedido y no puede frenar nada.
    Tomarlo por folio (mirar solo sus dígitos) frenaba la semana entera."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    previa = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["ehmo"], "almacen_id": env["alm"],
        "su_pedido": "HO-34VIL-MIE",
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": "1",
                    "precio_unitario": "10"}],
    })
    assert previa.status_code == 201, previa.text

    body = {"almacen_id": env["alm"], "lineas": [{
        "producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}]}

    # Otro punto de entrega de la MISMA semana: nada que ver, pasa.
    otra = client.post("/api/v1/oc-recibidas", headers=h,
                       json=_oc(folio_externo="HO-34ALB-LUN")).json()
    client.patch(f"/api/v1/oc-recibidas/{otra['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    assert client.post(f"/api/v1/oc-recibidas/{otra['id']}/crear-remision",
                       headers=h, json=body).status_code == 200

    # Y el MISMO texto repetido tampoco frena: es una entrega más, no un
    # duplicado. El PATCH de arriba ya aprendió cliente y destino, así que esta
    # ni siquiera espera un humano: la ingesta misma la vuelve remisión.
    igual = client.post("/api/v1/oc-recibidas", headers=h,
                        json=_oc(folio_externo="HO-34VIL-MIE")).json()
    assert igual["estado"] == "ASIGNADA" and igual["remision_id"]


def test_folio_numerico_no_choca_con_pedido_con_formato(client, env, auth_as):
    """El folio 34 y «HO-34VIL-MIE» comparten dígitos pero no son el mismo
    pedido: solo los folios numéricos se comparan entre sí."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    conformato = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["ehmo"], "almacen_id": env["alm"],
        "su_pedido": "HO-34VIL-MIE",
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": "1",
                    "precio_unitario": "10"}],
    })
    assert conformato.status_code == 201, conformato.text

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(folio_externo="34")).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"], "lineas": [{
            "producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}]})
    assert r.status_code == 200, r.text


def test_crear_remision_sin_cliente_es_422(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "1"}]})
    assert r.status_code == 422
    assert "cliente" in r.json()["detail"].lower()


def test_descartar_y_reabrir(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()

    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/descartar", headers=h,
                    params={"motivo": "era cotización"})
    assert r.status_code == 200 and r.json()["estado"] == "DESCARTADA"

    # Una descartada NO se resucita por un reintento del bot.
    again = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=oc["origen_externo"]))
    assert again.json()["estado"] == "DESCARTADA"

    assert client.post(f"/api/v1/oc-recibidas/{oc['id']}/reabrir",
                       headers=h).json()["estado"] == "PENDIENTE"


# ─── aislamiento ────────────────────────────────────────────────────────────

def test_tenant_b_no_ve_ni_resuelve_lo_de_a(client, env, auth_as):
    auth_as(env["admin_a"]); h_a = _hdr(env["admin_a"])
    _externo(client, h_a, "RFC", "GOA180712SF5", env["ehmo"])
    oc_a = client.post("/api/v1/oc-recibidas", headers=h_a, json=_oc()).json()

    auth_as(env["admin_b"]); h_b = _hdr(env["admin_b"])
    assert client.get("/api/v1/oc-recibidas", headers=h_b).json()["total"] == 0
    assert client.get(f"/api/v1/oc-recibidas/{oc_a['id']}", headers=h_b).status_code == 404
    assert client.get("/api/v1/clientes/externos", headers=h_b).json() == []
    r = client.post("/api/v1/clientes/resolver", headers=h_b,
                    json={"pistas": [{"sistema": "RFC", "clave": "GOA180712SF5"}]})
    assert r.json()["cliente_id"] is None


# ─── reglas que salieron de la revisión ─────────────────────────────────────

def test_el_grupo_acota_pero_no_decide(client, env, auth_as):
    """Por un mismo grupo entran dos razones sociales —EHMO y MAFAN en Pachuca,
    Balles y Jubran en Hidalgo—. El grupo convierte «no sé de quién es» en «es de
    estos dos», que es lo que el operador necesita; nunca elige por su cuenta."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-compartido@g.us"
    _externo(client, h, "WHATSAPP", jid, env["balles"])
    _externo(client, h, "WHATSAPP", jid, env["jubran"])

    # El mismo grupo, dos clientes: conviven (antes el UNIQUE lo impedía).
    registrados = client.get("/api/v1/clientes/externos", headers=h,
                             params={"sistema": "WHATSAPP"}).json()
    assert {e["cliente_id"] for e in registrados} == {env["balles"], env["jubran"]}

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, ubicacion=None, jid=jid)).json()
    assert oc["cliente_id"] is None                     # no adivina
    assert oc["ambiguo"] is False                       # tampoco es un conflicto
    assert set(oc["candidatos"]) == {env["balles"], env["jubran"]}
    assert "elige cuál" in oc["motivo"]
    assert "BALLES" in oc["motivo"] and "JUBRAN" in oc["motivo"]


def test_al_asignar_se_registra_el_grupo_como_candidato(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-nuevo@g.us"
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, ubicacion=None, jid=jid)).json()
    assert oc["candidatos"] == []                       # grupo desconocido

    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["balles"], "aprender": True})

    # La próxima orden de ese grupo ya llega con la lista corta — pero sigue
    # necesitando que una persona confirme: un grupo no identifica a nadie.
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, ubicacion=None, jid=jid)).json()
    assert otra["cliente_id"] is None
    assert otra["candidatos"] == [env["balles"]]


def test_el_mismo_punto_de_entrega_sirve_a_dos_clientes(client, env, auth_as):
    """Balles y Jubran descargan en el mismo lugar, cada uno con su sucursal."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    assert _externo(client, h, "UBICACION", "PROCU", env["balles"],
                    sucursal_id=env["suc_balles"]).status_code == 201
    assert _externo(client, h, "UBICACION", "PROCU", env["jubran"],
                    sucursal_id=env["suc_jubran"]).status_code == 201

    _externo(client, h, "NOMBRE", "JUBRAN", env["jubran"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, nombre="JUBRAN", ubicacion="PROCU")).json()
    assert oc["cliente_id"] == env["jubran"]
    assert oc["sucursal_id"] == env["suc_jubran"]       # la SUYA, no la de Balles


def test_reintento_no_pisa_la_asignacion_manual(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    body = _oc(rfc=None, ubicacion=None)          # sin pistas registrables
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=body).json()
    assert oc["cliente_id"] is None

    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "aprender": False})
    # El bot reintenta el mismo documento media hora después.
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=body).json()
    assert otra["id"] == oc["id"]
    assert otra["cliente_id"] == env["ehmo"]      # la decisión humana sobrevive


def test_descartada_no_acepta_asignacion(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    client.post(f"/api/v1/oc-recibidas/{oc['id']}/descartar", headers=h)
    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "aprender": True})
    assert r.status_code == 409
    assert client.get("/api/v1/clientes/externos", headers=h).json() == []


def test_cliente_borrado_no_resuelve(client, env, auth_as):
    """Una equivalencia huérfana dejaría la orden marcada «lista» y reventaría
    al crear la remisión; tiene que comportarse como inexistente."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    assert client.delete(f"/api/v1/clientes/{env['ehmo']}", headers=h).status_code == 204

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["cliente_id"] is None
    assert oc["estado"] == "PENDIENTE"


def test_proyecto_sin_perfil_no_es_pista(client, env, auth_as):
    """Sin perfil la clave caería en un espacio global: 'HOSPITALES' significa
    cosas distintas en Pachuca y en Villahermosa."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, ubicacion=None, perfil=None, proyecto="HOSPITALES")).json()
    assert oc["cliente_id"] is None


def test_sugerida_no_pisa_confirmada_y_avisa(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "NOMBRE", "BALLES", env["ehmo"])
    r = _externo(client, h, "NOMBRE", "BALLES", env["mafan"],
                 origen="BOT", confianza="SUGERIDA")
    assert r.status_code == 409                    # no un 201 mentiroso
    listado = client.get("/api/v1/clientes/externos", headers=h,
                         params={"sistema": "NOMBRE"}).json()
    assert listado[0]["cliente_id"] == env["ehmo"]


def test_punto_de_entrega_compartido_no_decide_el_cliente(client, env, auth_as):
    """Balles y Jubran comparten puntos de entrega. Si el punto votara por el
    cliente, toda orden de Jubran saldría «ambigua» contra Balles — o peor, se
    le facturaría a la razón social equivocada."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "NOMBRE", "BALLES", env["balles"])
    _externo(client, h, "NOMBRE", "JUBRAN", env["jubran"])
    # El mismo punto de entrega, registrado para cada razón social.
    _externo(client, h, "UBICACION", "PROCU", env["balles"], sucursal_id=env["suc_balles"])

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, nombre="JUBRAN", ubicacion="PROCU")).json()

    assert oc["ambiguo"] is False                    # el punto no contradice a nadie
    assert oc["cliente_id"] == env["jubran"]         # manda el nombre del documento
    assert oc["punto_entrega"] == "PROCU"
    # La sucursal le llega por SU PROPIO vínculo (Jubran tiene una sola plaza
    # — que es la misma que comparte con Balles), no por la equivalencia
    # UBICACION registrada para Balles. El cliente sigue siendo Jubran, que
    # es lo que este test protege: la razón social a la que se factura.
    assert oc["sucursal_id"] == env["suc_jubran"]


def test_el_punto_de_entrega_se_puede_corregir_a_mano(client, env, auth_as):
    """Lo que se corrige aquí es lo que sale impreso en la remisión y la factura."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={
        "cliente_id": env["ehmo"], "sucursal_id": env["suc"],
        "punto_entrega": "HOSPITAL JUAN GRAHAM (URGENCIAS)", "aprender": False})
    assert r.json()["punto_entrega"] == "HOSPITAL JUAN GRAHAM (URGENCIAS)"

    hecho = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}],
    }).json()
    rem = client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()
    assert (rem["notas"] or "").startswith("HOSPITAL JUAN GRAHAM (URGENCIAS)")


def test_el_almacen_se_resuelve_como_la_serie(client, env, auth_as):
    """sucursal → cliente → predeterminado. El bot no puede elegir almacén, y
    dejarlo vacío significaría no descontar inventario sin que nadie lo decida."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    # Cada intento es una OC distinta: repetir el folio para el mismo cliente
    # ahora es un duplicado y la bandeja lo rechaza.
    folios = iter(("2001", "2002", "2003"))

    def _crear():
        oc = client.post("/api/v1/oc-recibidas", headers=h,
                         json=_oc(folio_externo=next(folios))).json()
        client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"],
                           "aprender": False})
        hecho = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
            "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}],
        }).json()
        return client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()

    # Sin nada configurado y sin predeterminado: sale sin almacén (no toca stock).
    assert _crear()["almacen_id"] is None

    # Con almacén en el CLIENTE, lo hereda.
    client.patch(f"/api/v1/clientes/{env['ehmo']}", headers=h, json={"almacen_id": env["alm"]})
    assert _crear()["almacen_id"] == env["alm"]

    # El de la SUCURSAL gana sobre el del cliente.
    r = client.post("/api/v1/almacenes", headers=h,
                    json={"codigo": "OC-SUC", "nombre": "Bodega de la sucursal"})
    alm_suc = r.json()["id"]
    client.patch(f"/api/v1/sucursales/{env['suc']}", headers=h, json={"almacen_id": alm_suc})
    assert _crear()["almacen_id"] == alm_suc


def test_la_sucursal_del_grupo_es_la_ultima_red(client, env, auth_as):
    """Un hospital que nadie ha registrado, o una orden que no dice a dónde va:
    la entrega tiene que salir de algún lado igual. Lo que diga el grupo es lo
    más cercano a la verdad sin preguntarle a nadie."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    jid = "grupo-con-destino@g.us"

    # Sin sucursal por defecto: un punto desconocido no resuelve destino.
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        jid=jid, ubicacion="HOSPITAL QUE NADIE REGISTRÓ")).json()
    assert oc["cliente_id"] == env["ehmo"] and oc["sucursal_id"] is None

    # Se le pone al grupo su sucursal por defecto para ese cliente.
    assert _externo(client, h, "WHATSAPP", jid, env["ehmo"],
                    sucursal_id=env["suc"]).status_code == 201

    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        jid=jid, ubicacion="OTRO HOSPITAL DESCONOCIDO")).json()
    assert otra["sucursal_id"] == env["suc"]
    assert otra["punto_entrega"] == "OTRO HOSPITAL DESCONOCIDO"   # el texto se conserva


def test_asignar_no_borra_la_sucursal_por_defecto_del_grupo(client, env, auth_as):
    """Aprender el grupo al asignar una orden no puede llevarse de paso su
    sucursal por defecto — el bug que motivó el centinela en `aprender`."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-conserva@g.us"
    _externo(client, h, "WHATSAPP", jid, env["ehmo"], sucursal_id=env["suc"])

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, ubicacion=None, jid=jid)).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "aprender": True})

    wa = next(e for e in client.get("/api/v1/clientes/externos", headers=h,
                                    params={"sistema": "WHATSAPP"}).json()
              if e["clave"] == jid)
    assert wa["sucursal_id"] == env["suc"]     # sigue ahí


def test_la_serie_del_grupo_gana_sobre_la_del_cliente(client, env, auth_as):
    """Un cliente usa varias series según la operación por la que entra el
    pedido: en SAE, EHMO factura hospitales con una y costales con otra, y el
    grupo interno de Pachuca declara tres a la vez."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    jid = "grupo-con-serie@g.us"

    propia = client.post("/api/v1/series", headers=h, json={
        "codigo": "GRUPO1", "tipo_documento": "REMISION", "tipo": "NO_FISCAL"}).json()

    # Folio distinto por orden: el mismo pedido dos veces ya es un duplicado.
    folios = iter(("3001", "3002"))

    def _remision(**over):
        oc = client.post("/api/v1/oc-recibidas", headers=h,
                         json=_oc(jid=jid, folio_externo=next(folios), **over)).json()
        client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "aprender": False})
        hecho = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
            "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}]}).json()
        return client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()

    # Sin serie del grupo: la resuelve como siempre (default del inquilino).
    base = _remision()
    assert not base["folio_interno"].startswith("GRUPO1")

    # Con serie del grupo, esa manda.
    _externo(client, h, "WHATSAPP", jid, env["ehmo"], serie_remision_id=propia["id"])
    assert _remision()["folio_interno"].startswith("GRUPO1")


def test_la_serie_del_grupo_sobrevive_a_aprender(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-serie-conserva@g.us"
    propia = client.post("/api/v1/series", headers=h, json={
        "codigo": "GRUPO2", "tipo_documento": "REMISION", "tipo": "NO_FISCAL"}).json()
    _externo(client, h, "WHATSAPP", jid, env["ehmo"], serie_remision_id=propia["id"])

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, ubicacion=None, jid=jid)).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "aprender": True})

    wa = next(e for e in client.get("/api/v1/clientes/externos", headers=h,
                                    params={"sistema": "WHATSAPP"}).json()
              if e["clave"] == jid)
    assert wa["serie_remision_id"] == propia["id"]


def test_asignar_sucursal_limpia_el_motivo_viejo(client, env, auth_as):
    """El motivo es lo que la bandeja le enseña al operador: si decía «falta la
    sucursal» y la sucursal ya se asignó, dejarlo manda a revisar algo resuelto."""
    from app.core.db import SessionLocal
    from app.models import OCRecibida

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    suc_id = env["suc"]          # sucursal de EHMO, del fixture

    db = SessionLocal()
    try:
        db.query(OCRecibida).filter(OCRecibida.id == uuid.UUID(oc["id"])).update(
            {"motivo": "Falta decir a qué sucursal pertenece «APAN»"})
        db.commit()
    finally:
        db.close()

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "sucursal_id": suc_id})
    assert r.status_code == 200, r.text
    assert r.json()["motivo"] is None

    # un motivo que NO habla de la sucursal se respeta (no es la causa resuelta)
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"motivo": "revisar precios con el cliente"})
    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={"sucursal_id": suc_id})
    assert r.json()["motivo"] == "revisar precios con el cliente"


# ─── vistazo (slidedown de la lista) ─────────────────────────────────────────

def test_vistazo_trae_las_partidas_sin_cruce_ni_auto(client, env, auth_as):
    """El slidedown solo enseña las partidas como venían: con `vistazo=true`
    el detalle no carga catálogo, no cruza candidatos y no evalúa precios —
    era lo que hacía tardar segundos un renglón desplegado."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc())
    assert r.status_code == 201, r.text
    oc_id = r.json()["id"]

    v = client.get(f"/api/v1/oc-recibidas/{oc_id}", headers=h, params={"vistazo": "true"}).json()
    assert v["auto"] is None
    assert v["lineas"][0]["descripcion"] == "JITOMATE SALADET"
    assert v["lineas"][0]["candidatos"] == []

    # Sin el flag, el detalle completo sigue cruzando y evaluando como siempre.
    d = client.get(f"/api/v1/oc-recibidas/{oc_id}", headers=h).json()
    assert d["auto"] is not None


# ─── búsqueda, filtro por proyecto y observaciones en la lista ───────────────

def test_buscar_por_observaciones_y_punto_de_entrega(client, env, auth_as):
    """`q` también encuentra por lo que el documento decía fuera de las
    partidas: es donde vive "el pedido que decía tal cosa" cuando el folio
    no se sabe."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    marca = uuid.uuid4().hex[:8]
    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        folio_externo=f"OBS-{marca}",
        observaciones=f"ENTREGAR ANTES DE LAS 9 REF {marca}",
        ubicacion=f"HOSPITAL {marca}",
    ))
    assert r.status_code == 201, r.text

    por_obs = client.get("/api/v1/oc-recibidas", headers=h,
                         params={"q": f"REF {marca}"}).json()
    assert [x["folio_externo"] for x in por_obs["items"]] == [f"OBS-{marca}"]
    # La lista trae las observaciones para su columna, sin abrir el detalle.
    assert f"REF {marca}" in por_obs["items"][0]["observaciones"]

    por_punto = client.get("/api/v1/oc-recibidas", headers=h,
                           params={"q": f"HOSPITAL {marca}"}).json()
    assert [x["folio_externo"] for x in por_punto["items"]] == [f"OBS-{marca}"]


def test_filtrar_por_proyecto(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    marca = uuid.uuid4().hex[:6]
    proy = client.post("/api/v1/proyectos", headers=h,
                       json={"nombre": f"HOSPITALES {marca}"}).json()
    a = client.post("/api/v1/oc-recibidas", headers=h,
                    json=_oc(folio_externo=f"PA-{marca}")).json()
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(folio_externo=f"PB-{marca}"))
    client.patch(f"/api/v1/oc-recibidas/{a['id']}", headers=h,
                 json={"proyecto_id": proy["id"], "aprender": False})

    filtrado = client.get("/api/v1/oc-recibidas", headers=h,
                          params={"proyecto_id": proy["id"]}).json()
    assert [x["folio_externo"] for x in filtrado["items"]] == [f"PA-{marca}"]
    assert filtrado["items"][0]["proyecto_nombre"] == f"HOSPITALES {marca}"


# ─── filtro por grupo de origen (filtros encadenados de la lista) ────────────

# ─── pasar a Remisiones sin revisar ──────────────────────────────────────────
# El atajo de un clic solo acepta la orden perfecta. Esto la pasa igual y deja
# la revisión para la pantalla de Remisiones — con el freno de que, hasta que
# alguien la mire, la remisión no se confirma, no se factura y no sale a SAE.

def _oc_mixta(client, h, env):
    """Una orden con una partida que cruza y otra que no existe en el catálogo."""
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=[
        {"descripcion": "JITOMATE SALADET", "cantidad": "25", "unidad": "KG",
         "precio": "18.50", "clave": "C-77"},
        {"descripcion": "ZZZ ARTICULO QUE NO ESTA EN EL CATALOGO", "cantidad": "3",
         "unidad": "CAJA", "clave": "C-99"},
    ])).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    return oc


def test_sin_revisar_pasa_lo_que_cruza_y_conserva_lo_que_no(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = _oc_mixta(client, h, env)

    r = client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-sin-revisar?almacen_id={env['alm']}",
        headers=h,
    )
    assert r.status_code == 200, r.text
    hecho = r.json()
    assert hecho["estado"] == "ASIGNADA"
    assert hecho["remision_id"] and hecho["remision_folio"]

    rem = client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()
    assert rem["revision_pendiente"] is True
    # La que cruzó es línea; la que no, viaja aparte tal como venía.
    assert len(rem["lineas"]) == 1
    assert [p["numero"] for p in rem["partidas_por_cruzar"]] == [2]
    sin_cruzar = rem["partidas_por_cruzar"][0]
    assert sin_cruzar["descripcion"] == "ZZZ ARTICULO QUE NO ESTA EN EL CATALOGO"
    assert sin_cruzar["cantidad"] == "3"
    assert sin_cruzar["unidad"] == "CAJA"
    assert sin_cruzar["clave"] == "C-99"

    # La línea se lleva escrito lo que venía y qué hay que mirar: es lo único
    # que el revisor tendrá enfrente cuando la abra.
    notas = rem["lineas"][0]["notas"] or ""
    assert "Como venía: «JITOMATE SALADET»" in notas
    assert "clave C-77" in notas
    assert "Revisar:" in notas
    # Sin lista de precios, el precio que entra es el del documento (anotado).
    assert float(rem["lineas"][0]["precio_unitario"]) == 18.5


def test_la_reposicion_entra_en_cero_y_con_su_marca(client, env, auth_as):
    """Una REPOSICIÓN se surte y NO se cobra (regla del dueño, 17-ago-2026).

    El lote lo manda el bot con la partida y la conversión lo ignoraba: la
    cobraba a precio de lista. Ahora entra en cero, y las dos marcas van al
    PRINCIPIO de la nota, que es donde las buscan la nota de remisión, el armado
    y el pronóstico del bot. Un EXTRA sí se cobra: solo lleva su marca.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=[
        {"descripcion": "JITOMATE SALADET", "cantidad": "25", "unidad": "KG",
         "precio": "18.50", "lote": "REPOSICIÓN"},
        {"descripcion": "JITOMATE SALADET", "cantidad": "5", "unidad": "KG",
         "precio": "18.50", "lote": "EXTRAS"},
    ])).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    r = client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-sin-revisar?almacen_id={env['alm']}",
        headers=h,
    )
    assert r.status_code == 200, r.text
    rem = client.get(f"/api/v1/remisiones/{r.json()['remision_id']}", headers=h).json()
    por_cant = {float(l["cantidad_solicitada"]): l for l in rem["lineas"]}
    repo, extra = por_cant[25.0], por_cant[5.0]
    assert float(repo["precio_unitario"]) == 0
    assert (repo["notas"] or "").startswith("REPOSICIÓN — se surte, no se cobra")
    assert float(extra["precio_unitario"]) == 18.5
    assert (extra["notas"] or "").startswith("EXTRA")


def test_sin_revisar_no_le_ensena_nada_al_catalogo(client, env, auth_as):
    """Un cruce que nadie confirmó no aprende alias ni estampa código de cliente.

    `crear-remision` sí aprende, porque ahí un humano acaba de validar la
    partida. Aquí no ha mirado nadie: aprender sería repetir para siempre un
    falso positivo, y `codigo_cliente` es el NoIdentificacion de sus CFDI.
    """
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = _oc_mixta(client, h, env)
    assert client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-sin-revisar", headers=h
    ).status_code == 200

    db = SessionLocal()
    try:
        tid = env["admin_a"]["tenant_id"]
        alias = db.execute(
            text("SELECT count(*) FROM producto_alias WHERE tenant_id = :t"), {"t": tid}
        ).scalar()
        codigos = db.execute(
            text("SELECT count(*) FROM producto_clientes WHERE tenant_id = :t"), {"t": tid}
        ).scalar()
    finally:
        db.close()
    assert alias == 0
    assert codigos == 0


def test_sin_revisar_no_se_confirma_ni_se_factura(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = _oc_mixta(client, h, env)
    rid = client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-sin-revisar?almacen_id={env['alm']}",
        headers=h,
    ).json()["remision_id"]

    r = client.post(f"/api/v1/remisiones/{rid}/confirmar", headers=h, json={})
    assert r.status_code == 409
    assert "sin revisar" in r.json()["detail"].lower()

    f = client.post("/api/v1/facturas/desde-remisiones", headers=h,
                    json={"remision_ids": [rid]})
    assert f.status_code == 409
    assert "sin revisar" in f.json()["detail"].lower()


def test_no_se_da_por_revisada_con_partidas_sin_cruzar(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = _oc_mixta(client, h, env)
    rid = client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-sin-revisar?almacen_id={env['alm']}",
        headers=h,
    ).json()["remision_id"]

    # Queda una partida sin cruzar: darla por revisada sería tirarla en silencio.
    r = client.patch(f"/api/v1/remisiones/{rid}", headers=h,
                     json={"revision_pendiente": False})
    assert r.status_code == 409
    assert "sin cruzar" in r.json()["detail"].lower()

    # Resuelta (agregada como línea o descartada a mano), ya se puede.
    r = client.patch(f"/api/v1/remisiones/{rid}", headers=h,
                     json={"partidas_por_cruzar": [], "revision_pendiente": False})
    assert r.status_code == 200, r.text
    assert r.json()["revision_pendiente"] is False

    # Y con eso se levanta el freno.
    assert client.post(
        f"/api/v1/remisiones/{rid}/confirmar", headers=h, json={}
    ).status_code != 409


def test_sin_revisar_no_quema_folio_si_nada_cruza(client, env, auth_as):
    """Una remisión sin líneas no es un documento a medias: es un folio perdido."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=[
        {"descripcion": "ZZZ NADA DE ESTO EXISTE", "cantidad": "1", "unidad": "PZA"},
    ])).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})

    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-sin-revisar", headers=h)
    assert r.status_code == 409
    assert "ninguna partida" in r.json()["detail"].lower()
    assert client.get(f"/api/v1/oc-recibidas/{oc['id']}", headers=h).json()["remision_id"] is None


def test_la_lista_de_remisiones_filtra_las_que_faltan_por_revisar(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = _oc_mixta(client, h, env)
    rid = client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-sin-revisar", headers=h
    ).json()["remision_id"]

    por_revisar = client.get("/api/v1/remisiones?revision_pendiente=true", headers=h).json()
    assert [x["id"] for x in por_revisar["items"]] == [rid]
    revisadas = client.get("/api/v1/remisiones?revision_pendiente=false", headers=h).json()
    assert rid not in [x["id"] for x in revisadas["items"]]


# ─── ingesta directa: lo que resuelve nace remisión «por revisar» ────────────
# El ticket «Une los menús»: la bandeja dejó de ser parada obligatoria. La
# orden que trae cliente y destino resueltos genera su remisión en el mismo
# request de ingesta; lo que no cruza queda PENDIENTE con su motivo y se
# resuelve en la franja de órdenes por resolver de /remisiones.

def _destino_resuelto(client, h, env):
    """RFC → cliente y hospital → sucursal: todo lo que una orden necesita
    para volverse remisión sin que nadie la toque."""
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    _externo(client, h, "UBICACION", "villahermosa:JUAN GRAHAM", env["ehmo"],
             sucursal_id=env["suc"])


def test_ingesta_con_destino_resuelto_nace_remision_por_revisar(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _destino_resuelto(client, h, env)

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["estado"] == "ASIGNADA", oc
    assert oc["remision_id"] and oc["remision_folio"]

    rem = client.get(f"/api/v1/remisiones/{oc['remision_id']}", headers=h).json()
    assert rem["estado"] == "BORRADOR"
    assert rem["revision_pendiente"] is True    # el freno viaja puesto
    assert rem["su_pedido"] == "1188"

    # Idempotencia intacta: el reintento del bot devuelve la MISMA captura.
    again = client.post("/api/v1/oc-recibidas", headers=h,
                        json=_oc(origen_externo=oc["origen_externo"])).json()
    assert again["remision_id"] == oc["remision_id"]
    assert client.get("/api/v1/remisiones", headers=h).json()["total"] == 1


def test_ingesta_duplicada_de_una_captura_manual_queda_pendiente(client, env, auth_as):
    """El candado de PR #100 visto desde la ingesta: no revienta al bot y deja
    el motivo escrito, con el folio de la remisión que ya existe."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _destino_resuelto(client, h, env)

    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["ehmo"],
        "sucursal_id": env["suc"],
        "almacen_id": env["alm"],
        "su_pedido": "OC 1188",
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": "10",
                    "precio_unitario": "20"}],
    })
    assert rem.status_code == 201, rem.text

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["estado"] == "PENDIENTE"
    assert oc["remision_id"] is None
    assert oc["motivo"].startswith("No se pudo pasar a remisiones en automático")
    assert rem.json()["folio_interno"] in oc["motivo"]


def test_ingesta_que_no_cruza_nada_queda_pendiente_con_motivo(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _destino_resuelto(client, h, env)
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(lineas=[
        {"descripcion": "ZZZ NADA DE ESTO EXISTE", "cantidad": "1", "unidad": "PZA"},
    ])).json()
    assert oc["estado"] == "PENDIENTE" and oc["remision_id"] is None
    assert "Ninguna partida" in oc["motivo"]


def test_reintento_no_convierte_lo_detenido_a_mano(client, env, auth_as):
    """Una orden que un humano asignó (y dejó quieta a propósito) no se vuelve
    remisión porque el bot la reenvíe; el lote explícito sí la toma."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["estado"] == "PENDIENTE"       # el hospital no está mapeado

    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    again = client.post("/api/v1/oc-recibidas", headers=h,
                        json=_oc(origen_externo=oc["origen_externo"])).json()
    assert again["estado"] == "PENDIENTE" and again["remision_id"] is None

    r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["creadas"] == 1
    hecha = client.get(f"/api/v1/oc-recibidas/{oc['id']}", headers=h).json()
    assert hecha["estado"] == "ASIGNADA" and hecha["remision_id"]


def test_procesar_pendientes_destraba_el_backlog_al_mapear_el_hospital(client, env, auth_as):
    """Aprender un destino destraba TODAS las órdenes acumuladas de ese punto
    de entrega en una pasada, sin abrirlas una por una. Lo marcado EN DUDA por
    un humano no lo toca ningún lote."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    a = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    b = client.post("/api/v1/oc-recibidas", headers=h,
                    json=_oc(folio_externo="1189")).json()
    dudosa = client.post("/api/v1/oc-recibidas", headers=h,
                         json=_oc(folio_externo="1190")).json()
    assert {a["estado"], b["estado"], dudosa["estado"]} == {"PENDIENTE"}
    client.patch(f"/api/v1/oc-recibidas/{dudosa['id']}", headers=h,
                 json={"motivo": "EN DUDA - entró dos veces; revisar antes de remisionar"})

    _externo(client, h, "UBICACION", "villahermosa:JUAN GRAHAM", env["ehmo"],
             sucursal_id=env["suc"])
    r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=h).json()
    assert r["creadas"] == 2
    assert r["restantes"] == 0               # la dudosa no cuenta como candidata

    for creada in (a, b):
        hecha = client.get(f"/api/v1/oc-recibidas/{creada['id']}", headers=h).json()
        assert hecha["estado"] == "ASIGNADA" and hecha["remision_id"]
    quieta = client.get(f"/api/v1/oc-recibidas/{dudosa['id']}", headers=h).json()
    assert quieta["estado"] == "PENDIENTE" and quieta["remision_id"] is None
    assert quieta["motivo"].startswith("EN DUDA")


def test_lote_salta_lo_que_necesita_humano_y_no_se_atora(client, env, auth_as):
    """La cabeza de la fila puede ser una orden que el lote NO puede procesar
    (hospital sin mapear). El drenado del 10-sep se congelaba ahí: el limit
    del query tomaba a las mismas una y otra vez con creadas=0. El lote las
    salta —se quedan para un humano— y las convertibles de atrás sí pasan."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    atorada = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        ubicacion="HOSPITAL QUE NADIE HA MAPEADO")).json()
    convertible = client.post("/api/v1/oc-recibidas", headers=h,
                              json=_oc(folio_externo="1189")).json()
    assert atorada["estado"] == convertible["estado"] == "PENDIENTE"

    # Se mapea SOLO el punto de la convertible; la atorada sigue sin destino.
    _externo(client, h, "UBICACION", "villahermosa:JUAN GRAHAM", env["ehmo"],
             sucursal_id=env["suc"])

    r = client.post("/api/v1/oc-recibidas/procesar-pendientes?limite=1", headers=h).json()
    assert r["creadas"] == 1          # la convertible pasó AUNQUE la atorada va primero
    hecha = client.get(f"/api/v1/oc-recibidas/{convertible['id']}", headers=h).json()
    assert hecha["estado"] == "ASIGNADA" and hecha["remision_id"]
    sigue = client.get(f"/api/v1/oc-recibidas/{atorada['id']}", headers=h).json()
    assert sigue["estado"] == "PENDIENTE" and sigue["remision_id"] is None
    assert "sucursal" in (sigue["motivo"] or "").lower()


def test_robot_con_cliente_de_una_sola_plaza_pasa_directo(client, env, auth_as):
    """Ticket 86bbxx6ge: el pedido del robot de un cliente con UNA sola plaza
    vinculada no espera a nadie — la plaza no deja nada que adivinar, aunque
    su punto de entrega no esté mapeado (va a las observaciones, como
    siempre). El cliente multi-plaza (EHMO) sí espera el mapeo del punto."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "DAP250922PY2", env["jubran"])

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc="DAP250922PY2", folio_externo="7701",
        ubicacion="PUNTO QUE NADIE HA MAPEADO")).json()
    assert oc["sucursal_id"] == env["suc_jubran"]
    assert oc["estado"] == "ASIGNADA" and oc["remision_id"], oc["motivo"]
    assert oc["punto_entrega"] == "PUNTO QUE NADIE HA MAPEADO"


# ─── la orden cambió DESPUÉS de volverse remisión (0067) ─────────────────────
#
# El reenvío de una orden ya remisionada no toca la captura —una remisión no se
# corrige sola— pero hasta 0067 tampoco decía nada: el Master se quedaba con la
# versión nueva y la remisión con la vieja, y eso se descubría cuando el cliente
# reclamaba. Estas pruebas fijan que se detecte, que NO se pise nada, y que la
# incidencia se cierre con nombre y motivo en vez de "marcar como leída".

def _oc_remisionada(client, h, env, **over):
    """Una OC resuelta y ya convertida en remisión BORRADOR.

    Con la ingesta directa, una orden cuyo destino ya se aprendió se convierte
    SOLA al llegar; si no, se sigue el camino manual de siempre. El resultado
    para estas pruebas es el mismo: una OC ASIGNADA con su remisión."""
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(**over)).json()
    if oc.get("remision_id"):
        return oc
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})
    body = {"almacen_id": env["alm"], "lineas": [{
        "producto_id": env["prod"], "cantidad": "25", "precio_unitario": "18.50",
        "texto_original": "JITOMATE SALADET"}]}
    return client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision",
                       headers=h, json=body).json()


def test_reenvio_identico_no_abre_incidencia(client, env, auth_as):
    """El bot reintenta por timeout: eso no es un cambio y no debe avisar."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    hecho = _oc_remisionada(client, h, env)

    again = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=hecho["origen_externo"])).json()
    assert again["cambio_abierto"] is False
    assert again["cambio_detectado_at"] is None


def test_reenvio_con_cambios_abre_la_incidencia_sin_tocar_la_remision(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    hecho = _oc_remisionada(client, h, env)

    cambiada = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=hecho["origen_externo"],
        fecha_entrega="2026-09-05",
        lineas=[
            {"descripcion": "JITOMATE SALADET", "cantidad": "40", "unidad": "KG"},
            {"descripcion": "CEBOLLA BLANCA", "cantidad": "8", "unidad": "KG"},
        ])).json()

    assert cambiada["cambio_abierto"] is True
    assert cambiada["cambio_detectado_at"]
    resumen = cambiada["cambio_resumen"]
    assert "1 partida nueva" in resumen and "1 partida cambiada" in resumen
    assert "2026-09-05" in resumen

    # Nada de la captura se movió: misma remisión, mismo estado, y `payload`
    # sigue siendo la versión con la que se remisionó (es la evidencia).
    assert cambiada["remision_id"] == hecho["remision_id"]
    assert cambiada["estado"] == "ASIGNADA"
    congelado = cambiada["payload"]["lineas"]
    assert [(x["descripcion"], x["cantidad"]) for x in congelado] == [("JITOMATE SALADET", "25")]
    assert len(cambiada["payload_nuevo"]["lineas"]) == 2

    d = cambiada["cambio_detalle"]["lineas"]
    assert [x["descripcion"] for x in d["nuevas"]] == ["CEBOLLA BLANCA"]
    assert d["quitadas"] == []
    assert d["cambiadas"][0]["ahora"][0]["cantidad"] == "40"


def test_reordenar_las_partidas_no_es_un_cambio(client, env, auth_as):
    """El PDF llega con las partidas en otro orden: mismo pedido, sin aviso."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    dos = [{"descripcion": "JITOMATE SALADET", "cantidad": "25", "unidad": "KG"},
           {"descripcion": "CEBOLLA BLANCA", "cantidad": "8", "unidad": "KG"}]
    hecho = _oc_remisionada(client, h, env, lineas=dos)

    again = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=hecho["origen_externo"], lineas=list(reversed(dos)))).json()
    assert again["cambio_abierto"] is False


def test_el_mismo_reenvio_cambiado_no_vuelve_a_avisar(client, env, auth_as):
    """Un aviso por cada reintento es la forma más rápida de que los ignoren."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    hecho = _oc_remisionada(client, h, env)
    nuevas = [{"descripcion": "JITOMATE SALADET", "cantidad": "40", "unidad": "KG"}]

    a = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=hecho["origen_externo"], lineas=nuevas)).json()
    b = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=hecho["origen_externo"], lineas=nuevas)).json()
    assert a["cambio_detectado_at"] == b["cambio_detectado_at"]


def test_volver_a_la_version_remisionada_cierra_la_incidencia(client, env, auth_as):
    """El cliente deshizo su corrección: ya no hay nada que atender."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    hecho = _oc_remisionada(client, h, env)
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=hecho["origen_externo"],
        lineas=[{"descripcion": "JITOMATE SALADET", "cantidad": "40", "unidad": "KG"}]))

    vuelta = client.post("/api/v1/oc-recibidas", headers=h,
                         json=_oc(origen_externo=hecho["origen_externo"])).json()
    assert vuelta["cambio_abierto"] is False
    assert vuelta["cambio_resuelto_at"]
    assert "volvió a coincidir" in vuelta["cambio_resuelto_nota"]


def test_resolver_el_cambio_exige_nota_y_no_se_cierra_dos_veces(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    hecho = _oc_remisionada(client, h, env)
    oc_id = hecho["id"]
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=hecho["origen_externo"],
        lineas=[{"descripcion": "JITOMATE SALADET", "cantidad": "40", "unidad": "KG"}]))

    assert client.post(f"/api/v1/oc-recibidas/{oc_id}/cambio/resolver",
                       headers=h, json={"nota": "  "}).status_code == 422

    r = client.post(f"/api/v1/oc-recibidas/{oc_id}/cambio/resolver", headers=h,
                    json={"nota": "Corregí la remisión BORRADOR a 40 KG"})
    assert r.status_code == 200, r.text
    cerrada = r.json()
    assert cerrada["cambio_abierto"] is False
    assert cerrada["cambio_resuelto_por"]
    assert cerrada["cambio_resuelto_nota"] == "Corregí la remisión BORRADOR a 40 KG"
    # El diff NO se borra: es la evidencia de qué se decidió y sobre qué.
    assert cerrada["cambio_detalle"]["lineas"]["cambiadas"]

    assert client.post(f"/api/v1/oc-recibidas/{oc_id}/cambio/resolver", headers=h,
                       json={"nota": "otra vez"}).status_code == 409


def test_sin_cambio_no_se_puede_resolver(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    hecho = _oc_remisionada(client, h, env)
    assert client.post(f"/api/v1/oc-recibidas/{hecho['id']}/cambio/resolver",
                       headers=h, json={"nota": "nada que ver"}).status_code == 409


def test_el_filtro_de_la_bandeja_solo_trae_las_abiertas(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    # Folios DISTINTOS: el guard de duplicados (PR #100) rechaza remisionar dos
    # órdenes con el mismo «su pedido», y aquí hacen falta dos remisiones vivas.
    tranquila = _oc_remisionada(client, h, env, folio_externo=f"T{uuid.uuid4().hex[:5]}")
    ruidosa = _oc_remisionada(client, h, env, folio_externo=f"R{uuid.uuid4().hex[:5]}")
    client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=ruidosa["origen_externo"],
        lineas=[{"descripcion": "JITOMATE SALADET", "cantidad": "40", "unidad": "KG"}]))

    abiertas = client.get("/api/v1/oc-recibidas?cambio_abierto=true", headers=h).json()
    ids = [x["id"] for x in abiertas["items"]]
    assert ruidosa["id"] in ids
    assert tranquila["id"] not in ids

    client.post(f"/api/v1/oc-recibidas/{ruidosa['id']}/cambio/resolver", headers=h,
                json={"nota": "hablé con el cliente, se entrega lo original"})
    despues = client.get("/api/v1/oc-recibidas?cambio_abierto=true", headers=h).json()
    assert ruidosa["id"] not in [x["id"] for x in despues["items"]]


def test_una_orden_descartada_que_cambia_no_revive(client, env, auth_as):
    """Descartar es una decisión de una persona; un reenvío no la deshace."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    client.post(f"/api/v1/oc-recibidas/{oc['id']}/descartar", headers=h,
                params={"motivo": "duplicada"})
    again = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=oc["origen_externo"],
        lineas=[{"descripcion": "OTRA COSA", "cantidad": "1", "unidad": "PZ"}])).json()
    assert again["estado"] == "DESCARTADA"
    assert again["cambio_abierto"] is False


def test_folio_sin_semana_quita_la_b():
    """La «-B» es parte de la semana: la entrega VH-38ROV-LUN-B y un reenvío
    viejo de la misma como VH-39ROV-LUN deben reconocerse como la misma."""
    from app.api.v1.oc_recibidas import _RE_SUFIJO_APARTE, _folio_sin_semana

    assert _folio_sin_semana("VH-38ROV-LUN-B") == "VH-ROV-LUN"
    assert _folio_sin_semana("VH-39ROV-LUN") == "VH-ROV-LUN"
    assert _folio_sin_semana("VH-38ROV-LUN-2") is None
    m = _RE_SUFIJO_APARTE.match("VH-38ROV-LUN-B-2")
    assert m and m.group(1) == "VH-38ROV-LUN-B" and m.group(2) == "2"
    assert _RE_SUFIJO_APARTE.match("VH-38ROV-LUN-B") is None


# ─── el lote también destraba las órdenes SIN CLIENTE (26-sep-2026) ──────────
# El grupo de Pachuca pasó a perfil «ehmo-pachuca» y 30 órdenes entraron sin
# cliente. Arreglado el cruce, el botón «Procesar órdenes» no las veía: solo
# tomaba las que ya tenían cliente. Y tres de ellas eran reenvíos de entregas
# que ya eran remisión bajo el ancla vieja: convertirlas duplicaba.

def test_el_lote_destraba_las_ordenes_sin_cliente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, proyecto="HOSPITALES", folio_externo="VH-39JUA-LUN")).json()
    assert oc["estado"] == "PENDIENTE" and oc["cliente_id"] is None

    # Se aprende DESPUÉS de que la orden llegó: es lo que el lote debe recoger.
    _externo(client, h, "PROYECTO", "villahermosa:HOSPITALES", env["ehmo"])
    _externo(client, h, "UBICACION", "villahermosa:JUAN GRAHAM", env["ehmo"],
             sucursal_id=env["suc"])

    r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=h).json()
    assert r["creadas"] == 1 and r["restantes"] == 0
    hecha = client.get(f"/api/v1/oc-recibidas/{oc['id']}", headers=h).json()
    assert hecha["cliente_id"] == env["ehmo"] and hecha["sucursal_id"] == env["suc"]
    assert hecha["estado"] == "ASIGNADA" and hecha["remision_id"]


def test_el_lote_con_alcance_no_toca_las_ordenes_sin_cliente(client, env, auth_as):
    """Quien tiene alcance por cliente no ve las órdenes sin cliente en la
    franja: el lote tampoco se las asigna ni se las convierte."""
    from app.core.rbac import invalidate_auth_cache
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, proyecto="HOSPITALES", folio_externo="VH-39JUA-LUN")).json()
    assert oc["cliente_id"] is None
    _externo(client, h, "PROYECTO", "villahermosa:HOSPITALES", env["ehmo"])
    _externo(client, h, "UBICACION", "villahermosa:JUAN GRAHAM", env["ehmo"],
             sucursal_id=env["suc"])

    db = SessionLocal()
    sub = f"sub-oc-scope-{uuid.uuid4().hex[:8]}"
    u = m = None
    try:
        rol = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        u = User(email=f"{sub}@t.test", auth_user_id=sub, full_name="scope")
        db.add(u); db.flush()
        m = Membership(tenant_id=env["admin_a"]["tenant_id"], user_id=u.id, role_id=rol.id,
                       cliente_scope=[uuid.UUID(env["ehmo"])])
        db.add(m); db.commit()
        invalidate_auth_cache()
        atado = {"sub": sub, "email": u.email, "tenant_id": env["admin_a"]["tenant_id"]}

        auth_as(atado)
        r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=_hdr(atado)).json()
        assert r == {"creadas": 0, "fallidas": 0, "restantes": 0}
        auth_as(env["admin_a"])
        sigue = client.get(f"/api/v1/oc-recibidas/{oc['id']}", headers=h).json()
        assert sigue["cliente_id"] is None and sigue["remision_id"] is None
    finally:
        if m is not None:
            db.query(Membership).filter(Membership.id == m.id).delete()
        if u is not None:
            db.query(User).filter(User.id == u.id).delete()
        db.commit(); db.close()
        invalidate_auth_cache()


def test_la_misma_entrega_con_otra_ancla_no_se_remisiona_dos_veces(client, env, auth_as):
    """El caso CE-38CER: la entrega ya era remisión bajo «EHMO:ehmo:…» y el bot
    la reenvió como «EHMO:ehmo-pachuca:…». Mismo folio, fecha y cliente: el
    camino automático no genera la segunda; queda para una persona, con el
    folio de la remisión que ya existe en el motivo."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    _externo(client, h, "UBICACION", "ehmo:JUAN GRAHAM", env["ehmo"], sucursal_id=env["suc"])
    base = dict(folio_externo="CE-38CER-LUN", fecha_entrega="2026-09-21")

    primera = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo", origen_externo="EHMO:ehmo:CE-38CER-LUN", **base)).json()
    assert primera["estado"] == "ASIGNADA" and primera["remision_folio"]

    # Llegó sin cliente (antes de la herencia de perfil) y sin pasar por los
    # candados de ingesta de hoy — `forzar` reproduce eso.
    reenvio = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo-pachuca", origen_externo="EHMO:ehmo-pachuca:CE-38CER-LUN",
        rfc=None, proyecto="HOSPITALES", forzar=True, **base)).json()
    assert reenvio["estado"] == "PENDIENTE" and reenvio["cliente_id"] is None

    _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["ehmo"])
    r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=h).json()
    assert r["creadas"] == 0 and r["fallidas"] == 1

    quieta = client.get(f"/api/v1/oc-recibidas/{reenvio['id']}", headers=h).json()
    assert quieta["cliente_id"] == env["ehmo"]          # sí se resolvió…
    assert quieta["estado"] == "PENDIENTE" and quieta["remision_id"] is None  # …pero no se duplicó
    assert quieta["motivo"].startswith("No se pudo pasar a remisiones en automático")
    assert primera["remision_folio"] in quieta["motivo"]
    assert "descarta esta orden" in quieta["motivo"]      # mismo contenido: es el reenvío
    assert client.get("/api/v1/remisiones", headers=h).json()["total"] == 1

    # Ya explicada, el siguiente lote no la vuelve a intentar.
    r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=h).json()
    assert r == {"creadas": 0, "fallidas": 0, "restantes": 0}


@pytest.mark.parametrize("lineas, cuantas", [
    # El caso real: otro archivo, otra semana, más partidas.
    ([{"descripcion": "JITOMATE SALADET", "cantidad": "40", "unidad": "KG"},
      {"descripcion": "CEBOLLA BLANCA", "cantidad": "10", "unidad": "KG"},
      {"descripcion": "CHILE SERRANO", "cantidad": "5", "unidad": "KG"}],
     "3 vs 1 partidas"),
    # Mismas partidas en número, otra cantidad: tampoco es la misma entrega.
    ([{"descripcion": "JITOMATE SALADET", "cantidad": "30", "unidad": "KG"}],
     "1 partida cada una"),
])
def test_mismo_folio_y_fecha_con_otro_contenido_no_se_manda_a_descartar(
        client, env, auth_as, lineas, cuantas):
    """CE-38CER-LUN/MAR/MIE, 26-sep-2026: el folio y la fecha coincidían con
    RFMAFAN32-34, pero eran la semana 39 con fechas de la 38 y otro contenido.
    Se sigue frenando el camino automático, pero el motivo ya no dice
    «descártala»: dice que es otro pedido y que la fecha está mal."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    _externo(client, h, "UBICACION", "ehmo:JUAN GRAHAM", env["ehmo"], sucursal_id=env["suc"])
    base = dict(folio_externo="CE-38CER-LUN", fecha_entrega="2026-09-21")

    primera = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo", origen_externo="EHMO:ehmo:CE-38CER-LUN", **base)).json()
    assert primera["estado"] == "ASIGNADA" and primera["remision_folio"]

    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo-pachuca", origen_externo="EHMO:ehmo-pachuca:CE-38CER-LUN",
        rfc=None, proyecto="HOSPITALES", forzar=True, lineas=lineas, **base)).json()
    assert otra["estado"] == "PENDIENTE" and otra["cliente_id"] is None

    _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["ehmo"])
    r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=h).json()
    assert r["creadas"] == 0 and r["fallidas"] == 1

    quieta = client.get(f"/api/v1/oc-recibidas/{otra['id']}", headers=h).json()
    assert quieta["estado"] == "PENDIENTE" and quieta["remision_id"] is None
    motivo = quieta["motivo"]
    assert motivo.startswith("No se pudo pasar a remisiones en automático")
    assert primera["remision_folio"] in motivo
    assert "otro contenido" in motivo and cuantas in motivo
    assert "fecha mal puesta" in motivo
    assert "descarta esta orden" not in motivo

    db = SessionLocal()
    try:
        vivas = db.execute(text(
            "SELECT count(*) FROM remisiones WHERE tenant_id = :t AND deleted_at IS NULL"),
            {"t": env["admin_a"]["tenant_id"]}).scalar()
    finally:
        db.close()
    assert vivas == 1


def test_la_misma_entrega_se_vuelve_a_remisionar_si_la_anterior_se_cancelo(client, env, auth_as):
    """Una remisión cancelada no cuenta como la entrega hecha: ahí la nueva sí
    hace falta y el candado no estorba."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    _externo(client, h, "UBICACION", "ehmo:JUAN GRAHAM", env["ehmo"], sucursal_id=env["suc"])
    base = dict(folio_externo="CE-38CER-LUN", fecha_entrega="2026-09-21")
    primera = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo", origen_externo="EHMO:ehmo:CE-38CER-LUN", **base)).json()
    assert primera["remision_id"]

    db = SessionLocal()
    try:
        db.execute(text("UPDATE remisiones SET estado = 'CANCELADA' WHERE id = :id"),
                   {"id": primera["remision_id"]})
        db.commit()
    finally:
        db.close()

    reenvio = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo-pachuca", origen_externo="EHMO:ehmo-pachuca:CE-38CER-LUN",
        forzar=True, **base)).json()
    assert reenvio["estado"] == "ASIGNADA" and reenvio["remision_id"], reenvio["motivo"]


def test_el_lote_no_revive_lo_que_se_descarto_mientras_corria(client, env, auth_as, monkeypatch):
    """La lista del lote se arma al principio; una orden descartada DESPUÉS
    —mientras el botón corre— no puede volver a PENDIENTE ni convertirse.
    Se simula el descarte concurrente justo antes de que el lote bloquee la
    fila, que es la ventana real."""
    from app.api.v1 import oc_recibidas as mod
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, proyecto="HOSPITALES", folio_externo="VH-39JUA-LUN")).json()
    _externo(client, h, "PROYECTO", "villahermosa:HOSPITALES", env["ehmo"])
    _externo(client, h, "UBICACION", "villahermosa:JUAN GRAHAM", env["ehmo"],
             sucursal_id=env["suc"])

    original = mod.get_or_404
    ya = []

    def descarta_antes_del_candado(db, model, obj_id, *a, **kw):
        # Una sola vez: sin el arreglo el lote vuelve a bloquear la fila al
        # convertirla, y un segundo UPDATE desde aquí esperaría ese candado
        # para siempre (la prueba se colgaría en vez de fallar).
        if (not ya and model is mod.OCRecibida and str(obj_id) == oc["id"]
                and kw.get("for_update")):
            ya.append(1)
            otra = SessionLocal()
            try:
                otra.execute(text("UPDATE oc_recibidas SET estado = 'DESCARTADA', "
                                  "motivo = 'basura' WHERE id = :id"), {"id": oc["id"]})
                otra.commit()
            finally:
                otra.close()
        return original(db, model, obj_id, *a, **kw)

    monkeypatch.setattr(mod, "get_or_404", descarta_antes_del_candado)
    r = client.post("/api/v1/oc-recibidas/procesar-pendientes", headers=h).json()
    monkeypatch.setattr(mod, "get_or_404", original)

    assert r["creadas"] == 0
    sigue = client.get(f"/api/v1/oc-recibidas/{oc['id']}", headers=h).json()
    assert sigue["estado"] == "DESCARTADA" and sigue["motivo"] == "basura"
    assert sigue["remision_id"] is None
    assert client.get("/api/v1/remisiones", headers=h).json()["total"] == 0
