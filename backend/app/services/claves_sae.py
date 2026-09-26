"""El catálogo de artículos de SAE: cómo se mantiene el espejo y cómo se busca.

DOS MITADES, y viven juntas porque hablan de la misma tabla (`claves_sae`):

  · MANTENERLO. `reemplazar_catalogo` es EL depósito: lo llama la ruta por la
    que el bot mandaba INVE (`POST /facturas/espejo/claves`) y lo llama el
    reloj del espejo del Facturador, que desde el 26-sep-2026 lee INVE él
    mismo (`sincronizar_catalogo`). Una sola lógica con sus candados, como el
    espejo de facturas reusa `factura_espejo`: dos copias se separan.
    `reflejar_escritura` es el atajo de cuando el Facturador ACABA de escribir
    en SAE: lo que SAE confirmó entra al espejo en ese momento, sin esperar la
    siguiente lectura de INVE.

  · BUSCARLO para un cliente. Lo comparten la remisión (su aviso de «sin
    clave») y la captura, que todavía no tiene remisión que consultar: las dos
    preguntan lo mismo —qué claves conoce la empresa de SAE que le toca a ESTE
    cliente en ESTA plaza— y por eso vive aquí y no colgado de una de las dos
    pantallas.
"""
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import ClaveSae, Cliente, Producto, ProductoCliente, Sucursal
from ..models.clave_sae import norm_clave
from ..schemas.clave_sae import ClaveSaeBuscadaOut, ClaveSaeEnEmpresa, ClavesSaeResult
from ..schemas.remision import ClaveSaeEnUso, ClaveSaeSugerida, ClavesSaeOut
from .export_sae import _clave_para_remision, _claves_sae_de_clientes


# ── Mantener el espejo ───────────────────────────────────────────────────────

class CatalogoVacio(ValueError):
    """No llegó ninguna clave utilizable: no se reemplaza nada."""


class CatalogoEncogido(ValueError):
    """El catálogo se encogió a menos de la mitad: parece una lectura cortada."""


def _campos(item: Any) -> tuple[str, Optional[str], bool]:
    """(clave, descripcion, activa) de un item del depósito, venga como schema
    (la ruta) o como diccionario (el reloj, que lee INVE directo)."""
    if isinstance(item, dict):
        clave, desc, activa = item.get("clave"), item.get("descripcion"), item.get("activa", True)
    else:
        clave = getattr(item, "clave", None)
        desc = getattr(item, "descripcion", None)
        activa = getattr(item, "activa", True)
    return norm_clave(clave), desc, bool(True if activa is None else activa)


