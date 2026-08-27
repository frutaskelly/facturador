// Catálogos SAT del CFDI 4.0, compartidos por el alta de clientes (donde se
// definen los defaults del cliente) y por el alta de factura directa (donde se
// pueden sobreescribir por factura).

export type SatOption = { value: string; label: string };

// Catálogo SAT c_RegimenFiscal (CFDI 4.0). Única fuente para signup,
// Ajustes › Empresa y el alta de clientes (antes cada página traía su copia).
export const REGIMENES_FISCALES: SatOption[] = [
  { value: "601", label: "601 — General de Ley Personas Morales" },
  { value: "603", label: "603 — Personas Morales con Fines no Lucrativos" },
  { value: "605", label: "605 — Sueldos y Salarios e Ingresos Asimilados a Salarios" },
  { value: "606", label: "606 — Arrendamiento" },
  { value: "607", label: "607 — Régimen de Enajenación o Adquisición de Bienes" },
  { value: "608", label: "608 — Demás ingresos" },
  { value: "610", label: "610 — Residentes en el Extranjero sin Establecimiento Permanente en México" },
  { value: "611", label: "611 — Ingresos por Dividendos" },
  { value: "612", label: "612 — Personas Físicas con Actividades Empresariales y Profesionales" },
  { value: "614", label: "614 — Ingresos por intereses" },
  { value: "615", label: "615 — Régimen de los ingresos por obtención de premios" },
  { value: "616", label: "616 — Sin obligaciones fiscales" },
  { value: "620", label: "620 — Sociedades Cooperativas de Producción que optan por diferir ingresos" },
  { value: "621", label: "621 — Incorporación Fiscal" },
  { value: "622", label: "622 — Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras" },
  { value: "623", label: "623 — Opcional para Grupos de Sociedades" },
  { value: "624", label: "624 — Coordinados" },
  { value: "625", label: "625 — Régimen de Actividades Empresariales con ingresos a través de Plataformas Tecnológicas" },
  { value: "626", label: "626 — Régimen Simplificado de Confianza (RESICO)" },
  { value: "628", label: "628 — Hidrocarburos" },
  { value: "629", label: "629 — Regímenes Fiscales Preferentes y Empresas Multinacionales" },
  { value: "630", label: "630 — Enajenación de acciones en bolsa de valores" },
];

// Catálogo SAT c_UsoCFDI (CFDI 4.0).
export const USO_CFDI_OPTS: SatOption[] = [
  { value: "G01", label: "G01 — Adquisición de mercancías" },
  { value: "G02", label: "G02 — Devoluciones, descuentos o bonificaciones" },
  { value: "G03", label: "G03 — Gastos en general" },
  { value: "I01", label: "I01 — Construcciones" },
  { value: "I02", label: "I02 — Mobiliario y equipo de oficina por inversiones" },
  { value: "I03", label: "I03 — Equipo de transporte" },
  { value: "I04", label: "I04 — Equipo de cómputo y accesorios" },
  { value: "I05", label: "I05 — Dados, troqueles, moldes, matrices y otros activos" },
  { value: "I06", label: "I06 — Comunicaciones telefónicas" },
  { value: "I07", label: "I07 — Comunicaciones satelitales" },
  { value: "I08", label: "I08 — Otra maquinaria y equipo" },
  { value: "D01", label: "D01 — Honorarios médicos, dentales y gastos hospitalarios" },
  { value: "D02", label: "D02 — Gastos médicos por incapacidad o discapacidad" },
  { value: "D03", label: "D03 — Gastos funerales" },
  { value: "D04", label: "D04 — Donativos" },
  { value: "D05", label: "D05 — Intereses por créditos hipotecarios (casa habitación)" },
  { value: "D06", label: "D06 — Aportaciones voluntarias al SAR" },
  { value: "D07", label: "D07 — Primas por seguros de gastos médicos" },
  { value: "D08", label: "D08 — Gastos de transportación escolar obligatoria" },
  { value: "D09", label: "D09 — Depósitos en cuentas para el ahorro, pensiones" },
  { value: "D10", label: "D10 — Pagos por servicios educativos (colegiaturas)" },
  { value: "S01", label: "S01 — Sin efectos fiscales" },
  { value: "CP01", label: "CP01 — Pagos" },
  { value: "CN01", label: "CN01 — Nómina" },
];

// Catálogo SAT c_FormaPago (CFDI 4.0).
export const FORMA_PAGO_OPTS: SatOption[] = [
  { value: "01", label: "01 — Efectivo" },
  { value: "02", label: "02 — Cheque nominativo" },
  { value: "03", label: "03 — Transferencia electrónica de fondos" },
  { value: "04", label: "04 — Tarjeta de crédito" },
  { value: "05", label: "05 — Monedero electrónico" },
  { value: "06", label: "06 — Dinero electrónico" },
  { value: "08", label: "08 — Vales de despensa" },
  { value: "12", label: "12 — Dación en pago" },
  { value: "13", label: "13 — Pago por subrogación" },
  { value: "14", label: "14 — Pago por consignación" },
  { value: "15", label: "15 — Condonación" },
  { value: "17", label: "17 — Compensación" },
  { value: "23", label: "23 — Novación" },
  { value: "24", label: "24 — Confusión" },
  { value: "25", label: "25 — Remisión de deuda" },
  { value: "26", label: "26 — Prescripción o caducidad" },
  { value: "27", label: "27 — A satisfacción del acreedor" },
  { value: "28", label: "28 — Tarjeta de débito" },
  { value: "29", label: "29 — Tarjeta de servicios" },
  { value: "30", label: "30 — Aplicación de anticipos" },
  { value: "31", label: "31 — Intermediario pagos" },
  { value: "99", label: "99 — Por definir" },
];

