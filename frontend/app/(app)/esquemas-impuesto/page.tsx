"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import type { EsquemaImpuesto } from "@/lib/types";

// Fracción (0.16) → porcentaje legible sin redondear a entero. Conserva
// decimales reales de las retenciones (ISR 1.25%, IVA 10.6667%) y limpia el
// ruido de punto flotante. 0.16 → "16", 0.0125 → "1.25".
const pct = (frac: unknown): string => String(+(Number(frac) * 100).toFixed(4));

const config: CrudConfig<EsquemaImpuesto> = {
  title: "Esquemas de impuesto",
  subtitle: "Tasas de IVA / IEPS y retenciones",
  newLabel: "Nuevo esquema de impuesto",
  basePath: "/api/v1/esquemas-impuesto",
  writePerm: "esquema_impuesto:gestionar",
  columns: [
    { header: "Código", cell: (e) => <span className="font-medium">{e.codigo}</span> },
    { header: "Nombre", cell: (e) => e.nombre },
    { header: "IVA", cell: (e) => `${pct(e.iva_tasa)}%`, className: "text-right" },
    { header: "IEPS", cell: (e) => `${pct(e.ieps_tasa)}%`, className: "text-right" },
    { header: "Exento", cell: (e) => (e.iva_exento ? "Sí" : "No") },
    { header: "Estado", cell: (e) => <Badge tone={e.activo ? "success" : "muted"}>{e.activo ? "Activo" : "Inactivo"}</Badge> },
  ],
  // Los 8 esquemas de Aspel SAE (tabla IMPU02), listos para palomear.
  // TASAS VIGENTES: hoy ningún esquema cobra IEPS en la empresa del cliente
  // (instrucción del 23-jul-2026), así que 1/4/5/6/8 son 16% IVA a secas,
  // 2 y 7 son 0% y el 3 es exento. Los nombres conservan la etiqueta original
  // de SAE entre paréntesis para reconocerlos; todo es editable después.
  suggestions: {
    label: "Esquemas de SAE",
    title: "Esquemas de impuesto de SAE",
    hint:
      "Los mismos 8 esquemas que usa tu SAE. Hoy ninguno cobra IEPS: si lo reactivas, edita aquí la tasa.",
    keyOf: (e) => e.nombre.trim().toLowerCase(),
    items: [
      ["16% IVA", 16, false, "General gravado: no alimentos (limpieza, desechables), alimento para mascota, agua mineral o gaseosa."],
      ["0% IVA", 0, false, "Alimentos: frutas, verduras, carne, lácteos, pan y abarrotes. Es el esquema normal de un alimento, aunque sea procesado."],
      ["IVA exento", 0, true, "Exento. Evítalo en alimentos: para alimentos va el de 0%, que sí permite acreditar el IVA."],
      ["16% IVA (SAE: + 8% IEPS)", 16, false, "Refrescos y jugos. Hoy sin IEPS; si lo reactivas, sube la tasa aquí."],
      ["16% IVA (SAE: + 25% IEPS)", 16, false, "Etiqueta heredada de SAE con tasa desactualizada; hoy es 16% de IVA a secas."],
      ["16% IVA (variante SAE)", 16, false, "Variante de 16% de IVA heredada de SAE."],
      ["0% IVA (SAE: + 8% IEPS)", 0, false, "Dulces, chocolate, galletas dulces, botanas y granola. Hoy sin IEPS."],
      ["16% IVA (SAE: + 26.5% IEPS)", 16, false, "Vino de mesa de hasta 14 grados. Hoy sin IEPS."],
    ].map(([nombre, iva, exento, descripcion]) => ({
      key: String(nombre).trim().toLowerCase(),
      nombre: String(nombre),
      descripcion: String(descripcion),
      payload: {
        nombre: String(nombre),
        descripcion: String(descripcion),
        iva_tasa: Number(iva) / 100,
        ieps_tasa: 0,
        retencion_iva_tasa: 0,
        retencion_isr_tasa: 0,
        iva_exento: Boolean(exento),
        activo: true,
      },
    })),
  },
  fields: [
    { name: "codigo", label: "Código", readonly: true, hint: "Se genera automáticamente" },
    { name: "nombre", label: "Nombre", required: true },
    { name: "descripcion", label: "Descripción", type: "textarea", colSpan: 2 },
    { name: "iva_tasa", label: "Tasa IVA (%)", type: "number", step: "0.01", hint: "16 = 16%. Admite decimales." },
    { name: "ieps_tasa", label: "Tasa IEPS (%)", type: "number", step: "0.01", hint: "8 = 8%. Admite decimales." },
    { name: "retencion_iva_tasa", label: "Retención IVA (%)", type: "number", step: "0.0001", hint: "Ej. 10.6667" },
    { name: "retencion_isr_tasa", label: "Retención ISR (%)", type: "number", step: "0.01", hint: "Ej. 1.25" },
    { name: "iva_exento", label: "IVA exento", type: "switch" },
    { name: "activo", label: "Activo", type: "switch" },
  ],
  newValues: () => ({
    codigo: "",
    nombre: "",
    descripcion: "",
    iva_tasa: "0",
    ieps_tasa: "0",
    retencion_iva_tasa: "0",
    retencion_isr_tasa: "0",
    iva_exento: false,
    activo: true,
  }),
  toForm: (e) => ({
    codigo: e.codigo,
    nombre: e.nombre,
    descripcion: e.descripcion ?? "",
    // El backend guarda fracción (0.16); el form usa % (16), con decimales.
    iva_tasa: pct(e.iva_tasa),
    ieps_tasa: pct(e.ieps_tasa),
    retencion_iva_tasa: pct(e.retencion_iva_tasa),
    retencion_isr_tasa: pct(e.retencion_isr_tasa),
    iva_exento: e.iva_exento,
    activo: e.activo,
  }),
  toPayload: (v) => ({
    // `codigo` lo autogenera el backend (ESQ-NNN); no se envía.
    nombre: v.nombre,
    descripcion: (v.descripcion as string) || null,
    // % entero del form → fracción que espera el backend (16 → 0.16).
    iva_tasa: (Number(v.iva_tasa) || 0) / 100,
    ieps_tasa: (Number(v.ieps_tasa) || 0) / 100,
    retencion_iva_tasa: (Number(v.retencion_iva_tasa) || 0) / 100,
    retencion_isr_tasa: (Number(v.retencion_isr_tasa) || 0) / 100,
    iva_exento: v.iva_exento,
    activo: v.activo,
  }),
  rowLabel: (e) => e.nombre,
};

export default function Page() {
  return <CrudPage<EsquemaImpuesto> config={config} />;
}
