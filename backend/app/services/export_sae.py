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

from ..models import Cliente, ClienteExterno, Factura, ProductoCliente, Remision
from .series import resolver_serie

# La marca del espejo ("ZHGO 233") también se captura a mano (PATCH de la
# remisión), así que el cruce tolera espacios y ceros — el mismo criterio en
# todos los que la leen (_folio_sugerido, la colisión de folios y el espejo).
_RE_MARCA = re.compile(r"^([A-Z]+)\s*0*(\d+)$")


def parsear_marca(v: str) -> Optional[tuple[str, int]]:
    """'ZHGO 0233' / 'zhgo233' → ('ZHGO', 233); None si no parece una marca."""
    m = _RE_MARCA.match((v or "").strip().upper())
    return (m.group(1), int(m.group(2))) if m else None

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
    """{cliente_id: [(empresa, numero, sucursal_id)]} de cliente_externos SAE.

    Un cliente puede tener clave en DOS empresas SAE (EHMO: 02:5 Pachuca y
    03:1 Villahermosa): la fila de la equivalencia lleva `sucursal_id` para
    decidir por la sucursal de la REMISIÓN. Sin mapeo por sucursal y con más
    de una empresa, el lote se detiene (adivinar mandaría el documento a la
    empresa SAE equivocada). Balles/Jubrán/MAFAN viven solo en la 02.
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
        por_cliente.setdefault(f.cliente_id, []).append((m.group(1), m.group(2), f.sucursal_id))
    return por_cliente


def _clave_para_remision(pares: list, sucursal_id) -> tuple:
    """Elige la clave SAE para UNA remisión: la de SU sucursal gana; si no hay,
    caen las genéricas (sin sucursal). Devuelve (empresa, numero) o la lista
    de empresas en conflicto."""
    exactas = [p for p in pares if p[2] is not None and p[2] == sucursal_id]
    candidatas = exactas or [p for p in pares if p[2] is None] or pares
    distintas = {(p[0], p[1]) for p in candidatas}
    if len(distintas) > 1:
        # Dos claves distintas y nada que decida (ni la sucursal): no se adivina.
        return None, sorted({p[0] for p in candidatas})
    return next(iter(distintas)), None


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
    *,
    bloquear: bool = False,
) -> tuple[ResultadoPreview, list[DocExport]]:
    """Valida y resuelve todo lo que el archivo necesita. NO escribe nada.

    Reglas de exclusión (se reportan, no se truncan en silencio):
    - CANCELADA nunca se exporta.
    - Una remisión YA FACTURADA NATIVAMENTE (factura del Facturador viva) jamás
      entra al archivo: importarla en SAE emitiría un SEGUNDO CFDI real por la
      misma venta — la regla que este sistema existe para impedir.
    - FACTURA: una remisión con `factura_sae` (el espejo confirmó su factura, o
      alguien capturó la marca a mano) no se re-exporta — re-subir un masivo a
      SAE crea un documento DUPLICADO con el siguiente consecutivo.
    - Sin clave SAE del cliente, sin serie fiscal o con partidas sin código de
      cliente: el lote entero se detiene. Un archivo a medias importado en SAE
      no se puede "completar" después sin duplicar.

    El export NO estampa folios: el folio del archivo es una PROPUESTA y la
    verdad la pone el espejo cuando la factura existe en SAE (regla del dueño,
    29-ago-2026: "no asignar series sin que el SAE lo confirme" — pasó con un
    archivo que nunca se subió y quedó ZHGO 588 fantasma). Un export previo
    sin factura confirmada se reporta como AVISO, no bloquea.

    `bloquear=True` (lo usa generar): FOR UPDATE sobre las remisiones, para que
    dos exports simultáneos del mismo lote se serialicen y el segundo vea el
    `export_sae_at` del primero en su aviso.
    """
    res = ResultadoPreview(ok=False)
    tipo = (tipo or "").upper()
    if tipo not in ("FACTURA", "PEDIDO"):
        res.errores.append("tipo debe ser FACTURA o PEDIDO")
        return res, []
    if not ids:
        res.errores.append("sin remisiones seleccionadas")
        return res, []

    q = (
        db.query(Remision)
        .options(selectinload(Remision.lineas))
        .filter(Remision.tenant_id == tenant_id, Remision.id.in_(ids), Remision.deleted_at.is_(None))
    )
    if bloquear:
        # FOR UPDATE no admite selectinload de colección en la misma query; el
        # lock va sobre las remisiones y las líneas se cargan aparte.
        q = db.query(Remision).filter(
            Remision.tenant_id == tenant_id, Remision.id.in_(ids), Remision.deleted_at.is_(None)
        ).with_for_update()
    rems = q.all()
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
    # Facturas NATIVAS vivas ligadas a estas remisiones (el candado crítico).
    fact_ids = {r.factura_id for r in rems if r.factura_id}
    nativas_vivas = {
        f.id: f for f in db.query(Factura).filter(
            Factura.id.in_(fact_ids or [None]),
            Factura.origen != "ESPEJO_SAE",
            Factura.estado != "CANCELADA",
            Factura.deleted_at.is_(None),
        )
    } if fact_ids else {}

    docs: list[DocExport] = []
    empresas: set[str] = set()
    series_conteo: dict[str, int] = {}

    for rem in sorted(rems, key=lambda r: (r.fecha_remision, r.folio_interno)):
        nombre_cli = nombres.get(rem.cliente_facturacion_id, "?")
        if rem.estado == "CANCELADA":
            res.errores.append(f"{rem.folio_interno}: está CANCELADA")
            continue
        nativa = nativas_vivas.get(rem.factura_id)
        if nativa is not None:
            res.errores.append(
                f"{rem.folio_interno}: ya tiene la factura NATIVA {nativa.serie}{nativa.folio} "
                f"({nativa.estado}) — exportarla a SAE emitiría un SEGUNDO CFDI por la misma venta"
            )
            continue
        if tipo == "FACTURA" and rem.factura_sae:
            res.errores.append(
                f"{rem.folio_interno}: ya está amparada por la factura SAE "
                f"{rem.factura_sae} — re-exportarla duplicaría el documento en SAE"
            )
            continue
        if tipo == "FACTURA" and not getattr(
            db.query(Cliente).filter(Cliente.id == rem.cliente_facturacion_id).one(),
            "espejo_sae", False,
        ):
            # Sin espejo, la factura de SAE JAMÁS regresará a amparar la
            # remisión: quedaría exportada y facturable nativa a la vez (dos
            # CFDI). Se activa el candado del cliente y se re-exporta.
            res.errores.append(
                f"{rem.folio_interno}: {nombre_cli} no está en espejo SAE — actívalo en "
                "Clientes para que su factura se refleje y ampare la remisión"
            )
            continue
        if tipo == "FACTURA" and rem.export_sae_at is not None:
            # No bloquea: el archivo anterior pudo no subirse nunca (caso real).
            # Pero si SÍ se importó, re-exportar duplica — el operador decide.
            propuesto = f" (folio propuesto {rem.export_sae_folio})" if rem.export_sae_folio else ""
            res.avisos.append(
                f"{rem.folio_interno}: ya salió en un archivo el "
                f"{rem.export_sae_at:%d-%b %H:%M}{propuesto} y el espejo aún no confirma "
                "su factura — si aquel archivo SÍ se importó en SAE, no la re-exportes"
            )
        pares = claves.get(rem.cliente_facturacion_id) or []
        if not pares:
            res.errores.append(
                f"{rem.folio_interno}: {nombre_cli} no tiene clave SAE en equivalencias "
                "(cliente_externos sistema=SAE, formato 'empresa:cliente')"
            )
            continue
        elegida, conflicto = _clave_para_remision(pares, rem.sucursal_id)
        if elegida is None:
            res.errores.append(
                f"{rem.folio_interno}: {nombre_cli} tiene clave en más de una empresa SAE "
                f"({', '.join(conflicto)}) y su sucursal no decide — asigna la sucursal a "
                "cada equivalencia SAE en el cliente (empresa por sucursal)"
            )
            continue
        empresa, numero = elegida
        empresas.add(empresa)

        # Una devolución total deja la línea viva con cantidad 0: SAE importaría
        # una partida en cero cuyo CFDI el PAC rechaza. Se exportan solo las vivas.
        vivas = [ln for ln in rem.lineas if Decimal(str(ln.cantidad_solicitada or 0)) > 0]
        sin_codigo = []
        for ln in vivas:
            if (rem.cliente_facturacion_id, ln.producto_id) not in codigos:
                sin_codigo.append(str(ln.numero_linea))
        if sin_codigo:
            res.errores.append(
                f"{rem.folio_interno}: {len(sin_codigo)} partida(s) sin código del cliente "
                f"(líneas {', '.join(sin_codigo)}) — SAE rechaza claves que no están en su inventario"
            )
            continue
        if not vivas:
            res.errores.append(f"{rem.folio_interno}: no tiene partidas con cantidad")
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
            # Normalizada: la estampa y el espejo comparan en mayúsculas — un
            # código 'Zhgo ' con espacio dejaría marcas que nunca casan.
            doc.serie = (serie.codigo or "").strip().upper()
            series_conteo[doc.serie] = series_conteo.get(doc.serie, 0) + 1
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
    """max(folio conocido de esa serie) + 1. Lee lo CONFIRMADO (marcas
    factura_sae y facturas) y además los folios PROPUESTOS por exports de los
    últimos 7 días aún sin confirmar — dos lotes seguidos no proponen el mismo
    rango. Es un PRELLENADO: el operador confirma contra SAE (regla D1)."""
    from datetime import datetime, timedelta, timezone

    folios = _folios_ocupados(db, tenant_id, serie) | _folios_propuestos(db, tenant_id, serie)
    return max(folios) + 1 if folios else None


def _folios_propuestos(db: Session, tenant_id: UUID, serie: str) -> set[int]:
    """Folios que salieron PROPUESTOS en archivos de los últimos 7 días y cuya
    factura el espejo aún no confirma. No son reservas (el archivo pudo no
    subirse): alimentan el folio sugerido y un AVISO de posible colisión."""
    from datetime import datetime, timedelta, timezone

    corte = datetime.now(timezone.utc) - timedelta(days=7)
    out: set[int] = set()
    filas = (
        db.query(Remision.export_sae_folio)
        .filter(
            Remision.tenant_id == tenant_id,
            Remision.export_sae_folio.isnot(None),
            Remision.export_sae_at >= corte,
            Remision.factura_sae.is_(None),
            Remision.deleted_at.is_(None),
        )
        .all()
    )
    for (fs,) in filas:
        marca = parsear_marca(fs or "")
        if marca and marca[0] == serie:
            out.add(marca[1])
    return out


def _folios_ocupados(db: Session, tenant_id: UUID, serie: str) -> set[int]:
    """Todos los folios de esa serie que YA existen — en remisiones estampadas
    (factura_sae, texto libre tolerado) y en facturas (nativas o espejo). Es la
    base del folio sugerido Y del candado de colisión al estampar."""
    ocupados: set[int] = set()
    filas = (
        db.query(Remision.factura_sae)
        .filter(
            Remision.tenant_id == tenant_id,
            Remision.factura_sae.isnot(None),
            Remision.factura_sae.ilike(f"{serie}%"),
            Remision.deleted_at.is_(None),
        )
        .all()
    )
    for (fs,) in filas:
        marca = parsear_marca(fs or "")
        if marca and marca[0] == serie:
            ocupados.add(marca[1])
    for (folio,) in (
        db.query(Factura.folio)
        .filter(
            Factura.tenant_id == tenant_id,
            Factura.serie == serie,
            Factura.deleted_at.is_(None),
        )
        .all()
    ):
        ocupados.add(int(folio))
    return ocupados


def generar(
    db: Session,
    tenant_id: UUID,
    ids: list[UUID],
    tipo: str,
    *,
    folios: Optional[dict[str, int]] = None,
    fecha: Optional[date] = None,
) -> tuple[ResultadoPreview, Optional[bytes], Optional[str]]:
    """Genera el .xls (Excel 97-2004, el único formato que SAE importa).

    NO estampa folios en las remisiones: el folio del archivo es la PROPUESTA
    que el operador confirmó contra SAE, y `factura_sae` lo pone el ESPEJO
    cuando la factura de verdad existe (o una captura manual). Solo se marca
    `export_sae_at` como rastro para avisar de un doble export.

    `folios` = {serie: folio_inicial} confirmados por el operador (FACTURA).
    El commit lo hace el caller (endpoint).
    """
    import xlwt
    from datetime import datetime, timezone

    tipo = (tipo or "").upper()
    res, docs = preparar(db, tenant_id, ids, tipo, bloquear=True)
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
        # Colisión: contra folios CONFIRMADOS (marca o factura) es ERROR; contra
        # folios apenas PROPUESTOS por otro lote reciente (el archivo pudo no
        # subirse) es AVISO — el operador decide con SAE enfrente. Antes la
        # estampa inmediata hacía de libro de reservas; ya no existe (regla del
        # dueño 29-ago) y esta es la red que queda para la ventana export→import.
        for s in res.series:
            serie = s["serie"]
            rango = set(range(contador[serie], contador[serie] + s["remisiones"]))
            chocan = sorted(rango & _folios_ocupados(db, tenant_id, serie))
            if chocan:
                res.ok = False
                res.errores.append(
                    f"serie {serie}: los folios {', '.join(map(str, chocan))} ya existen "
                    "(remisión estampada o factura) — verifica el folio inicial contra SAE"
                )
                continue
            propuestos = sorted(rango & _folios_propuestos(db, tenant_id, serie))
            if propuestos:
                res.avisos.append(
                    f"serie {serie}: los folios {', '.join(map(str, propuestos))} salieron "
                    "PROPUESTOS en otro archivo reciente aún sin confirmar — si aquel "
                    "archivo se importó en SAE, usar el mismo rango duplicaría documentos"
                )
        if not res.ok:
            return res, None, None

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

        # Solo partidas vivas: una devolución total deja la línea con cantidad
        # 0, y SAE importaría un concepto en cero que el PAC rechaza.
        for ln in (x for x in rem.lineas if Decimal(str(x.cantidad_solicitada or 0)) > 0):
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

        if tipo == "FACTURA":
            # Solo el RASTRO del export (aviso de doble export y continuidad del
            # folio sugerido). El folio NO se estampa como factura: factura_sae
            # lo pone el espejo cuando SAE confirma.
            rem.export_sae_at = datetime.now(timezone.utc)
            rem.export_sae_folio = f"{doc.serie} {doc.folio}"

    buf = io.BytesIO()
    libro.save(buf)
    sufijo = dia.strftime("%Y-%m-%d")
    nombre = (
        f"{'FACTURA_massiva' if tipo == 'FACTURA' else 'PEDIDO_massivo'}"
        f"_SAE_emp{res.empresa}_{len(docs)}remisiones_{sufijo}.xls"
    )
    return res, buf.getvalue(), nombre