def reemplazar_catalogo(db: Session, tenant_id, empresa: str, items: Iterable[Any],
                        forzar: bool = False) -> ClavesSaeResult:
    """Deja el catálogo de esa empresa IGUAL a lo que llegó.

    REEMPLAZA (es un espejo, no un acumulado): lo que ya no está en SAE deja de
    estar aquí. Dos candados, y ninguno se salta desde el reloj:

      · Nada utilizable → `CatalogoVacio`. Un catálogo vacío nunca es la
        verdad de SAE; es una lectura que falló.
      · Menos de la mitad de lo que había → `CatalogoEncogido`, salvo
        `forzar`. Un catálogo que se parte a la mitad es casi siempre una
        lectura cortada, no un inventario vaciado, y reemplazarlo convertiría
        cientos de claves buenas en «no existe en SAE» y trabaría exports
        legítimos. Forzar lo decide una persona, nunca la pasada automática.
    """
    empresa = str(empresa or "").strip()
    recibidas: dict[str, tuple[Optional[str], bool]] = {}
    for item in items or []:
        clave, desc, activa = _campos(item)
        if clave:
            recibidas[clave] = (desc, activa)
    if not recibidas:
        raise CatalogoVacio("No llegó ninguna clave utilizable")

    actuales = {
        c.clave: c for c in db.query(ClaveSae).filter(
            ClaveSae.tenant_id == tenant_id, ClaveSae.empresa == empresa
        ).with_for_update()
    }
    if actuales and len(recibidas) * 2 < len(actuales) and not forzar:
        raise CatalogoEncogido(
            f"Llegaron {len(recibidas)} claves para la empresa {empresa} y había "
            f"{len(actuales)}: parece una lectura incompleta de SAE. Si el catálogo "
            "de verdad encogió así, repite con forzar=true"
        )

    ahora = datetime.now(timezone.utc)
    nuevas: list[dict] = []
    actualizadas = 0
    for clave, (desc_cruda, activa) in recibidas.items():
        desc = (desc_cruda or "").strip()[:254] or None
        fila = actuales.get(clave)
        if fila is None:
            nuevas.append({
                "id": uuid4(), "tenant_id": tenant_id, "empresa": empresa,
                "clave": clave, "descripcion": desc, "activa": activa,
                "sincronizado_at": ahora,
            })
        elif fila.activa != activa or fila.descripcion != desc:
            fila.activa = activa
            fila.descripcion = desc
            actualizadas += 1

    sobrantes = [c for c in actuales if c not in recibidas]
    if sobrantes:
        db.query(ClaveSae).filter(
            ClaveSae.tenant_id == tenant_id,
            ClaveSae.empresa == empresa,
            ClaveSae.clave.in_(sobrantes),
        ).delete(synchronize_session=False)
    if nuevas:
        db.bulk_insert_mappings(ClaveSae, nuevas)
    # El sello de sincronización, en UN solo UPDATE. Ponerlo fila por fila
    # ensuciaba las ~2,000 de la empresa y el depósito tardaba más que el
    # timeout del conector (30 s): el espejo se quedaba días sin actualizar y
    # nadie se enteraba, porque el bot reportaba «FALLÓ el depósito» en su log
    # y el Facturador seguía enseñando el catálogo viejo (18-sep-2026).
    db.query(ClaveSae).filter(
        ClaveSae.tenant_id == tenant_id, ClaveSae.empresa == empresa,
    ).update({ClaveSae.sincronizado_at: ahora}, synchronize_session=False)
    db.flush()

    return ClavesSaeResult(
        empresa=empresa, recibidas=len(recibidas), creadas=len(nuevas),
        actualizadas=actualizadas, eliminadas=len(sobrantes), total=len(recibidas),
    )


def sincronizar_catalogo(db: Session, tenant_id, empresa: str) -> ClavesSaeResult:
    """Lee INVE<empresa> de SAE y reemplaza el espejo de esa empresa.

    Lo hacía el bot (`sync_claves_sae.py` por launchd, 7:30 y 15:30) y llegaba
    por HTTP; desde el 26-sep-2026 lo hace el reloj del espejo del Facturador.
    SAE caído LANZA antes de tocar nada: nunca se vacía el catálogo por una
    lectura fallida. Y el candado de «encoge a menos de la mitad» se respeta
    sin forzar: que lo decida una persona.
    """
    from . import sae_lectura   # perezoso: la lectura trae la configuración de SAE

    filas = sae_lectura.catalogo_inve(empresa)
    return reemplazar_catalogo(db, tenant_id, empresa, filas, forzar=False)


