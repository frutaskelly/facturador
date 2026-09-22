// Rangos de fecha del tablero: los presets, el corrimiento al pasado y las
// etiquetas. Todo en fechas LOCALES con formato ISO corto (`2026-09-22`), que
// es lo que entienden `<input type="date">` y el backend.
//
// A propósito no se usa `new Date("2026-09-22")`: ese constructor interpreta la
// cadena como UTC y en México devuelve el día anterior a partir de las 18:00.

export type Rango = { desde: string; hasta: string };

const pad = (n: number) => String(n).padStart(2, "0");

export function iso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fecha(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function hoyISO(): string {
  return iso(new Date());
}

export function sumarDias(s: string, n: number): string {
  const d = fecha(s);
  d.setDate(d.getDate() + n);
  return iso(d);
}

/** Mismo día n meses adelante/atrás, recortado al último del mes si no existe. */
export function sumarMeses(s: string, n: number): string {
  const d = fecha(s);
  const dia = d.getDate();
  d.setDate(1);
  d.setMonth(d.getMonth() + n);
  const ultimo = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
  d.setDate(Math.min(dia, ultimo));
  return iso(d);
}

export function finDeMes(s: string): string {
  const d = fecha(s);
  return iso(new Date(d.getFullYear(), d.getMonth() + 1, 0));
}

export function inicioDeMes(s: string): string {
  const d = fecha(s);
  return iso(new Date(d.getFullYear(), d.getMonth(), 1));
}

export function diasDe(r: Rango): number {
  return Math.round((fecha(r.hasta).getTime() - fecha(r.desde).getTime()) / 86_400_000) + 1;
}

export type PresetKey =
  | "7d" | "30d" | "mes" | "mes_pasado" | "3m" | "6m" | "12m" | "anio";

export const PRESETS: { key: PresetKey; label: string }[] = [
  { key: "7d", label: "Últimos 7 días" },
  { key: "30d", label: "Últimos 30 días" },
  { key: "mes", label: "Este mes" },
  { key: "mes_pasado", label: "Mes pasado" },
  { key: "3m", label: "Últimos 3 meses" },
  { key: "6m", label: "Últimos 6 meses" },
  { key: "12m", label: "Últimos 12 meses" },
  { key: "anio", label: "Este año" },
];

/** El rango de un preset, siempre terminando hoy salvo "mes pasado". */
export function rangoPreset(key: PresetKey, hoy = hoyISO()): Rango {
  switch (key) {
    case "7d":
      return { desde: sumarDias(hoy, -6), hasta: hoy };
    case "30d":
      return { desde: sumarDias(hoy, -29), hasta: hoy };
    case "mes":
      return { desde: inicioDeMes(hoy), hasta: hoy };
    case "mes_pasado": {
      const previo = sumarMeses(inicioDeMes(hoy), -1);
      return { desde: previo, hasta: finDeMes(previo) };
    }
    // "Últimos N meses" incluye el mes en curso, aún incompleto: el dueño
    // pregunta por el trimestre que corre, no por el que cerró.
    case "3m":
      return { desde: inicioDeMes(sumarMeses(hoy, -2)), hasta: hoy };
    case "6m":
      return { desde: inicioDeMes(sumarMeses(hoy, -5)), hasta: hoy };
    case "12m":
      return { desde: inicioDeMes(sumarMeses(hoy, -11)), hasta: hoy };
    case "anio":
      return { desde: `${fecha(hoy).getFullYear()}-01-01`, hasta: hoy };
  }
}

/** ¿Qué preset describe este rango? `null` = personalizado. */
export function presetDe(r: Rango, hoy = hoyISO()): PresetKey | null {
  return PRESETS.find((p) => {
    const q = rangoPreset(p.key, hoy);
    return q.desde === r.desde && q.hasta === r.hasta;
  })?.key ?? null;
}

/**
 * Corre el rango al pasado (−1) o al futuro (+1).
 *
 * El salto se mide en la unidad del propio rango: meses completos retroceden
 * meses completos (agosto antes de septiembre, no "los 30 días de antes"), y un
 * mes empezado cae en el mismo tramo del mes anterior (del 1 al 22 de agosto).
 * Corrido en días, "mes pasado" se volvería un rango a caballo entre dos meses
 * y la comparación dejaría de significar nada.
 */
export function correr(r: Rango, dir: -1 | 1): Rango {
  const d = fecha(r.desde);
  const dias = diasDe(r);
  if (d.getDate() === 1) {
    if (r.hasta === finDeMes(r.hasta)) {
      const meses = (fecha(r.hasta).getFullYear() - d.getFullYear()) * 12
        + (fecha(r.hasta).getMonth() - d.getMonth()) + 1;
      const desde = sumarMeses(r.desde, dir * meses);
      return { desde, hasta: finDeMes(sumarMeses(inicioDeMes(r.hasta), dir * meses)) };
    }
    const desde = sumarMeses(r.desde, dir);
    const tope = finDeMes(desde);
    const hasta = sumarDias(desde, dias - 1);
    return { desde, hasta: hasta > tope ? tope : hasta };
  }
  return { desde: sumarDias(r.desde, dir * dias), hasta: sumarDias(r.hasta, dir * dias) };
}

const DIA_MES = new Intl.DateTimeFormat("es-MX", { day: "numeric", month: "short" });
const DIA_MES_ANIO = new Intl.DateTimeFormat("es-MX", { day: "numeric", month: "short", year: "numeric" });
const MES_ANIO = new Intl.DateTimeFormat("es-MX", { month: "short", year: "2-digit" });

export const etiquetaDia = (s: string) => DIA_MES.format(fecha(s));
export const etiquetaMes = (s: string) => MES_ANIO.format(fecha(s));

/** "1 – 22 sep 2026" / "24 ago – 22 sep 2026": el año se dice una sola vez. */
export function etiquetaRango(r: Rango): string {
  if (r.desde === r.hasta) return DIA_MES_ANIO.format(fecha(r.desde));
  const mismoMes = r.desde.slice(0, 7) === r.hasta.slice(0, 7);
  const izq = mismoMes ? String(fecha(r.desde).getDate()) : etiquetaDia(r.desde);
  return `${izq} – ${DIA_MES_ANIO.format(fecha(r.hasta))}`;
}
