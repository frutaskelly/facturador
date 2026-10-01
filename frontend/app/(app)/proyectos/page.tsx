"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import type { Proyecto } from "@/lib/types";

// Un proyecto es la negociación con nombre propio ("HOSPITALES TUXTLA"). Es la
// ÚNICA fuente (decisión del dueño, 1-oct-2026): su lista cobra los precios,
// sus series reparten las facturas en Reportes y Cobranza, y «se reporta en»
// junta en una fila lo que se cobra aparte (NERI → CERESOS).
const lista = (v: unknown) => String(v ?? "").split(/[\s,;]+/).filter(Boolean);
// Las palabras pueden llevar espacio («SECRETARIO NERI»): sólo coma o punto y coma separan.
const frases = (v: unknown) => String(v ?? "").split(/[,;]+/).map((x) => x.trim()).filter(Boolean);

const config: CrudConfig<Proyecto> = {
  title: "Proyectos",
  subtitle: "Las negociaciones con nombre propio. Aquí se dan de alta y de aquí salen la lista de precios y las filas de Reportes y Cobranza.",
  newLabel: "Nuevo proyecto",
  basePath: "/api/v1/proyectos",
  writePerm: "cliente:gestionar",
  searchable: true,
  columns: [
    { header: "Proyecto", cell: (p) => <span className="font-medium">{p.nombre}</span> },
    { header: "Cliente", cell: (p) => p.cliente_nombre ?? "(de todo el grupo)" },
    { header: "Sucursal", cell: (p) => p.sucursal_nombre ?? "(todas)" },
    { header: "Series", cell: (p) => (p.series ?? []).join(", ") || "—" },
    { header: "Lista de precios", cell: (p) => p.lista_nombre ?? "—" },
    { header: "Se reporta en", cell: (p) => p.reporta_en_nombre ?? "—" },
    { header: "Estado", cell: (p) => <Badge tone={p.activo ? "success" : "muted"}>{p.activo ? "Activo" : "Inactivo"}</Badge> },
  ],
  fields: [
    { name: "nombre", label: "Nombre", required: true, placeholder: "Hospitales Tuxtla" },
    { name: "codigo", label: "Código", readOnly: true, hint: "Se genera del nombre" },
    { name: "cliente_id", label: "Cliente", type: "select", search: true,
      hint: "Vacío = el proyecto es del grupo y lo pueden usar varios clientes." },
    { name: "sucursal_id", label: "Sucursal", type: "select", search: true, filterBy: "cliente_id",
      hint: "Un proyecto por sucursal. Vacío = aplica en cualquier sucursal." },
    { name: "lista_id", label: "Lista de precios", type: "select", search: true, colSpan: 2,
      hint: "Con esta lista se cobran las remisiones del proyecto. Vacío = la del cliente o la plaza." },
    { name: "series", label: "Series de factura", type: "multisearch", colSpan: 2,
      placeholder: "Busca la serie (ZEHMOTG…)", allowCustom: true,
      normalize: (s) => s.replace(/[\s,;]+/g, "").toUpperCase(),
      hint: "Las facturas de estas series (también las que llegan del SAE) son de este proyecto. Si una serie sólo existe en el SAE, escríbela y Enter." },
    { name: "palabras_obs", label: "Si comparte serie: palabras de la observación", colSpan: 2,
      placeholder: "DIF, COSTALES",
      hint: "Sólo si otro proyecto usa la misma serie. Si la observación dice alguna, la factura es de este; el que no tiene palabras se lleva el resto." },
    { name: "reporta_en_id", label: "Se reporta en", type: "select", search: true, colSpan: 2,
      hint: "Cobra con su propia lista pero en Reportes y Cobranza se suma a otro proyecto (NERI → CERESOS). Vacío = es su propia fila." },
    { name: "activo", label: "Activo", type: "switch" },
    // Ticket 86bbyveu1: las facturas del proyecto casi siempre van a las
    // mismas personas — el envío las prellena con esto (editable al enviar).
    { name: "correos_facturas", label: "Correos para envío de facturas", colSpan: 2,
      placeholder: "pagos@hospital.mx, cxc@hospital.mx",
      hint: "Separa varios con coma o espacio. Al enviar una factura de este proyecto, llegan prellenados (se pueden cambiar antes de enviar)." },
    { name: "notas", label: "Notas", type: "textarea", colSpan: 2 },
  ],
  lookups: {
    cliente_id: {
      path: "/api/v1/clientes?limit=500",
      value: (r) => String(r.id),
      label: (r) => String(r.legal_name),
    },
    sucursal_id: {
      path: "/api/v1/sucursales?limit=1000",
      value: (r) => String(r.id),
      label: (r) => String(r.nombre),
      // Con qué se filtra al elegir cliente: los clientes VINCULADOS a la sucursal.
      tag: (r) => ((r.clientes_ids as string[]) ?? []).join(","),
    },
    series: {
      path: "/api/v1/series?tipo_documento=FACTURA&limit=500",
      value: (r) => String(r.codigo),
      label: (r) => String(r.codigo),
    },
    lista_id: {
      path: "/api/v1/listas-precios?limit=200",
      value: (r) => String(r.id),
      label: (r) => String(r.nombre),
    },
    reporta_en_id: {
      path: "/api/v1/proyectos?limit=500",
      value: (r) => String(r.id),
      label: (r) => String(r.nombre),
    },
  },
  newValues: () => ({
    codigo: "", nombre: "", cliente_id: "", sucursal_id: "", lista_id: "", series: "",
    palabras_obs: "", reporta_en_id: "", activo: true, correos_facturas: "", notas: "",
  }),
  toForm: (p) => ({
    codigo: p.codigo,
    nombre: p.nombre,
    cliente_id: p.cliente_id ?? "",
    sucursal_id: p.sucursal_id ?? "",
    lista_id: p.lista_id ?? "",
    series: (p.series ?? []).join(","),
    palabras_obs: (p.palabras_obs ?? []).join(", "),
    reporta_en_id: p.reporta_en_id ?? "",
    activo: p.activo,
    correos_facturas: (p.correos_facturas ?? []).join(", "),
    notas: p.notas ?? "",
  }),
  toPayload: (v) => ({
    // `codigo` lo genera el backend a partir del nombre; no se envía.
    nombre: v.nombre,
    cliente_id: (v.cliente_id as string) || null,
    sucursal_id: (v.sucursal_id as string) || null,
    lista_id: (v.lista_id as string) || null,
    // Texto libre → lista: el backend normaliza (mayúsculas, sin repetidos).
    series: lista(v.series),
    palabras_obs: frases(v.palabras_obs),
    reporta_en_id: (v.reporta_en_id as string) || null,
    activo: v.activo,
    // Texto libre → lista: el backend valida y normaliza cada correo.
    correos_facturas: lista(v.correos_facturas),
    notas: (v.notas as string) || null,
  }),
  rowLabel: (p) => p.nombre,
};

export default function Page() {
  return <CrudPage<Proyecto> config={config} />;
}