def reflejar_escritura(db: Session, tenant_id, empresa: str, clave: str,
                       descripcion: Optional[str] = None,
                       activa: Optional[bool] = None) -> None:
    """Lo que SAE ACABA de confirmar, directo al espejo de ESA empresa.

    Sin esto, el alta recién escrita no existía para el Facturador hasta la
    siguiente lectura de INVE (horas): otra alta de la misma clave pasaba el
    candado 409 y la búsqueda de claves decía «no existe» de algo que ya
    estaba en SAE (26-sep-2026).

    Upsert por (tenant, empresa, clave). `None` es «no lo sé» y NO pisa lo que
    el espejo ya tenía: una clave que ya existía y estaba de baja no se vuelve
    activa porque alguien pidió darla de alta. Si la fila no existía, nace
    activa: SAE acaba de decir que la clave está ahí, y la siguiente lectura
    de INVE corrige el STATUS si hiciera falta.
    """
    clave = norm_clave(clave)
    if not clave:
        return
    desc = (descripcion or "").strip()[:254] or None
    valores = {"id": uuid4(), "tenant_id": tenant_id, "empresa": str(empresa).strip(),
               "clave": clave, "descripcion": desc,
               "activa": True if activa is None else bool(activa),
               "sincronizado_at": datetime.now(timezone.utc)}
    cambia: dict[str, Any] = {}
    if desc is not None:
        cambia["descripcion"] = desc
    if activa is not None:
        cambia["activa"] = bool(activa)
    stmt = pg_insert(ClaveSae.__table__).values(**valores)
    if cambia:
        stmt = stmt.on_conflict_do_update(constraint="uq_clave_sae_tenant_empresa", set_=cambia)
    else:
        stmt = stmt.on_conflict_do_nothing(constraint="uq_clave_sae_tenant_empresa")
    db.execute(stmt)


# ── Buscar una clave en todas las empresas ──────────────────────────────────

def clave_de_busqueda(valor: Optional[str]) -> str:
    """Como se compara una clave buscada: MAYÚSCULAS y sin espacios. 'ajo kg'
    y 'AJOKG' son la misma pregunta."""
    return re.sub(r"\s+", "", str(valor or "")).upper()


def buscar_claves(db: Session, tenant_id, *, clave: Optional[str] = None,
                  q: Optional[str] = None, empresa: Optional[str] = None,
                  solo_activas: bool = False, limit: int = 20,
                  empresas: Optional[tuple] = None) -> list[ClaveSaeBuscadaOut]:
    """Las claves del espejo que casan, agrupadas: una fila por clave con todas
    las empresas donde existe y el producto del Facturador que la lleva.

    Sale de `claves_sae` (el espejo de INVE), NO de SAE en vivo: contesta en
    milisegundos y sin depender de la red del Mini. Lo que el Facturador acaba
    de escribir en SAE ya está aquí (`reflejar_escritura`).

    `clave` es exacta (normalizada); `q` busca texto en la clave y en la
    descripción. Con las dos, casa cualquiera de las dos, y la clave exacta va
    primero — si no hay `clave`, la exacta es `q` tomada como clave: buscar
    «AJOKG» pone AJOKG antes que AJOKGMORADO.

    El filtro por texto elige QUÉ claves salen; el mapa `empresas` trae todas
    las empresas donde esa clave existe (respetando `empresa` y
    `solo_activas`), aunque la descripción de alguna no contenga el texto: el
    bot las necesita todas para mandar un cambio completo.
    """
    exacta = clave_de_busqueda(clave) if clave else ""
    texto = (q or "").strip()
    llave = func.upper(func.btrim(ClaveSae.clave))
    sin_espacios = func.replace(llave, " ", "")

    base = db.query(ClaveSae).filter(ClaveSae.tenant_id == tenant_id)
    if empresa:
        base = base.filter(ClaveSae.empresa == empresa)
    if empresas is not None:
        base = base.filter(ClaveSae.empresa.in_(list(empresas)))
    if solo_activas:
        base = base.filter(ClaveSae.activa.is_(True))

    condiciones = []
    if exacta:
        condiciones.append(sin_espacios == exacta)
    if texto:
        like = f"%{texto}%"
        condiciones.append(or_(ClaveSae.clave.ilike(like), ClaveSae.descripcion.ilike(like)))
    if not condiciones:
        return []
    primero = exacta or clave_de_busqueda(texto)
    rango = func.min(case((sin_espacios == primero, 0), else_=1))
    llaves = [
        k for (k,) in base.filter(or_(*condiciones))
        .with_entities(llave)
        .group_by(llave)
        .order_by(rango, llave)
        .limit(limit)
        .all()
    ]
    if not llaves:
        return []

    por_llave: dict[str, dict[str, ClaveSaeEnEmpresa]] = {}
    for fila in base.filter(llave.in_(llaves)).order_by(ClaveSae.empresa).all():
        por_llave.setdefault(norm_clave(fila.clave), {})[fila.empresa] = ClaveSaeEnEmpresa(
            activa=bool(fila.activa), descripcion=fila.descripcion)

    # Una clave puede amparar VARIOS productos (23-sep-2026). El contrato pide
    # uno: el activo primero y, entre iguales, el primero por nombre — siempre
    # el mismo, para que dos búsquedas seguidas no contesten distinto.
    producto: dict[str, tuple] = {}
    llave_prod = func.upper(func.btrim(Producto.clave_sae))
    for pid, nombre, k in (
        db.query(Producto.id, Producto.nombre, llave_prod)
        .filter(Producto.tenant_id == tenant_id, Producto.deleted_at.is_(None),
                llave_prod.in_(llaves))
        .order_by(Producto.activo.desc(), Producto.nombre, Producto.id)
        .all()
    ):
        producto.setdefault(k, (pid, nombre))

    salida = []
    for k in llaves:
        empresas = por_llave.get(k, {})
        descripcion = next((e.descripcion for e in empresas.values()
                            if e.activa and e.descripcion), None) or next(
            (e.descripcion for e in empresas.values() if e.descripcion), None)
        pid, nombre = producto.get(k, (None, None))
        salida.append(ClaveSaeBuscadaOut(clave=k, descripcion=descripcion, empresas=empresas,
                                         producto_id=pid, producto_nombre=nombre))
    return salida


