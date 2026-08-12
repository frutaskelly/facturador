// Cobranza — tipos de estado de cuenta y recibos de pago (REP).
export type FacturaSaldo = {
  factura_id: string; serie: string; folio: number; uuid: string | null;
  fecha: string; vencimiento: string; dias_vencida: number;
  total: string; saldo_insoluto: string;
};

export type ReciboFactura = {
  factura_id: string; serie: string | null; folio: number | null;
  importe_pagado: string; num_parcialidad: number;
  saldo_anterior: string; saldo_insoluto: string;
};

export type Recibo = {
  id: string; serie: string; folio: number; cliente_id: string;
  fecha_pago: string; forma_pago: string; monto: string; moneda: string;
  num_operacion: string | null; banco: string | null;
  estado: "BORRADOR" | "TIMBRADO" | "CANCELADO"; uuid: string | null;
  fecha_timbrado: string | null; facturas: ReciboFactura[];
};

// Formas de pago SAT más usadas para cobranza.
export const FORMA_PAGO_SAT: { value: string; label: string }[] = [
  { value: "03", label: "03 · Transferencia" },
  { value: "01", label: "01 · Efectivo" },
  { value: "04", label: "04 · Tarjeta de crédito" },
  { value: "28", label: "28 · Tarjeta de débito" },
  { value: "02", label: "02 · Cheque nominativo" },
  { value: "99", label: "99 · Por definir" },
];
