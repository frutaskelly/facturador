"use client";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import { apiFetch } from "@/lib/api";
import { fmtNumber } from "@/lib/format";
import type { Almacen, ExistenciaRow } from "@/lib/types";

const config: CrudConfig<Almacen> = {
  title: "Almacenes",
  subtitle: "Almacenes / puntos de stock",
  newLabel: "Nuevo almacén",
  basePath: "/api/v1/almacenes",
  writePerm: "almacen:gestionar",
  deletePerm: "almacen:eliminar",
  searchable: false,
  columns: [
    { header: "Código", cell: (a) => <span className="font-medium">{a.codigo}</span> },
    { header: "Nombre", cell: (a) => a.nombre },
    {
      header: "Domicilio",
      cell: (a) =>
        [a.calle, a.colonia, a.ciudad, a.estado].filter(Boolean).join(", ") || "—",
    },
    { header: "C.P.", cell: (a) => a.cp ?? "—" },
    { header: "Predeterminado", cell: (a) => (a.es_default ? <Badge tone="accent">Sí</Badge> : "—") },
  ],
  fields: [
    { name: "codigo", label: "Código", readonly: true, hint: "Se genera automáticamente" },
    { name: "nombre", label: "Nombre", required: true },
    { name: "calle", label: "Calle y número", colSpan: 2 },
    { name: "colonia", label: "Colonia" },
    { name: "cp", label: "C.P. (Lugar de expedición CFDI)" },
    { name: "ciudad", label: "Ciudad / Municipio" },
    { name: "estado", label: "Estado" },
    { name: "es_default", label: "Almacén predeterminado", type: "switch" },
  ],
  newValues: () => ({ codigo: "", nombre: "", calle: "", colonia: "", cp: "", ciudad: "", estado: "", es_default: false }),
  toForm: (a) => ({
    codigo: a.codigo,
    nombre: a.nombre,
    calle: a.calle ?? "",
    colonia: a.colonia ?? "",
    cp: a.cp ?? "",
    ciudad: a.ciudad ?? "",
    estado: a.estado ?? "",
    es_default: a.es_default,
  }),
  toPayload: (v) => ({
    // `codigo` lo autogenera el backend (ALM-NNN); no se envía.
    nombre: v.nombre,
    calle: (v.calle as string) || null,
    colonia: (v.colonia as string) || null,
    cp: (v.cp as string) || null,
    ciudad: (v.ciudad as string) || null,
    estado: (v.estado as string) || null,
    es_default: v.es_default,
  }),
  rowLabel: (a) => a.nombre,
  // Advierte si el almacén tiene existencias (eliminarlo desconectaría el inventario).
  deleteWarning: async (a) => {
    try {
      const rows = await apiFetch<ExistenciaRow[]>(`/api/v1/inventario/existencias?almacen_id=${a.id}`);
      const total = rows.reduce((s, r) => s + Number(r.disponible), 0);
      const prods = rows.filter((r) => Number(r.disponible) > 0).length;
      if (total > 0) {
        return (
          `"${a.nombre}" tiene inventario activo: ${prods} producto(s) con ${fmtNumber(total)} unidades. ` +
          `Si lo eliminas, ese inventario quedará desconectado y desaparecerá de las existencias. ` +
          `Alternativa recomendada: transfiere el inventario a otro almacén (o ajústalo a 0) antes de eliminarlo.`
        );
      }
    } catch {
      /* si falla la consulta, no bloquea el diálogo normal */
    }
    return null;
  },
};

export default function Page() {
  return <CrudPage<Almacen> config={config} />;
}
