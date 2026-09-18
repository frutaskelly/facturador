"""El catálogo de artículos de SAE, buscable, para un cliente.

Lo comparten la remisión (su aviso de «sin clave») y la captura, que todavía no
tiene remisión que consultar: las dos preguntan lo mismo —qué claves conoce la
empresa de SAE que le toca a ESTE cliente en ESTA plaza— y por eso vive aquí y
no colgado de una de las dos pantallas.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import ClaveSae, Cliente, Producto, ProductoCliente, Sucursal
from ..models.clave_sae import norm_clave
from ..schemas.remision import ClaveSaeEnUso, ClaveSaeSugerida, ClavesSaeOut
from .export_sae import _clave_para_remision, _claves_sae_de_clientes


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
    busca por descripción sobre el espejo que deposita el bot y se elige una que
    existe, en la empresa que le toca a esa plaza.

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

    # ¿Alguna ya es de otro producto de ESTE cliente? Dos productos con la misma
    # CVE_ART mandan a SAE la misma línea dos veces.
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
