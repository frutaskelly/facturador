"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import type { Proyecto } from "@/lib/types";

// Un proyecto es la negociación con nombre propio ("HOSPITALES E IMSS
// BIENESTAR"). Existe para poder colgarle una lista de precios: el mismo
// cliente, en la misma plaza, cobra distinto según el programa al que entrega.
const config: CrudConfig<Proyecto> = {
  title: "Proyectos",
  subtitle: "Las negociaciones con nombre propio. Cada una puede tener su propia lista de precios.",
  newLabel: "Nuevo proyecto",
  basePath: "/api/v1/proyectos",
  writePerm: "cliente:gestionar",
  searchable: true,
  columns: [
    { header: "Código", cell: (p) => <span className="font-medium">{p.codigo}</span> },
    { header: "Nombre", cell: (p) => p.nombre },
    { header: "Cliente", cell: (p) => p.cliente_nombre ?? "(de todo el grupo)" },
    { header: "Sucursales", cell: (p) => (p.sucursales_nombres?.length ? p.sucursales_nombres.join(", ") : "—") },
    { header: "Estado", cell: (p) => <Badge tone={p.activo ? "success" : "muted"}>{p.activo ? "Activo" : "Inactivo"}</Badge> },
  ],
  fields: [
    { name: "nombre", label: "Nombre", required: true, placeholder: "Hospitales e IMSS Bienestar" },
    { name: "codigo", label: "Código", readOnly: true, hint: "Se genera del nombre" },
    { name: "cliente_id", label: "Cliente", type: "select",
      hint: "Vacío = el proyecto es del grupo y lo pueden usar varios clientes." },
    { name: "sucursal_ids", label: "Sucursales donde entrega", type: "multiselect",
      filterBy: "cliente_id", colSpan: 2,
      hint: "Una o más. Con cliente elegido solo salen las suyas; un proyecto del grupo puede abarcar de varios.",
      emptyText: "El cliente no tiene sucursales dadas de alta." },
    { name: "activo", label: "Activo", type: "switch" },
    { name: "notas", label: "Notas", type: "textarea", colSpan: 2 },
  ],
  lookups: {
    cliente_id: {
      path: "/api/v1/clientes?limit=500",
      value: (r) => String(r.id),
      label: (r) => String(r.legal_name),
    },
    sucursal_ids: {
      path: "/api/v1/sucursales?limit=1000",
      value: (r) => String(r.id),
      label: (r) => String(r.nombre),
      // Con qué se filtra al elegir cliente (ver `filterBy` arriba).
      tag: (r) => String(r.cliente_id),
    },
  },
  newValues: () => ({ codigo: "", nombre: "", cliente_id: "", sucursal_ids: "", activo: true, notas: "" }),
  toForm: (p) => ({
    codigo: p.codigo,
    nombre: p.nombre,
    cliente_id: p.cliente_id ?? "",
    sucursal_ids: (p.sucursal_ids ?? []).join(","),
    activo: p.activo,
    notas: p.notas ?? "",
  }),
  toPayload: (v) => ({
    // `codigo` lo genera el backend a partir del nombre; no se envía.
    nombre: v.nombre,
    cliente_id: (v.cliente_id as string) || null,
    sucursal_ids: String(v.sucursal_ids ?? "").split(",").filter(Boolean),
    activo: v.activo,
    notas: (v.notas as string) || null,
  }),
  rowLabel: (p) => p.nombre,
};

export default function Page() {
  return <CrudPage<Proyecto> config={config} />;
}
