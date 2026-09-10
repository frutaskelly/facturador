"use client";

import { DataTable, type Column, type DataTableProps, type RowAction } from "./DataTable";

export type { Column, DataTableProps, RowAction };

/**
 * DataTableSmart — la DataTable «con todo encendido».
 *
 * Es la misma tabla que {@link DataTable} (búsqueda, orden, menú de columnas,
 * redimensionado estilo Excel, exportar a CSV y paginado) pero con esas
 * funciones activadas por defecto, más el soporte de **filas expandibles**
 * (slide-down): al hacer clic en una fila se despliega debajo el contenido de
 * `renderExpanded`.
 *
 * Si le pasas `actions`, renderiza una columna de íconos por fila con su menú ⋮
 * (puntos) para reordenarlos y mostrarlos/ocultarlos.
 *
 * Todo es configurable: cualquier prop que pases gana sobre el valor por
 * defecto (p. ej. `searchable={false}` apaga el buscador). Para que el
 * redimensionado/orden/visibilidad de columnas/íconos se recuerden entre
 * visitas, pásale un `storageKey`.
 */
export function DataTableSmart<T>({
  searchable = true,
  headerFilters = true,
  columnsMenu = true,
  resizable = true,
  exportable = true,
  paginated = true,
  actionsMenu = true,
  ...props
}: DataTableProps<T>) {
  return (
    <DataTable
      searchable={searchable}
      headerFilters={headerFilters}
      columnsMenu={columnsMenu}
      resizable={resizable}
      exportable={exportable}
      paginated={paginated}
      actionsMenu={actionsMenu}
      {...props}
    />
  );
}
