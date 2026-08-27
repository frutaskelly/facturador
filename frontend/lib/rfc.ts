/**
 * Validación local de RFC: formato + dígito verificador del SAT.
 * Espejo de backend/app/services/rfc.py — atrapa dígitos transpuestos y typos
 * al instante, sin consultar al SAT ni gastar folios del PAC.
 */
const RFC_RE = /^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$/;
const TABLA = "0123456789ABCDEFGHIJKLMN&OPQRSTUVWXYZ";

/** RFC genéricos del SAT: válidos como RECEPTOR (no cumplen dígito verificador). */
export const RFC_GENERICOS = new Set(["XAXX010101000", "XEXX010101000"]);

function digitoVerificador(rfcSinDv: string): string {
  const base = rfcSinDv.padStart(12, " ");
  let suma = 0;
  for (let i = 0; i < 12; i++) {
    const ch = base[i];
    const val = ch === " " ? 37 : ch === "Ñ" ? 38 : TABLA.indexOf(ch);
    suma += (val < 0 ? 0 : val) * (13 - i);
  }
  const r = 11 - (suma % 11);
  return r === 11 ? "0" : r === 10 ? "A" : String(r);
}

/** Motivo del error, o null si el RFC es válido. `permitirGenericos` para receptores. */
export function motivoRfcInvalido(
  rfc: string,
  { permitirGenericos = false }: { permitirGenericos?: boolean } = {},
): string | null {
  const r = (rfc || "").trim().toUpperCase();
  if (!r) return null; // el "obligatorio" lo maneja el propio formulario
  if (RFC_GENERICOS.has(r)) {
    return permitirGenericos ? null : "Los RFC genéricos del SAT no pueden usarse aquí";
  }
  if (!RFC_RE.test(r))
    return "Formato inválido: 3-4 letras + fecha (AAMMDD) + homoclave de 3";
  if (digitoVerificador(r.slice(0, -1)) !== r[r.length - 1])
    return "No pasa el dígito verificador del SAT — revisa si hay dígitos transpuestos o el último carácter";
  return null;
}

/** Motivo del error del código postal, o null si está bien. */
export function motivoCpInvalido(cp: string): string | null {
  const v = (cp || "").trim();
  if (!v) return null;
  return /^\d{5}$/.test(v) ? null : "Debe tener exactamente 5 dígitos";
}
