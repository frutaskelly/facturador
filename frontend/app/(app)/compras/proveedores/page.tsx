"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import type { Proveedor } from "@/lib/types";

const config: CrudConfig<Proveedor> = {
  title: "Proveedores",
  subtitle: "Catálogo de proveedores",
  newLabel: "Nuevo proveedor",
  basePath: "/api/v1/proveedores",
  writePerm: "proveedor:gestionar",
  deletePerm: "proveedor:eliminar",
  searchable: false,
  columns: [
    { header: "Código", cell: (p) => <span className="font-medium">{p.codigo}</span> },
    { header: "Nombre", cell: (p) => p.nombre },
    { header: "RFC", cell: (p) => p.rfc ?? "—" },
    { header: "Teléfono", cell: (p) => p.telefono ?? "—" },
    { header: "Estado", cell: (p) => <Badge tone={p.activo ? "success" : "muted"}>{p.activo ? "Activo" : "Inactivo"}</Badge> },
  ],
  fields: [
    { name: "nombre", label: "Nombre", required: true },
    { name: "codigo", label: "Código", readOnly: true, hint: "Se genera automáticamente" },
    { name: "rfc", label: "RFC" },
    { name: "contacto", label: "Contacto" },
    { name: "telefono", label: "Teléfono" },
    { name: "email", label: "Email" },
    { name: "condiciones_pago", label: "Condiciones de pago", placeholder: "30 días" },
    { name: "activo", label: "Activo", type: "switch" },
    { name: "notas", label: "Notas", type: "textarea", colSpan: 2 },
  ],
  newValues: () => ({
    codigo: "",
    nombre: "",
    rfc: "",
    contacto: "",
    telefono: "",
    email: "",
    condiciones_pago: "",
    activo: true,
    notas: "",
  }),
  toForm: (p) => ({
    codigo: p.codigo,
    nombre: p.nombre,
    rfc: p.rfc ?? "",
    contacto: p.contacto ?? "",
    telefono: p.telefono ?? "",
    email: p.email ?? "",
    condiciones_pago: p.condiciones_pago ?? "",
    activo: p.activo,
    notas: p.notas ?? "",
  }),
  toPayload: (v) => ({
    // `codigo` lo autogenera el backend (PROV-01, …); no se envía.
    nombre: v.nombre,
    rfc: (v.rfc as string) || null,
    contacto: (v.contacto as string) || null,
    telefono: (v.telefono as string) || null,
    email: (v.email as string) || null,
    condiciones_pago: (v.condiciones_pago as string) || null,
    activo: v.activo,
    notas: (v.notas as string) || null,
  }),
  rowLabel: (p) => p.nombre,
};

export default function Page() {
  return <CrudPage<Proveedor> config={config} />;
}
