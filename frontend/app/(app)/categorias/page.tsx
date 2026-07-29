"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import type { Categoria } from "@/lib/types";

const config: CrudConfig<Categoria> = {
  title: "Categorías",
  subtitle: "Categorías de productos",
  newLabel: "Nueva categoría",
  basePath: "/api/v1/categorias",
  writePerm: "categoria:gestionar",
  searchable: false,
  columns: [
    { header: "Código", cell: (c) => <span className="font-medium">{c.codigo}</span> },
    { header: "Nombre", cell: (c) => c.nombre },
    { header: "Estado", cell: (c) => <Badge tone={c.activo ? "success" : "muted"}>{c.activo ? "Activo" : "Inactivo"}</Badge> },
  ],
  fields: [
    { name: "nombre", label: "Nombre", required: true },
    {
      name: "codigo",
      label: "Código",
      readonly: true,
      // Regla "el backend calcula todo": el código lo genera el SERVIDOR al
      // guardar (services/categoria_codigo.py) — aquí no se deriva nada.
      hint: "Se genera automáticamente al guardar",
      derive: (f) => String(f._codigo_guardado ?? "") || "(se genera al guardar)",
    },
    { name: "descripcion", label: "Descripción", type: "textarea", colSpan: 2 },
    { name: "activo", label: "Activo", type: "switch" },
  ],
  newValues: () => ({ _codigo_guardado: "", nombre: "", descripcion: "", activo: true }),
  toForm: (c) => ({
    _codigo_guardado: c.codigo,
    nombre: c.nombre,
    descripcion: c.descripcion ?? "",
    activo: c.activo,
  }),
  // `codigo` NUNCA viaja: al crear lo genera el backend (con sufijo
  // anticolisión) y al editar se conserva el guardado (renombrar no lo cambia).
  toPayload: (v) => ({
    nombre: v.nombre,
    descripcion: (v.descripcion as string) || null,
    activo: v.activo,
  }),
  rowLabel: (c) => c.nombre,
};

export default function Page() {
  return <CrudPage<Categoria> config={config} />;
}
