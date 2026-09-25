"use client";

// Master de facturas: una fila por factura con todo lo que cuelga de ella —
// remisión, OC con la liga al documento original, cobranza, notas de crédito,
// cancelación— y su estado REAL (siete, no los tres de la columna). Cuelga de
// los mismos filtros de rango y cliente que ventas. Las columnas de consulta
// ocasional arrancan ocultas y se prenden desde «Columnas»; el Excel del
// servidor las trae todas, con la liga a la OC y el pie de totales.
import { useMemo, useState } from "react";
import { Download, ExternalLink } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Spinner } from "@/components/ui/Spinner";
import { apiDownload } from "@/lib/api";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useResource } from "@/lib/hooks";

type EstadoMaster =
  | "BORRADOR" | "TIMBRANDO" | "ERROR_TIMBRADO" | "TIMBRADA"
  | "EN_CANCELACION" | "NO_CANCELABLE" | "CANCELADA";

type OC = {
  remision: string; folio: string | null; url: string | null; archivo: string | null;
  canal: string; remitente: string | null; recibida_at: string | null;
};

type Fila = {
  id: string; serie: string; folio: number; fecha: string; fecha_timbrado: string | null;
  estado: EstadoMaster; estado_label: string; estado_detalle: string | null; estado_fecha: string | null;
  uuid: string | null; empresa: string | null; origen: string; semana: number | null;
  cliente: string; rfc: string; cliente_codigo: string | null; cliente_tipo: string;
  proyecto: string | null; sucursal: string | null;
  remisiones: string[]; su_pedido: string | null; fecha_remision: string | null;
  fecha_entrega: string | null; canal: string | null; factura_sae: string | null;
  ocs: OC[]; oc_folio: string | null; oc_url: string | null; oc_archivo: string | null;
  oc_canal: string | null; oc_remitente: string | null; oc_recibida_at: string | null;
  oc_fecha_entrega: string | null; oc_punto_entrega: string | null; oc_cambio_at: string | null;
  subtotal: string; descuento: string; iva: string; ieps: string;
  ret_iva: string; ret_isr: string; total: string; moneda: string;
  forma_pago_label: string; metodo_pago: string; uso_cfdi: string; tipo_comprobante: string;
  pagado: string; saldo: string; pagada: "Sí" | "Parcial" | "No" | null;
  ultimo_pago: string | null; parcialidades: number | null; reps: string[];
  banco: string | null; referencia: string | null; dias_credito: number;
  vencimiento: string | null; dias_vencida: number | null; antiguedad: string | null;
  notas_credito: string[]; nc_importe: string; total_neto: string;
  fecha_cancelacion: string | null; motivo_cancelacion: string | null;
  sustituye_a: string | null; sustituida_por: string | null;
  notas: string | null; creada_por: string | null;
};

type Master = {
  desde: string; hasta: string;
  estados: { clave: EstadoMaster; etiqueta: string; facturas: number }[];
  items: Fila[];
};

// Lo que se cobra y lo que está atorado: el resto se prende a mano.
const POR_DEFECTO: EstadoMaster[] = ["TIMBRADA", "EN_CANCELACION", "ERROR_TIMBRADO"];

const TONO: Record<EstadoMaster, "default" | "success" | "warning" | "danger" | "muted" | "accent"> = {
  BORRADOR: "muted", TIMBRANDO: "accent", ERROR_TIMBRADO: "danger", TIMBRADA: "success",
  EN_CANCELACION: "warning", NO_CANCELABLE: "accent", CANCELADA: "danger",
};

const lista = (xs: string[]) => xs.join(", ");
const guion = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === "" ? <span className="text-muted">—</span> : v;
const num = "whitespace-nowrap text-right tabular-nums";

