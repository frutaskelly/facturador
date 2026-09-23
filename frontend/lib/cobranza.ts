// Cobranza — tipos de estado de cuenta y recibos de pago (REP).
export type FacturaSaldo = {
  factura_id: string; serie: string; folio: number; uuid: string | null;
  fecha: string; vencimiento: string; dias_vencida: number;
  total: string; saldo_insoluto: string;
};

/** Fila de la tabla global de pendientes (rediseño 86bbyw5u2). */
export type FacturaPendiente = {
  factura_id: string; serie: string; folio: number; cliente_id: string;
  fecha: string; vencimiento: string; dias_vencida: number;
  total: string; saldo_insoluto: string;
  estado_pago: "PENDIENTE" | "PARCIAL";
};

export type ReciboFactura = {
  /** null en el espejo cuando SAE abonó a una factura que aquí no está. */
  factura_id: string | null; serie: string | null; folio: number | null;
  /** La factura como la nombra SAE (sólo el espejo). */
  factura_ref?: string | null;
  importe_pagado: string; num_parcialidad: number;
  saldo_anterior: string | null; saldo_insoluto: string | null;
};

export type Recibo = {
  id: string; serie: string; folio: number; cliente_id: string;
  fecha_pago: string; forma_pago: string; monto: string; moneda: string;
  num_operacion: string | null; banco: string | null;
  estado: "BORRADOR" | "TIMBRADO" | "CANCELADO"; uuid: string | null;
  fecha_timbrado: string | null; facturas: ReciboFactura[];
  /** ESPEJO_SAE = lo timbró SAE: aquí sólo se consulta. */
  origen?: "NATIVO" | "ESPEJO_SAE";
};

/** El folio de la factura que abona un renglón, con el nombre de SAE de respaldo. */
export const folioRelacionado = (f: { serie: string | null; folio: number | null; factura_ref?: string | null }) =>
  f.folio !== null ? `${f.serie ?? ""}${f.folio}` : (f.factura_ref ?? "—");

// Formas de pago SAT más usadas para cobranza.
export const FORMA_PAGO_SAT: { value: string; label: string }[] = [
  { value: "03", label: "03 · Transferencia" },
  { value: "01", label: "01 · Efectivo" },
  { value: "04", label: "04 · Tarjeta de crédito" },
  { value: "28", label: "28 · Tarjeta de débito" },
  { value: "02", label: "02 · Cheque nominativo" },
  { value: "99", label: "99 · Por definir" },
];
