// es-MX / MXN formatting helpers.

export function fmtMoney(value: number | string | null | undefined, currency = "MXN"): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("es-MX", { style: "currency", currency }).format(Number(value));
}

/** Monto abreviado para ejes y chips: $1.2 M, $450 k, $980.
 *
 * Un eje no tiene ancho para "$1,234,567.89" y repetirlo cinco veces lo vuelve
 * ilegible; la cifra exacta vive en el tooltip de la barra. */
export function fmtMoneyCorto(value: number, currency = "MXN"): string {
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: Math.abs(value) >= 1_000_000 ? 1 : 0,
  }).format(value);
}

export function fmtNumber(value: number | string | null | undefined, maxFractionDigits = 4): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("es-MX", { maximumFractionDigits: maxFractionDigits }).format(Number(value));
}

export function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("es-MX", { dateStyle: "medium" }).format(d);
}

export function fmtDateTime(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(d);
}