function col(
  header: string, valor: (f: Fila) => string | number | null | undefined,
  extra: Partial<Column<Fila>> = {},
): Column<Fila> {
  return {
    header, sortable: true, sortValue: valor, exportValue: valor,
    cell: (f) => guion(valor(f)), ...extra,
  };
}
const fecha = (header: string, valor: (f: Fila) => string | null, oculta = false): Column<Fila> =>
  col(header, (f) => valor(f) ?? "", {
    className: "whitespace-nowrap", hiddenByDefault: oculta,
    exportValue: (f) => (valor(f) ? fmtDate(valor(f)!) : ""),
    cell: (f) => (valor(f) ? fmtDate(valor(f)!) : guion(null)),
  });
const dinero = (header: string, valor: (f: Fila) => string, oculta = false): Column<Fila> =>
  col(header, (f) => Number(valor(f)), {
    className: num, hiddenByDefault: oculta,
    exportValue: valor, cell: (f) => fmtMoney(valor(f)),
  });

export function MasterFacturas({
  filtros, rango, clienteNombre,
}: {
  filtros: string;
  rango: string;
  clienteNombre?: string;
}) {
  const res = useResource<Master>(`/api/v1/reportes/master-facturas?${filtros}`);
  const d = res.data;
  const [estados, setEstados] = useState<EstadoMaster[]>(POR_DEFECTO);
  const [visibles, setVisibles] = useState<Fila[] | null>(null);
  const [descargando, setDescargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const filas = useMemo(
    () => (d?.items ?? []).filter((f) => estados.includes(f.estado)),
    [d, estados],
  );

  const cols: Column<Fila>[] = useMemo(() => [
    // 1. Identificación
    col("Folio", (f) => `${f.serie}${f.folio}`, {
      className: "whitespace-nowrap",
      sortValue: (f) => `${f.serie}${String(f.folio).padStart(8, "0")}`,
      cell: (f) => <span className="font-medium">{f.serie}{f.folio}</span>,
    }),
    fecha("Fecha", (f) => f.fecha),
    col("Estado", (f) => f.estado_label, {
      cell: (f) => (
        <span title={f.estado_detalle ?? undefined}>
          <Badge tone={TONO[f.estado]}>{f.estado_label}</Badge>
        </span>
      ),
    }),
    col("Detalle del estado", (f) => f.estado_detalle, { truncate: true, hiddenByDefault: true }),
    col("Semana", (f) => f.semana, { className: num, hiddenByDefault: true }),
    // 2. Cliente y destino
    col("Cliente", (f) => f.cliente, {
      truncate: true, cell: (f) => <span title={f.cliente}>{f.cliente}</span>,
    }),
    col("RFC", (f) => f.rfc, { hiddenByDefault: true }),
    col("Proyecto", (f) => f.proyecto, { truncate: true }),
    col("Sucursal", (f) => f.sucursal),
    // 3. Origen comercial
    col("Remisión", (f) => lista(f.remisiones), { className: "whitespace-nowrap" }),
    col("Su pedido", (f) => f.su_pedido, { className: "whitespace-nowrap" }),
    // 3b. OC
    col("Folio OC", (f) => f.oc_folio, { className: "whitespace-nowrap" }),
    {
      header: "OC original", key: "oc_url",
      exportValue: (f) => f.oc_url ?? "",
      cell: (f) => {
        const ligas = f.ocs.filter((o) => o.url);
        if (ligas.length === 0) {
          return f.ocs.length || f.su_pedido
            ? <span className="text-xs text-muted">sin archivo</span>
            : guion(null);
        }
        return (
          <span className="flex flex-wrap gap-2">
            {ligas.map((o, i) => (
              <a
                key={`${o.url}-${i}`} href={o.url!} target="_blank" rel="noopener noreferrer"
                title={o.archivo ?? o.url!}
                className="inline-flex items-center gap-1 whitespace-nowrap text-accent hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                <ExternalLink size={12} aria-hidden /> {ligas.length === 1 ? "Ver OC" : o.folio ?? `OC ${i + 1}`}
              </a>
            ))}
          </span>
        );
      },
    },
    col("Canal OC", (f) => f.oc_canal, { hiddenByDefault: true }),
    col("Remitente OC", (f) => f.oc_remitente, { hiddenByDefault: true }),
    fecha("OC recibida", (f) => f.oc_recibida_at, true),
    fecha("OC cambió", (f) => f.oc_cambio_at, true),
    fecha("Fecha remisión", (f) => f.fecha_remision, true),
    fecha("Fecha entrega", (f) => f.fecha_entrega, true),
    // 4. Importes
    dinero("Subtotal", (f) => f.subtotal),
    dinero("Descuento", (f) => f.descuento, true),
    dinero("IVA", (f) => f.iva),
    dinero("IEPS", (f) => f.ieps, true),
    dinero("Ret. IVA", (f) => f.ret_iva, true),
    dinero("Ret. ISR", (f) => f.ret_isr, true),
    dinero("Total", (f) => f.total),
    // 5. Fiscal
    col("Forma de pago", (f) => f.forma_pago_label, { truncate: true }),
    col("Método", (f) => f.metodo_pago),
    col("Uso CFDI", (f) => f.uso_cfdi, { hiddenByDefault: true }),
    // 6. Cobranza
    dinero("Pagado", (f) => f.pagado),
    dinero("Saldo", (f) => f.saldo),
    col("¿Pagada?", (f) => f.pagada, {
      cell: (f) => f.pagada === "Sí" ? <Badge tone="success">Sí</Badge>
        : f.pagada === "Parcial" ? <Badge tone="warning">Parcial</Badge>
        : f.pagada === "No" ? <Badge tone="danger">No</Badge> : guion(null),
    }),
    fecha("Último pago", (f) => f.ultimo_pago, true),
    col("Parcialidades", (f) => f.parcialidades, { className: num, hiddenByDefault: true }),
    col("REP", (f) => lista(f.reps), { hiddenByDefault: true }),
    col("Banco", (f) => f.banco, { hiddenByDefault: true }),
    col("Referencia", (f) => f.referencia, { hiddenByDefault: true }),
    col("Días crédito", (f) => f.dias_credito, { className: num, hiddenByDefault: true }),
    fecha("Vencimiento", (f) => f.vencimiento),
    col("Días vencida", (f) => f.dias_vencida, {
      className: num,
      cell: (f) => f.dias_vencida === null ? guion(null)
        : <span className={f.dias_vencida > 0 ? "font-medium text-danger" : ""}>{f.dias_vencida}</span>,
    }),
    col("Antigüedad", (f) => f.antiguedad, { hiddenByDefault: true }),
    // 7. Notas de crédito
    col("Notas de crédito", (f) => lista(f.notas_credito)),
    dinero("Importe NC", (f) => f.nc_importe),
    dinero("Total neto", (f) => f.total_neto, true),
    // 8. Cancelación y sustitución
    fecha("Fecha cancelación", (f) => f.fecha_cancelacion, true),
    col("Motivo cancelación", (f) => f.motivo_cancelacion, { truncate: true, hiddenByDefault: true }),
    col("Sustituye a", (f) => f.sustituye_a, { hiddenByDefault: true }),
    col("Sustituida por", (f) => f.sustituida_por, { hiddenByDefault: true }),
    // 9. Otros
    col("UUID", (f) => f.uuid, {
      hiddenByDefault: true,
      cell: (f) => f.uuid
        ? <span className="font-mono text-xs text-muted" title={f.uuid}>{f.uuid.slice(0, 8)}…</span>
        : guion(null),
    }),
    col("Empresa SAE", (f) => f.empresa, { hiddenByDefault: true }),
    col("Origen", (f) => f.origen, { hiddenByDefault: true }),
    fecha("Fecha timbrado", (f) => f.fecha_timbrado, true),
    col("Creada por", (f) => f.creada_por, { hiddenByDefault: true }),
    col("Nota", (f) => f.notas, {
      truncate: true, cell: (f) => f.notas ? <span title={f.notas}>{f.notas}</span> : guion(null),
    }),
  ], []);

  // Los totales siguen a lo que se VE (estado, filtros de columna y buscador).
  const suma = useMemo(() => {
    const base = visibles ?? filas;
    const s = (k: keyof Fila) => base.reduce((a, f) => a + Number(f[k] ?? 0), 0);
    return {
      facturas: base.length, subtotal: s("subtotal"), iva: s("iva"), total: s("total"),
      pagado: s("pagado"), saldo: s("saldo"), nc: s("nc_importe"),
    };
  }, [visibles, filas]);

  const toggle = (e: EstadoMaster) =>
    setEstados((xs) => (xs.includes(e) ? xs.filter((x) => x !== e) : [...xs, e]));

  async function descargar() {
    setDescargando(true);
    setError(null);
    try {
      const qs = new URLSearchParams(filtros);
      estados.forEach((e) => qs.append("estado", e));
      await apiDownload(`/api/v1/reportes/master-facturas/xlsx?${qs}`,
        `master-facturas_${d?.desde ?? ""}_${d?.hasta ?? ""}.xlsx`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo generar el Excel.");
    } finally {
      setDescargando(false);
    }
  }

  if (res.error) {
    return (
      <Card title="Master de facturas">
        <p className="py-8 text-center text-sm text-muted">No se pudo cargar el master de facturas.</p>
      </Card>
    );
  }

  return (
    <Card
      title="Master de facturas"
      subtitle={`Una fila por factura emitida en ${rango}${clienteNombre ? ` · solo ${clienteNombre}` : ""}`}
    >
      {!d ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Estados">
            {d.estados.map((e) => {
              const activo = estados.includes(e.clave);
              return (
                <button
                  key={e.clave} type="button" aria-pressed={activo} onClick={() => toggle(e.clave)}
                  className={`rounded-full border px-3 py-1 text-xs transition ${
                    activo
                      ? "border-accent bg-accent/10 font-medium text-foreground"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {e.etiqueta} <span className="tabular-nums">({fmtNumber(e.facturas, 0)})</span>
                </button>
              );
            })}
          </div>

          <div className="mb-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4 lg:grid-cols-7">
            {([
              ["Facturas", fmtNumber(suma.facturas, 0)],
              ["Subtotal", fmtMoney(suma.subtotal)],
              ["IVA", fmtMoney(suma.iva)],
              ["Total", fmtMoney(suma.total)],
              ["Pagado", fmtMoney(suma.pagado)],
              ["Saldo", fmtMoney(suma.saldo)],
              ["Notas de crédito", fmtMoney(suma.nc)],
            ] as const).map(([t, v]) => (
              <div key={t} className="rounded-lg border border-border px-3 py-2">
                <div className="text-xs text-muted">{t}</div>
                <div className="font-semibold tabular-nums">{v}</div>
              </div>
            ))}
          </div>

          {error && <div className="mb-3"><Alert tone="danger">{error}</Alert></div>}

          <div className={res.loading ? "opacity-50 transition-opacity" : "transition-opacity"}>
            <DataTable
              rows={filas}
              rowKey={(f) => f.id}
              columns={cols}
              empty={estados.length ? "Sin facturas con esos estados en el rango." : "Elige al menos un estado."}
              storageKey="reportes-master-facturas"
              columnsMenu
              resizable
              headerFilters
              searchable
              searchPlaceholder="Folio, cliente, remisión, OC…"
              paginated
              defaultPageSize={50}
              onFilteredRowsChange={setVisibles}
              toolbarExtra={
                <Button variant="secondary" onClick={descargar} disabled={descargando || estados.length === 0}>
                  {descargando ? <Spinner /> : <Download size={14} aria-hidden />} Excel completo
                </Button>
              }
            />
          </div>
          <p className="mt-3 text-xs text-muted">
            «Excel completo» trae las {fmtNumber(cols.length, 0)} columnas con la liga a la OC, el pie de
            totales y la hoja «OC por factura». Pagado = total − saldo − notas de crédito: el mismo saldo del
            estado de cuenta. Una PUE sale pagada desde que se timbra.
          </p>
        </>
      )}
    </Card>
  );
}
