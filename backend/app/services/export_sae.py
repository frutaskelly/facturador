"""Export del Excel masivo para SAE — remisiones → archivo que Aspel importa.

Fase espejo de la migración (PLAN-migracion-master-facturador.md, Etapa 1.3):
mientras SAE siga siendo el sistema fiscal, el Facturador genera el archivo
que el operador importa a mano en Aspel (Ventas → Facturas/Pedidos →
Importación). El layout replica BYTE A BYTE el que hoy produce el bot
(cmd_massivo / cmd_massivo_factura en SmartSupply/bot/sheets_push.py) porque es
el que SAE ya demostró aceptar:

- FACTURA: 27 columnas, hoja "Facturas", una FILA = una PARTIDA, cabecera
  repetida por fila; SAE agrupa en un documento las filas que comparten
  FOLIO+CLIENTE+FECHA (regla oficial del manual Aspel §2.4). El FOLIO va
  pre-resuelto: serie + número alineado a la derecha en 10 posiciones
  ("ZHGO       233" = 14 chars) — el ancho NO es por serie, es serie+10.
- PEDIDO: 22 columnas, hoja "Pedidos", sin columnas CFDI; el FOLIO lleva la OC
  del cliente (su_pedido) y SAE asigna su propio consecutivo al importar.

Tres trampas que este módulo hereda resueltas del bot:

1. **FECHA en MM/DD/YYYY.** La PC donde se importa interpreta la columna con el
   regional de Windows (mes/día/año); con DD/MM entraron facturas con la fecha
   CAMBIADA en silencio (ZHGO 312, 324, 335, 365-369 — una timbrada así).
2. **La CLAVE es la del cliente** (producto_clientes.codigo_cliente = CVE_ART
   de SAE), nunca el SKU interno: SAE rechaza claves que no existen en INVE02.
3. **La Observación es la llave de conciliación**: "OC <su_pedido> …" es como
   el resto del sistema (bot, Master, estado de cuenta) casa la factura de SAE
   con su orden. Se arma con el MISMO formato.

El archivo se escribe con xlwt (.xls Excel 97-2003): es el formato de los
masivos que SAE ya importa hoy — no se innova en el formato del intercambio.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session, selectinload

from ..models import Cliente, ClienteExterno, ProductoCliente, Remision
from .series import resolver_serie

# Cabeceras EXACTAS de los masivos reales (verificadas contra los .xls de
# ~/Downloads y KnowHow_Massivos_SAE.md §1-2). Las COLxx van vacías pero DEBEN
# existir: el asistente de importación de SAE asocia por posición.
FACTURA_HDR = (
    ["FOLIO", "CLIENTE", "FECHA", "SU PEDIDO", "CLAVE", "CANTIDAD", "PREC", "COL82"]
    + [f"COL{i}" for i in range(9, 22)]
    + ["METODO PAGO", "FORMA PAGO SAT", "USO CFDI", "COL22", "COL23", "Observaciones"]
)
PEDIDO_HDR = (
    ["FOLIO", "CLIENTE", "FECHA", "SU PEDIDO", "CLAVE", "CANTIDAD", "PRECIO"]
    + [f"COL{i}" for i in range(1, 15)]
    + ["Observaciones"]
)
assert len(FACTURA_HDR) == 27 and len(PEDIDO_HDR) == 22


def folio_sae(serie: str, numero: int) -> str:
    """'ZHGO' + 233 → 'ZHGO       233' (serie + número a la derecha en 10).

    Es el relleno interno de CVE_DOC en SAE: ZHGO=14, ZMAFAN=16, ZEHMOHOS=18 —
    siempre len(serie)+10. Un padding distinto parte el documento al importar.
    """
    return f"{serie}{str(numero).rjust(10)}"


def fecha_sae(d: date) -> str:
    """MM/DD/YYYY — el regional de la PC de importación (trampa real, ver arriba)."""
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def _observacion(rem: Remision) -> str:
    """'OC <su_pedido> <resto de las notas>' — la llave de conciliación.

    Las notas de una remisión nacida de la bandeja ya traen el segmento
    'OC <folio>' adentro ('punto · OC 24736 · obs'); se quita para no decir
    'OC 24736 OC 24736' y el resto (punto de entrega, observaciones del
    documento) se conserva porque es lo que el negocio lee en SAE.
    """
    partes = [p.strip() for p in (rem.notas or "").split("·") if p.strip()]
    if rem.su_pedido:
        redundante = f"OC {rem.su_pedido}"
        partes = [p for p in partes if p.upper() != redundante.upper()]
        partes.insert(0, redundante)
    return " ".join(partes)[:250]


@dataclass
class DocExport:
    """Una remisión resuelta a documento SAE, lista para volverse filas."""
    remision: Remision
    cliente_sae: str          # número de cliente en SAE ("7")
    empresa: str              # empresa SAE ("02")
    serie: Optional[str] = None      # solo FACTURA
    folio: Optional[int] = None      # solo FACTURA
    filas: list[list] = field(default_factory=list)


@dataclass
class ResultadoPreview:
    ok: bool
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    empresa: Optional[str] = None
    # Por serie fiscal: cuántas remisiones foliarán con ella y el folio sugerido.
    series: list[dict] = field(default_factory=list)
    remisiones: int = 0


def _claves_sae_de_clientes(db: Session, tenant_id: UUID, cliente_ids: set) -> dict:
    """{cliente_id: (empresa, numero)} desde cliente_externos sistema='SAE'.

    Un cliente con claves en DOS empresas SAE (EHMO: 02:5 y 03:1) es ambiguo a
    este nivel: se reporta como error hasta que la Etapa 2 mapee empresa por
    sucursal. Balles/Jubran viven solo en la 02.
    """
    filas = (
        db.query(ClienteExterno)
        .filter(
            ClienteExterno.tenant_id == tenant_id,
            ClienteExterno.sistema == "SAE",
            ClienteExterno.cliente_id.in_(cliente_ids or [None]),
            ClienteExterno.confianza == "CONFIRMADA",
        )
        .all()
    )
    por_cliente: dict = {}
    for f in filas:
        m = re.match(r"^\s*(\d+)\s*[:. ]\s*(\d+)\s*$", f.clave or "")
        if not m:
            continue
        por_cliente.setdefault(f.cliente_id, []).append((m.group(1), m.group(2)))
    return por_cliente


def _codigos_cliente(db: Session, tenant_id: UUID, cliente_ids: set) -> dict:
    """{(cliente_id, producto_id): codigo_cliente} — la CVE_ART que SAE conoce."""
    filas = (
        db.query(ProductoCliente)
        .filter(
            ProductoCliente.tenant_id == tenant_id,
            ProductoCliente.cliente_id.in_(cliente_ids or [None]),
            ProductoCliente.codigo_cliente.isnot(None),
        )
        .all()
    )
    return {(f.cliente_id, f.producto_id): f.codigo_cliente for f in filas}


def _num(v) -> float:
    """Decimal → float para xlwt (SAE lee la celda numérica tal cual)."""
    return float(Decimal(str(v or 0)))


def preparar(
    db: Session,
    tenant_id: UUID,
    ids: list[UUID],
    tipo: str,
) -> tuple[ResultadoPreview, list[DocExport]]:
    """Valida y resuelve todo lo que el archivo necesita. NO escribe nada.

    Reglas de exclusión (se reportan, no se truncan en silencio):
    - CANCELADA nunca se exporta.
    - FACTURA: una remisión que YA tiene factura_sae no se re-exporta — re-subir
      un masivo a SAE crea un documento DUPLICADO con el siguiente consecutivo.
    - Sin clave SAE del cliente, sin serie fiscal o con partidas sin código de
      cliente: el lote entero se detiene. Un archivo a medias importado en SAE
      no se puede "completar" después sin duplicar.
    """
    res = ResultadoPreview(ok=False)
    tipo = (tipo or "").upper()
    if tipo not in ("FACTURA", "PEDIDO"):
        res.errores.append("tipo debe ser FACTURA o PEDIDO")
        return res, []
    if not ids:
        res.errores.append("sin remisiones seleccionadas")
        return res, []

    rems = (
        db.query(Remision)
        .options(selectinload(Remision.lineas))
        .filter(Remision.tenant_id == tenant_id, Remision.id.in_(ids), Remision.deleted_at.is_(None))
        .all()
    )
    encontradas = {r.id for r in rems}
    for i in ids:
        if i not in encontradas:
            res.errores.append(f"remisión {i} no existe")
    if res.errores:
        return res, []

    cliente_ids = {r.cliente_facturacion_id for r in rems}
    claves = _claves_sae_de_clientes(db, tenant_id, cliente_ids)
    codigos = _codigos_cliente(db, tenant_id, cliente_ids)
    nombres = dict(
        db.query(Cliente.id, Cliente.legal_name).filter(Cliente.id.in_(cliente_ids)).all()
    )

    docs: list[DocExport] = []
    empresas: set[str] = set()
    series_conteo: dict[str, int] = {}

    for rem in sorted(rems, key=lambda r: (r.fecha_remision, r.folio_interno)):
        nombre_cli = nombres.get(rem.cliente_facturacion_id, "?")
        if rem.estado == "CANCELADA":
            res.errores.append(f"{rem.folio_interno}: está CANCELADA")
            continue
        if tipo == "FACTURA" and rem.factura_sae:
            res.errores.append(
                f"{rem.folio_interno}: ya está amparada por la factura SAE "
                f"{rem.factura_sae} — re-exportarla duplicaría el documento en SAE"
            )
            continue
        pares = claves.get(rem.cliente_facturacion_id) or []
        if not pares:
            res.errores.append(
                f"{rem.folio_interno}: {nombre_cli} no tiene clave SAE en equivalencias "
                "(cliente_externos sistema=SAE, formato 'empresa:cliente')"
            )
            continue
        if len({p[0] for p in pares}) > 1:
            res.errores.append(
                f"{rem.folio_interno}: {nombre_cli} tiene clave en más de una empresa SAE "
                f"({', '.join(':'.join(p) for p in pares)}) — falta mapear empresa por sucursal"
            )
            continue
        empresa, numero = pares[0]
        empresas.add(empresa)

        sin_codigo = []
        for ln in rem.lineas:
            if (rem.cliente_facturacion_id, ln.producto_id) not in codigos:
                sin_codigo.append(str(ln.numero_linea))
        if sin_codigo:
            res.errores.append(
                f"{rem.folio_interno}: {len(sin_codigo)} partida(s) sin código del cliente "
                f"(líneas {', '.join(sin_codigo)}) — SAE rechaza claves que no están en su inventario"
            )
            continue
        if not rem.lineas:
            res.errores.append(f"{rem.folio_interno}: no tiene partidas")
            continue

        doc = DocExport(remision=rem, cliente_sae=numero, empresa=empresa)
        if tipo == "FACTURA":
            serie = resolver_serie(
                db, tenant_id, "FACTURA",
                cliente_id=rem.cliente_facturacion_id, sucursal_id=rem.sucursal_id,
            )
            if serie is None:
                res.errores.append(
                    f"{rem.folio_interno}: no se resuelve serie de FACTURA para {nombre_cli}"
                )
                continue
            doc.serie = serie.codigo
            series_conteo[serie.codigo] = series_conteo.get(serie.codigo, 0) + 1
        docs.append(doc)

    if len(empresas) > 1:
        res.errores.append(
            f"la selección mezcla empresas SAE ({', '.join(sorted(empresas))}): "
            "SAE importa por empresa — genera un archivo por cada una"
        )

    if res.errores:
        return res, []

    res.empresa = next(iter(empresas), None)
    res.remisiones = len(docs)
    if tipo == "FACTURA":
        for codigo, n in sorted(series_conteo.items()):
            res.series.append({
                "serie": codigo,
                "remisiones": n,
                "folio_sugerido": _folio_sugerido(db, tenant_id, codigo),
            })
    res.ok = True
    return res, docs


def _folio_sugerido(db: Session, tenant_id: UUID, serie: str) -> Optional[int]:
    """max(folio conocido de esa serie) + 1, leyendo AMBAS fuentes del espejo:
    remisiones.factura_sae ('ZHGO 331') y facturas espejo (serie/folio). Es un
    PRELLENADO: el operador confirma contra SAE (regla D1 del plan) — un folio
    adelantado o repetido hace fallar el import."""
    from ..models import Factura  # import local: evita ciclo en el arranque

    mayor = 0
    filas = (
        db.query(Remision.factura_sae)
        .filter(
            Remision.tenant_id == tenant_id,
            Remision.factura_sae.isnot(None),
            Remision.factura_sae.like(f"{serie}%"),
        )
        .all()
    )
    for (fs,) in filas:
        m = re.match(rf"^{re.escape(serie)}\s*0*(\d+)$", (fs or "").strip())
        if m:
            mayor = max(mayor, int(m.group(1)))
    tope = (
        db.query(Factura.folio)
        .filter(Factura.tenant_id == tenant_id, Factura.serie == serie)
        .order_by(Factura.folio.desc())
        .first()
    )
    if tope:
        mayor = max(mayor, int(tope[0]))
    return mayor + 1 if mayor else None


def generar(
    db: Session,
    tenant_id: UUID,
    ids: list[UUID],
    tipo: str,
    *,
    folios: Optional[dict[str, int]] = None,
    fecha: Optional[date] = None,
    estampar: bool = True,
) -> tuple[ResultadoPreview, Optional[bytes], Optional[str]]:
    """Genera el .xls y —solo FACTURA con estampar— deja el espejo puesto:
    cada remisión del lote queda con su `factura_sae` (→ RESERVADO), que es lo
    que evita el doble export y alimenta el folio sugerido del siguiente lote.

    `folios` = {serie: folio_inicial} confirmados por el operador (FACTURA).
    El commit lo hace el caller (endpoint): estampa y archivo viajan juntos o
    no viaja nada.
    """
    import xlwt

    tipo = (tipo or "").upper()
    res, docs = preparar(db, tenant_id, ids, tipo)
    if not res.ok:
        return res, None, None

    if tipo == "FACTURA":
        folios = folios or {}
        for s in res.series:
            if not folios.get(s["serie"]):
                res.ok = False
                res.errores.append(
                    f"falta el folio inicial de la serie {s['serie']} "
                    "(confírmalo contra SAE: un folio usado hace fallar el import)"
                )
        if not res.ok:
            return res, None, None
        contador = {s: int(n) for s, n in folios.items()}

    dia = fecha or date.today()
    f_txt = fecha_sae(dia)

    libro = xlwt.Workbook()
    hoja = libro.add_sheet("Facturas" if tipo == "FACTURA" else "Pedidos")
    hdr = FACTURA_HDR if tipo == "FACTURA" else PEDIDO_HDR
    for c, titulo in enumerate(hdr):
        hoja.write(0, c, titulo)

    codigos = _codigos_cliente(db, tenant_id, {d.remision.cliente_facturacion_id for d in docs})
    clientes = {
        c.id: c for c in db.query(Cliente).filter(
            Cliente.id.in_({d.remision.cliente_facturacion_id for d in docs})
        )
    }

    fila = 1
    for doc in docs:
        rem = doc.remision
        cli = clientes[rem.cliente_facturacion_id]
        obs = _observacion(rem)
        if tipo == "FACTURA":
            doc.folio = contador[doc.serie]
            contador[doc.serie] += 1
            folio_txt = folio_sae(doc.serie, doc.folio)
        else:
            # PEDIDO: va la OC del cliente; SAE asigna su consecutivo al importar.
            folio_txt = (rem.su_pedido or rem.folio_interno or "").strip()

        for ln in rem.lineas:
            clave = codigos[(rem.cliente_facturacion_id, ln.producto_id)]
            if tipo == "FACTURA":
                vals = [folio_txt, doc.cliente_sae, f_txt, "", clave,
                        _num(ln.cantidad_solicitada), _num(ln.precio_unitario), ""]
                vals += [""] * 13
                vals += [cli.metodo_pago_default or "PPD",
                         cli.forma_pago_default or "99",
                         cli.uso_cfdi_default or "G01", "", "", obs]
            else:
                vals = [folio_txt, doc.cliente_sae, f_txt, "", clave,
                        _num(ln.cantidad_solicitada), _num(ln.precio_unitario)]
                vals += [""] * 14
                vals += [obs]
            for c, v in enumerate(vals):
                hoja.write(fila, c, v)
            fila += 1

        if tipo == "FACTURA" and estampar:
            rem.factura_sae = f"{doc.serie} {doc.folio}"
            if rem.estado == "BORRADOR":
                rem.estado = "RESERVADO"

    buf = io.BytesIO()
    libro.save(buf)
    sufijo = dia.strftime("%Y-%m-%d")
    nombre = (
        f"{'FACTURA_massiva' if tipo == 'FACTURA' else 'PEDIDO_massivo'}"
        f"_SAE_emp{res.empresa}_{len(docs)}remisiones_{sufijo}.xls"
    )
    return res, buf.getvalue(), nombre