// Catálogo SAT c_MetodoPago (CFDI 4.0).
export const METODO_PAGO_OPTS: SatOption[] = [
  { value: "PUE", label: "PUE — Pago en una sola exhibición" },
  { value: "PPD", label: "PPD — Pago en parcialidades o diferido" },
];

// Defaults que aplica el backend cuando ni la factura ni el cliente traen valor
// (ver facturas.py: factura_directa).
export const USO_CFDI_FALLBACK = "G01";
export const FORMA_PAGO_FALLBACK = "99";
export const METODO_PAGO_FALLBACK = "PPD";

// ─────────────────────────────────────────────────────────────────────────────
// Emisor: entidades federativas y validación local del RFC. Compartido por
// Ajustes › Empresas (el panel de edición) y Ajustes › Empresa › Configuración.
// ─────────────────────────────────────────────────────────────────────────────

/** Catálogo SAT c_Estado (clave de 3 letras). */
export const ESTADOS_MX: SatOption[] = [
  ["AGU", "Aguascalientes"], ["BCN", "Baja California"], ["BCS", "Baja California Sur"],
  ["CAM", "Campeche"], ["CHP", "Chiapas"], ["CHH", "Chihuahua"], ["COA", "Coahuila"],
  ["COL", "Colima"], ["CMX", "Ciudad de México"], ["DUR", "Durango"], ["MEX", "Estado de México"],
  ["GUA", "Guanajuato"], ["GRO", "Guerrero"], ["HID", "Hidalgo"], ["JAL", "Jalisco"],
  ["MIC", "Michoacán"], ["MOR", "Morelos"], ["NAY", "Nayarit"], ["NLE", "Nuevo León"],
  ["OAX", "Oaxaca"], ["PUE", "Puebla"], ["QUE", "Querétaro"], ["ROO", "Quintana Roo"],
  ["SLP", "San Luis Potosí"], ["SIN", "Sinaloa"], ["SON", "Sonora"], ["TAB", "Tabasco"],
  ["TAM", "Tamaulipas"], ["TLA", "Tlaxcala"], ["VER", "Veracruz"], ["YUC", "Yucatán"],
  ["ZAC", "Zacatecas"],
].map(([value, label]) => ({ value, label }));

/** Acepta una clave SAT ("JAL") o un nombre ("Jalisco") y devuelve la clave. */
export function normalizaEstado(v: string): string {
  const s = v.trim();
  if (!s) return "";
  if (ESTADOS_MX.some((o) => o.value === s)) return s;
  const byLabel = ESTADOS_MX.find((o) => o.label.toLowerCase() === s.toLowerCase());
  return byLabel ? byLabel.value : s;
}

// ── Validación local de RFC: formato + dígito verificador del SAT ──────────
// Espejo de backend/app/services/rfc.py: el último carácter del RFC es un
// dígito verificador determinista — atrapa dígitos transpuestos y typos al
// instante, sin consultar al SAT ni gastar folios.
const RFC_RE = /^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$/;
const RFC_TABLA = "0123456789ABCDEFGHIJKLMN&OPQRSTUVWXYZ";

function rfcDigitoVerificador(rfcSinDv: string): string {
  const base = rfcSinDv.padStart(12, " ");
  let suma = 0;
  for (let i = 0; i < 12; i++) {
    const ch = base[i];
    const val = ch === " " ? 37 : ch === "Ñ" ? 38 : RFC_TABLA.indexOf(ch);
    suma += (val < 0 ? 0 : val) * (13 - i);
  }
  const r = 11 - (suma % 11);
  return r === 11 ? "0" : r === 10 ? "A" : String(r);
}

/** ¿Sirve este RFC como EMISOR? (los genéricos del SAT no). */
export function validarRfcEmisor(rfc: string): { ok: boolean; motivo: string } {
  const r = rfc.trim().toUpperCase();
  if (!r) return { ok: false, motivo: "" };
  if (r === "XAXX010101000" || r === "XEXX010101000")
    return { ok: false, motivo: "Los RFC genéricos del SAT no pueden ser el emisor" };
  if (!RFC_RE.test(r))
    return { ok: false, motivo: "Formato inválido: 3-4 letras + fecha (AAMMDD) + homoclave" };
  if (rfcDigitoVerificador(r.slice(0, -1)) !== r[r.length - 1])
    return { ok: false, motivo: "No pasa el dígito verificador del SAT — revisa dígitos transpuestos o la última letra" };
  return { ok: true, motivo: "" };
}
