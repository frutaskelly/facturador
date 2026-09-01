"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { ProductosDelGrupo } from "@/components/ProductosDelGrupo";
import { Badge } from "@/components/ui/Badge";
import type { Categoria } from "@/lib/types";

const config: CrudConfig<Categoria> = {
  title: "Categorías",
  subtitle: "Categorías de productos",
  newLabel: "Nueva categoría",
  basePath: "/api/v1/categorias",
  writePerm: "categoria:gestionar",
  deletePerm: "categoria:eliminar",
  searchable: false,
  columns: [
    { header: "Código", cell: (c) => <span className="font-medium">{c.codigo}</span> },
    { header: "Nombre", cell: (c) => c.nombre },
    { header: "Productos", className: "text-right tabular-nums",
      cell: (c) => (c.productos ? c.productos : <span className="text-muted">0</span>) },
    { header: "Estado", cell: (c) => <Badge tone={c.activo ? "success" : "muted"}>{c.activo ? "Activo" : "Inactivo"}</Badge> },
  ],
  // Clic en el renglón: los productos de la categoría, editables ahí mismo.
  renderExpanded: (c) => <ProductosDelGrupo filtro={{ categoria_id: c.id }} canWrite />,
  // Catálogo sugerido para distribución de alimentos: el usuario solo palomea
  // lo que usa, en vez de teclear una por una. La descripción es opcional.
  suggestions: {
    label: "Catálogo sugerido",
    title: "Categorías sugeridas",
    hint: "Elige las que uses en tu negocio. Podrás editarlas o agregar más después.",
    keyOf: (c) => c.nombre.trim().toLowerCase(),
    items: [
      // «Sin categorizar» NO va aquí: la crea el sistema en cada empresa y es
      // donde caen los productos que se dan de alta sin elegir categoría
      // (ver services/catalogos_default.py). Ofrecerla como sugerencia sería
      // pedir que se dé de alta algo que ya existe.
      ["Frutas y Verduras", "Fruta, verdura y hortaliza fresca de temporada"],
      ["Abarrotes", "Despensa y productos secos"],
      ["Lácteos", "Leche, queso, crema y yogurt"],
      ["Carnes", "Res, cerdo, pollo y pavo"],
      ["Embutidos", "Jamón, salchicha, tocino y salami"],
      ["Pescados y mariscos", "Producto del mar fresco y congelado"],
      ["Panadería", "Pan, tortilla y repostería"],
      ["Bebidas", "Agua, refrescos y jugos"],
      ["Botanas y dulces", "Frituras, galletas dulces y confitería"],
      ["Congelados", "Producto que requiere cadena de frío"],
      ["Enlatados y conservas", "Producto enlatado o en conserva"],
      ["Granos y semillas", "Frijol, arroz, lenteja y semillas"],
      ["Especias y condimentos", "Sal, especias, salsas y aderezos"],
      ["Aceites y grasas", "Aceite, manteca y mantequilla"],
      ["Limpieza", "Productos de limpieza e higiene"],
      ["Desechables", "Platos, vasos, cubiertos y servilletas"],
      ["Papelería", "Artículos de oficina y papelería"],
    ].map(([nombre, descripcion]) => ({
      key: nombre.trim().toLowerCase(),
      nombre,
      descripcion,
      payload: { nombre, descripcion, activo: true },
    })),
  },
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
