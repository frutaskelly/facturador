"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import { categoriaCodigo } from "@/lib/codigo";
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
      hint: "Se genera automáticamente a partir del nombre",
      // Vista previa con la misma regla que el backend (lib/codigo.ts es el
      // espejo de app/services/categoria_codigo.py). Una vez creada, el código
      // guardado no cambia aunque se renombre la categoría.
      derive: (f) => {
        if (f._codigo_guardado) return String(f._codigo_guardado);
        const nombre = String(f.nombre ?? "").trim();
        return nombre ? categoriaCodigo(nombre) : "";
      },
    },
    { name: "descripcion", label: "Descripción", type: "textarea", colSpan: 2 },
    { name: "activo", label: "Activo", type: "switch" },
  ],
  newValues: () => ({ _codigo_guardado: "", codigo: "", nombre: "", descripcion: "", activo: true }),
  toForm: (c) => ({
    _codigo_guardado: c.codigo,
    codigo: c.codigo,
    nombre: c.nombre,
    descripcion: c.descripcion ?? "",
    activo: c.activo,
  }),
  toPayload: (v) => ({
    // El backend exige `codigo` al crear (CategoriaCreate): se manda el
    // derivado del nombre. Al editar se re-manda el ya guardado, así el
    // código es estable aunque cambie el nombre.
    codigo: v.codigo,
    nombre: v.nombre,
    descripcion: (v.descripcion as string) || null,
    activo: v.activo,
  }),
  rowLabel: (c) => c.nombre,
};

export default function Page() {
  return <CrudPage<Categoria> config={config} />;
}