# ── Buscarlo para un cliente ─────────────────────────────────────────────────


def claves_que_ya_usa(
    db: Session, producto_id: Optional[UUID], empresa: Optional[str]
) -> list:
    """Las claves que ESE producto ya trae puestas: su clave base y las del
    catálogo de cualquier cliente o plaza.

    Va primero en el buscador porque casi nunca falta la clave — está guardada
    donde no ampara. El CILANTRO de EHMO tenía CILANTROKG amarrado a Tabasco y
    la remisión era de Pachuca; la clave existía, viva, en la empresa 02.
    """
    if producto_id is None:
        return []
    prod = db.query(Producto).filter(Producto.id == producto_id).one_or_none()
    if prod is None:
        return []
    usos: dict[str, str] = {}
    if (prod.clave_sae or "").strip():
        usos[norm_clave(prod.clave_sae)] = "clave del producto"
    filas = (
        db.query(ProductoCliente, Cliente.legal_name, Sucursal.nombre)
        .join(Cliente, Cliente.id == ProductoCliente.cliente_id)
        .outerjoin(Sucursal, Sucursal.id == ProductoCliente.sucursal_id)
        .filter(
            ProductoCliente.producto_id == producto_id,
            ProductoCliente.codigo_cliente.isnot(None),
        )
        .all()
    )
    for pc, cliente, plaza in filas:
        clave = norm_clave(pc.codigo_cliente)
        if not clave:
            continue
        de_donde = cliente + (f" en {plaza}" if plaza else "")
        usos[clave] = usos.get(clave) or de_donde
        if usos[clave] != "clave del producto" and usos[clave] != de_donde:
            usos[clave] = f"{usos[clave]} y otros"
    espejo = {}
    if empresa and usos:
        espejo = {
            norm_clave(c): (d, a)
            for c, d, a in db.query(ClaveSae.clave, ClaveSae.descripcion, ClaveSae.activa)
            .filter(ClaveSae.empresa == empresa, ClaveSae.clave.in_(list(usos)))
            .all()
        }
    return [
        ClaveSaeEnUso(
            clave=clave,
            descripcion=espejo.get(clave, (None, None))[0],
            activa=espejo.get(clave, (None, None))[1] if clave in espejo else None,
            de_donde=de_donde,
        )
        for clave, de_donde in sorted(usos.items())
    ]


