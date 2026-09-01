"""Resolutor de precios (precios v2).

Devuelve el precio unitario para (cliente, sucursal, serie, proyecto, producto,
presentación, cantidad, fecha) aplicando prioridad MÁS-ESPECÍFICO-GANA (estándar
wholesale, no "precio más bajo"):

  1. Override de la sucursal + producto — el de (cliente, sucursal) exacto le
     gana al de la plaza sola (cliente NULL = para todos los que surte)
  2. Override del cliente + producto (sin plaza)
  3. Lista FORZADA a mano en el documento (`remisiones.lista_precios_id`)
  4. Lista asignada — el renglón de `lista_asignaciones` que coincide en las
     dimensiones más específicas (proyecto 8 · serie 4 · sucursal 2 · cliente 1)
  5. Lista base/default del tenant (`es_default`, o código UNICO, o la 1ª activa)

El paso 4 es el que sustituyó a "lista de la sucursal / lista del cliente": el
negocio también pacta por SERIE y por PROYECTO, y esas cuatro dimensiones se
combinan (EHMO en Pachuca bajo HOSPITALES es una negociación distinta de EHMO en
Pachuca a secas). Los pesos NO viven aquí: son la columna generada
`lista_asignaciones.especificidad`, para declararlos una sola vez.

Dentro de una lista se toma el tier cuyo `cantidad_minima` ≤ cantidad más alto
(así "compra más → mejor precio"). Todo filtrado por vigencia.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import (
    ListaAsignacion,
    ListaPrecios,
    Precio,
    PrecioOverride,
    Producto,
)


def _vigente(query, model, fecha):
    return query.filter(
        or_(model.vigencia_desde.is_(None), model.vigencia_desde <= fecha),
        or_(model.vigencia_hasta.is_(None), model.vigencia_hasta >= fecha),
    )


def _override(db, *, producto_id, presentacion, fecha, cliente_id=None, sucursal_id=None):
    q = db.query(PrecioOverride.precio_unitario).filter(
        PrecioOverride.producto_id == producto_id,
        PrecioOverride.presentacion == presentacion,
    )
    if sucursal_id:
        # Paso sucursal: los de esta plaza — el del cliente exacto o el de la
        # plaza para todos (cliente NULL). La plaza es compartida: uno anclado a
        # OTRO cliente no debe hablar aquí.
        q = q.filter(
            PrecioOverride.sucursal_id == sucursal_id,
            or_(PrecioOverride.cliente_id.is_(None), PrecioOverride.cliente_id == cliente_id),
        )
        orden_especifico = [PrecioOverride.cliente_id.is_(None).asc()]
    else:
        # Paso cliente: solo los SIN plaza — uno de (cliente, otra plaza) no
        # aplica a un documento que no está en ella.
        q = q.filter(
            PrecioOverride.cliente_id == cliente_id,
            PrecioOverride.sucursal_id.is_(None),
        )
        orden_especifico = []
    # id como desempate: dos overrides capturados en la misma transacción traen
    # el MISMO created_at (func.now() es por transacción) y sin llave secundaria
    # el ganador dependía del plan de la consulta — aquí y en el lote debe ser
    # el mismo.
    q = _vigente(q, PrecioOverride, fecha).order_by(
        *orden_especifico, PrecioOverride.created_at.desc(), PrecioOverride.id.desc()
    )
    row = q.first()
    return row[0] if row else None


def _precio_lista(db, lista_id, producto_id, presentacion, cantidad, fecha):
    q = db.query(Precio.precio_unitario).filter(
        Precio.lista_id == lista_id,
        Precio.producto_id == producto_id,
        Precio.presentacion == presentacion,
        Precio.cantidad_minima <= cantidad,
    )
    q = _vigente(q, Precio, fecha).order_by(Precio.cantidad_minima.desc())
    row = q.first()
    return row[0] if row else None


def resolver_asignacion(
    db: Session,
    *,
    cliente_id: Optional[UUID] = None,
    sucursal_id: Optional[UUID] = None,
    serie_id: Optional[UUID] = None,
    proyecto_id: Optional[UUID] = None,
    fecha: Optional[date] = None,
) -> Optional[ListaAsignacion]:
    """El renglón de `lista_asignaciones` que aplica, o None.

    Una dimensión en NULL es comodín: coincide con lo que sea. Una dimensión
    llena sólo coincide con ese valor exacto — por eso un renglón de sucursal
    NO aplica a un documento sin sucursal, que es justo lo que se quiere.
    """
    fecha = fecha or date.today()
    q = db.query(ListaAsignacion).filter(
        or_(ListaAsignacion.cliente_id.is_(None), ListaAsignacion.cliente_id == cliente_id),
        or_(ListaAsignacion.sucursal_id.is_(None), ListaAsignacion.sucursal_id == sucursal_id),
        or_(ListaAsignacion.serie_id.is_(None), ListaAsignacion.serie_id == serie_id),
        or_(ListaAsignacion.proyecto_id.is_(None), ListaAsignacion.proyecto_id == proyecto_id),
    )
    q = _vigente(q, ListaAsignacion, fecha)
    # A igual especificidad gana la más reciente: es la última negociación.
    return q.order_by(
        ListaAsignacion.especificidad.desc(), ListaAsignacion.created_at.desc(),
        ListaAsignacion.id.desc(),
    ).first()


def resolver_asignaciones(
    db: Session,
    *,
    cliente_id: Optional[UUID] = None,
    sucursal_id: Optional[UUID] = None,
    serie_id: Optional[UUID] = None,
    proyecto_id: Optional[UUID] = None,
    fecha: Optional[date] = None,
) -> list[ListaAsignacion]:
    """TODOS los renglones que aplican, del más al menos específico.

    Existe porque las listas negociadas pueden ser PARCIALES: la del proyecto
    DIF trae solo lo pactado para DIF. Si el producto no está ahí, el precio
    correcto es el de la siguiente negociación que aplique (la lista del
    cliente), no la lista base del negocio — que es menos específica que
    cualquier renglón coincidente.
    """
    fecha = fecha or date.today()
    q = db.query(ListaAsignacion).filter(
        or_(ListaAsignacion.cliente_id.is_(None), ListaAsignacion.cliente_id == cliente_id),
        or_(ListaAsignacion.sucursal_id.is_(None), ListaAsignacion.sucursal_id == sucursal_id),
        or_(ListaAsignacion.serie_id.is_(None), ListaAsignacion.serie_id == serie_id),
        or_(ListaAsignacion.proyecto_id.is_(None), ListaAsignacion.proyecto_id == proyecto_id),
    )
    q = _vigente(q, ListaAsignacion, fecha)
    return q.order_by(
        ListaAsignacion.especificidad.desc(), ListaAsignacion.created_at.desc(),
        ListaAsignacion.id.desc(),
    ).all()


def listas_asignadas_a_cliente(db: Session, cliente_id: UUID, fecha: Optional[date] = None) -> set[UUID]:
    """Las listas negociadas CON ese cliente, por cualquier dimensión.

    En los datos reales toda negociación lleva cliente_id (a veces además
    sucursal o proyecto), así que basta ese filtro. Vacío = el cliente compra
    a lista base (sin negociación propia).
    """
    q = db.query(ListaAsignacion.lista_id).filter(ListaAsignacion.cliente_id == cliente_id)
    q = _vigente(q, ListaAsignacion, fecha or date.today())
    return {lid for (lid,) in q.all()}


def origen_de(a: ListaAsignacion) -> str:
    """Cómo se le llama en pantalla al renglón que ganó: por su dimensión más
    específica, que es la que el vendedor reconoce ("es el precio del proyecto")."""
    if a.proyecto_id is not None:
        return "lista_proyecto"
    if a.serie_id is not None:
        return "lista_serie"
    if a.sucursal_id is not None:
        return "lista_sucursal"
    return "lista_cliente"


def _lista_default(db):
    # 1) la marcada explícitamente como default (wizard de importación /
    # administración de listas); 2) la convención histórica codigo='UNICO';
    # 3) la más vieja activa.
    marcada = (
        db.query(ListaPrecios)
        .filter(ListaPrecios.es_default.is_(True), ListaPrecios.deleted_at.is_(None))
        .order_by(ListaPrecios.created_at.asc())
        .first()
    )
    if marcada is not None:
        return marcada
    base = (
        db.query(ListaPrecios)
        .filter(ListaPrecios.codigo == "UNICO", ListaPrecios.deleted_at.is_(None))
        .one_or_none()
    )
    if base is None:
        base = (
            db.query(ListaPrecios)
            .filter(ListaPrecios.status == "ACTIVO", ListaPrecios.deleted_at.is_(None))
            .order_by(ListaPrecios.created_at.asc())
            .first()
        )
    return base


def _factor(v) -> Decimal:
    """Factor de una presentación: soporta forma simple (número) o rica ({factor})."""
    if isinstance(v, dict):
        return Decimal(str(v.get("factor", 1)))
    return Decimal(str(v))


def resolver_precio(
    db: Session,
    *,
    producto_id: UUID,
    presentacion: str = "KILO",
    cantidad: Decimal = Decimal("1"),
    cliente_id: Optional[UUID] = None,
    sucursal_id: Optional[UUID] = None,
    serie_id: Optional[UUID] = None,
    proyecto_id: Optional[UUID] = None,
    lista_id: Optional[UUID] = None,
    fecha: Optional[date] = None,
) -> Optional[dict]:
    """Precio resuelto + origen, o None si no hay ninguna regla aplicable.

    Si la presentación pedida no tiene precio propio, se deriva del precio de la
    unidad base × el factor de la presentación (p. ej. CAJA = PIEZA × 12). La
    cantidad se convierte a unidades base para elegir el tramo correcto.
    """
    fecha = fecha or date.today()
    cantidad = Decimal(cantidad)

    # Intentos de presentación: exacta primero; si falla, la unidad base × factor.
    # Cada intento es (presentacion, multiplicador_del_precio, cantidad_para_el_tramo).
    intentos: list[tuple[str, Decimal, Decimal]] = [(presentacion, Decimal(1), cantidad)]
    prod = (
        db.query(Producto)
        .filter(Producto.id == producto_id, Producto.deleted_at.is_(None))
        .one_or_none()
    )
    if prod:
        pres = prod.presentaciones or {}
        base = prod.unidad_base or prod.presentacion_default
        if base and base != presentacion and presentacion in pres and base in pres:
            ratio = _factor(pres[presentacion]) / _factor(pres[base])
            if ratio > 0:
                intentos.append((base, ratio, cantidad * ratio))

    def _resolver(src) -> Optional[Decimal]:
        """src(presentacion, cantidad) -> precio_unitario | None, aplicando intentos."""
        for pres_try, mult, cant_try in intentos:
            p = src(pres_try, cant_try)
            if p is not None:
                return p if mult == 1 else (p * mult).quantize(Decimal("0.01"))
        return None

    # 1. override sucursal (con el cliente: el exacto gana al de la plaza sola)
    if sucursal_id:
        p = _resolver(lambda pr, _c: _override(db, sucursal_id=sucursal_id, cliente_id=cliente_id, producto_id=producto_id, presentacion=pr, fecha=fecha))
        if p is not None:
            return {"precio": p, "origen": "override_sucursal"}
    # 2. override cliente
    if cliente_id:
        p = _resolver(lambda pr, _c: _override(db, cliente_id=cliente_id, producto_id=producto_id, presentacion=pr, fecha=fecha))
        if p is not None:
            return {"precio": p, "origen": "override_cliente"}
    # 3. lista forzada a mano en el documento
    if lista_id:
        p = _resolver(lambda pr, cant: _precio_lista(db, lista_id, producto_id, pr, cant, fecha))
        if p is not None:
            return {"precio": p, "origen": "lista_forzada", "lista_id": str(lista_id)}
    # 4. las asignaciones que coinciden, en CASCADA de especificidad: una lista
    #    negociada puede ser parcial (la del proyecto trae solo lo pactado); si
    #    no trae el producto, aplica la siguiente negociación — no la lista base.
    for asignacion in resolver_asignaciones(
        db, cliente_id=cliente_id, sucursal_id=sucursal_id,
        serie_id=serie_id, proyecto_id=proyecto_id, fecha=fecha,
    ):
        p = _resolver(lambda pr, cant, _l=asignacion.lista_id: _precio_lista(db, _l, producto_id, pr, cant, fecha))
        if p is not None:
            return {
                "precio": p,
                "origen": origen_de(asignacion),
                "lista_id": str(asignacion.lista_id),
                "asignacion_id": str(asignacion.id),
            }
    # 5. lista base/default
    base_lp = _lista_default(db)
    if base_lp is not None:
        p = _resolver(lambda pr, cant: _precio_lista(db, base_lp.id, producto_id, pr, cant, fecha))
        if p is not None:
            return {"precio": p, "origen": "lista_base", "lista_id": str(base_lp.id)}
    return None


def resolver_precios_lote(
    db: Session,
    *,
    items: list[dict],
    cliente_id: Optional[UUID] = None,
    sucursal_id: Optional[UUID] = None,
    serie_id: Optional[UUID] = None,
    proyecto_id: Optional[UUID] = None,
    fecha: Optional[date] = None,
) -> list[Optional[dict]]:
    """`resolver_precio` para N partidas con un puñado de consultas, no ~6×N.

    Nació de la bandeja: abrir una orden de 25 partidas contra la BD remota
    tardaba ~27 s porque cada partida recorría su propia cascada de consultas.
    Aquí se trae TODO lo que la cascada puede necesitar (overrides, asignaciones,
    precios de esas listas, la lista base) en una consulta por tabla, y la
    cascada corre en memoria con las mismas reglas y el mismo orden.

    `items` es una lista de {producto_id, presentacion, cantidad}; devuelve una
    lista paralela con el mismo dict que daría `resolver_precio` (o None). No
    contempla la lista forzada del documento (paso 3), que es por-remisión.
    La paridad con `resolver_precio` la vigila un test que corre ambos sobre la
    misma matriz de escenarios.
    """
    if not items:
        return []
    fecha = fecha or date.today()

    producto_ids = {it["producto_id"] for it in items}
    prods = {
        p.id: p
        for p in db.query(Producto)
        .filter(Producto.id.in_(producto_ids), Producto.deleted_at.is_(None))
        .all()
    }

    # Los intentos de cada partida: la presentación pedida y, si el producto la
    # traduce, la unidad base × factor — mismos criterios que resolver_precio.
    intentos_por_item: list[list[tuple[str, Decimal, Decimal]]] = []
    for it in items:
        cantidad = Decimal(it["cantidad"])
        presentacion = it["presentacion"]
        intentos: list[tuple[str, Decimal, Decimal]] = [(presentacion, Decimal(1), cantidad)]
        prod = prods.get(it["producto_id"])
        if prod:
            pres = prod.presentaciones or {}
            base = prod.unidad_base or prod.presentacion_default
            if base and base != presentacion and presentacion in pres and base in pres:
                ratio = _factor(pres[presentacion]) / _factor(pres[base])
                if ratio > 0:
                    intentos.append((base, ratio, cantidad * ratio))
        intentos_por_item.append(intentos)

    # Overrides de sucursal y de cliente: una consulta por dimensión, indexada
    # por (producto, presentación); el orden created_at desc hace que el primero
    # visto sea el que resolver_precio elegiría (el más reciente).
    def _overrides(*filtros, orden=()) -> dict[tuple, Decimal]:
        q = db.query(
            PrecioOverride.producto_id, PrecioOverride.presentacion, PrecioOverride.precio_unitario
        ).filter(PrecioOverride.producto_id.in_(producto_ids), *filtros)
        q = _vigente(q, PrecioOverride, fecha).order_by(
            *orden, PrecioOverride.created_at.desc(), PrecioOverride.id.desc()
        )
        out: dict[tuple, Decimal] = {}
        for pid, pres, precio in q.all():
            out.setdefault((pid, pres), precio)
        return out

    # Mismos filtros y orden que _override: el de (cliente, plaza) exacto gana
    # al de la plaza sola; el paso cliente solo ve los SIN plaza.
    ov_sucursal = _overrides(
        PrecioOverride.sucursal_id == sucursal_id,
        or_(PrecioOverride.cliente_id.is_(None), PrecioOverride.cliente_id == cliente_id),
        orden=[PrecioOverride.cliente_id.is_(None).asc()],
    ) if sucursal_id else {}
    ov_cliente = _overrides(
        PrecioOverride.cliente_id == cliente_id,
        PrecioOverride.sucursal_id.is_(None),
    ) if cliente_id else {}

    asignaciones = resolver_asignaciones(
        db, cliente_id=cliente_id, sucursal_id=sucursal_id,
        serie_id=serie_id, proyecto_id=proyecto_id, fecha=fecha,
    )
    base_lp = _lista_default(db)
    lista_ids = [a.lista_id for a in asignaciones]
    if base_lp is not None and base_lp.id not in lista_ids:
        lista_ids.append(base_lp.id)

    # Precios de TODAS esas listas para TODOS los productos, con sus tramos
    # ordenados como los ordenaría _precio_lista (cantidad_minima desc).
    tramos: dict[tuple, list[tuple[int, Decimal]]] = {}
    if lista_ids:
        q = db.query(
            Precio.lista_id, Precio.producto_id, Precio.presentacion,
            Precio.cantidad_minima, Precio.precio_unitario,
        ).filter(Precio.lista_id.in_(lista_ids), Precio.producto_id.in_(producto_ids))
        q = _vigente(q, Precio, fecha)
        for lid, pid, pres, cmin, precio in q.all():
            tramos.setdefault((lid, pid, pres), []).append((cmin, precio))
        for lst in tramos.values():
            lst.sort(key=lambda t: t[0], reverse=True)

    def _precio_en(lista_id, pid, pres, cantidad) -> Optional[Decimal]:
        for cmin, precio in tramos.get((lista_id, pid, pres), ()):
            if cmin <= cantidad:
                return precio
        return None

    resultados: list[Optional[dict]] = []
    for it, intentos in zip(items, intentos_por_item):
        pid = it["producto_id"]

        def _resolver(src) -> Optional[Decimal]:
            for pres_try, mult, cant_try in intentos:
                p = src(pres_try, cant_try)
                if p is not None:
                    return p if mult == 1 else (p * mult).quantize(Decimal("0.01"))
            return None

        res: Optional[dict] = None
        if sucursal_id:
            p = _resolver(lambda pr, _c: ov_sucursal.get((pid, pr)))
            if p is not None:
                res = {"precio": p, "origen": "override_sucursal"}
        if res is None and cliente_id:
            p = _resolver(lambda pr, _c: ov_cliente.get((pid, pr)))
            if p is not None:
                res = {"precio": p, "origen": "override_cliente"}
        if res is None:
            for asignacion in asignaciones:
                p = _resolver(lambda pr, cant, _l=asignacion.lista_id: _precio_en(_l, pid, pr, cant))
                if p is not None:
                    res = {
                        "precio": p,
                        "origen": origen_de(asignacion),
                        "lista_id": str(asignacion.lista_id),
                        "asignacion_id": str(asignacion.id),
                    }
                    break
        if res is None and base_lp is not None:
            p = _resolver(lambda pr, cant: _precio_en(base_lp.id, pid, pr, cant))
            if p is not None:
                res = {"precio": p, "origen": "lista_base", "lista_id": str(base_lp.id)}
        resultados.append(res)
    return resultados