def sugerencias_de_claves(
    db: Session,
    tenant_id: UUID,
    cliente_id: Optional[UUID],
    sucursal_id: Optional[UUID],
    q: str = "",
    producto_id: Optional[UUID] = None,
) -> ClavesSaeOut:
    """El catálogo de SAE buscable para (cliente, plaza), con lo que el producto
    ya usa por delante.

    Es la otra mitad del aviso «sin clave SAE»: cuando el producto de verdad es
    nuevo para el cliente hay que capturarle su clave, y teclearla de memoria es
    justo como salió la FRESADOMOPZ que SAE no conocía (14-sep-2026). Aquí se
    busca por descripción sobre el espejo de INVE (que desde el 26-sep-2026 lee
    el propio Facturador) y se elige una que existe, en la empresa que le toca
    a esa plaza.

    Sin espejo (o sin equivalencia SAE del cliente) contesta `espejo=False` con
    el motivo: la captura sigue siendo libre y la pantalla no promete nada —
    el mismo fail-open que el export.

    Sin cliente todavía (la captura recién abierta) contesta el motivo y lo que
    el producto ya usa, que es lo único que se puede saber sin saber a quién se
    le vende.
    """
    if cliente_id is None:
        return ClavesSaeOut(
            motivo="Elige el cliente para saber qué empresa de SAE le toca",
            ya_usa=claves_que_ya_usa(db, producto_id, None),
        )
    pares = _claves_sae_de_clientes(
        db, tenant_id, {cliente_id}
    ).get(cliente_id, [])
    if not pares:
        return ClavesSaeOut(
            motivo="Este cliente no tiene equivalencia con SAE todavía",
            ya_usa=claves_que_ya_usa(db, producto_id, None),
        )
    par, conflicto = _clave_para_remision(pares, sucursal_id)
    if par is None:
        return ClavesSaeOut(
            motivo=(
                "El cliente vive en las empresas SAE "
                + " y ".join(conflicto or [])
                + ", y la plaza de la remisión no decide cuál"
            ),
        )
    empresa = par[0]

    base = db.query(ClaveSae).filter(
        ClaveSae.tenant_id == tenant_id, ClaveSae.empresa == empresa
    )
    if not db.query(base.exists()).scalar():
        return ClavesSaeOut(
            empresa=empresa,
            motivo=f"No hay espejo del catálogo de la empresa {empresa}",
            ya_usa=claves_que_ya_usa(db, producto_id, empresa),
        )

    texto = (q or '').strip()
    filas_q = base
    if texto:
        like = f"%{texto}%"
        filas_q = filas_q.filter(
            or_(ClaveSae.clave.ilike(like), ClaveSae.descripcion.ilike(like))
        )
    # Las vivas primero: una clave de baja EXISTE pero no factura, y ofrecerla
    # arriba sería ofrecer el siguiente problema.
    filas = filas_q.order_by(ClaveSae.activa.desc(), ClaveSae.descripcion).limit(20).all()

    # ¿Alguna ya la usa otro producto de ESTE cliente? Se avisa, no se bloquea:
    # varios productos pueden compartir la misma CVE_ART.
    normalizadas = {norm_clave(f.clave) for f in filas}
    usadas: dict[str, tuple] = {}
    if normalizadas:
        for pc, nombre in (
            db.query(ProductoCliente, Producto.nombre)
            .join(Producto, Producto.id == ProductoCliente.producto_id)
            .filter(
                ProductoCliente.cliente_id == cliente_id,
                ProductoCliente.codigo_cliente.isnot(None),
            )
            .all()
        ):
            clave = norm_clave(pc.codigo_cliente)
            if clave in normalizadas:
                usadas.setdefault(clave, (pc.producto_id, nombre))

    return ClavesSaeOut(
        empresa=empresa,
        espejo=True,
        ya_usa=claves_que_ya_usa(db, producto_id, empresa),
        claves=[
            ClaveSaeSugerida(
                clave=f.clave,
                descripcion=f.descripcion,
                activa=bool(f.activa),
                producto_id=usadas.get(norm_clave(f.clave), (None, None))[0],
                producto_nombre=usadas.get(norm_clave(f.clave), (None, None))[1],
            )
            for f in filas
        ],
    )
