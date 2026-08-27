/**
 * Contrato frontend↔backend verificado en compile-time (regla 2026-07-29:
 * "el contrato deja de ser un acuerdo de caballeros").
 *
 * `lib/api-types.gen.ts` se genera del OpenAPI del backend:
 *   cd backend && python -m scripts.export_openapi && cd ../frontend && npm run gen:api
 *
 * Cada línea de abajo exige que TODO campo que el frontend espera leer exista
 * en la respuesta real del backend. Si el backend renombra o elimina un campo,
 * `tsc` truena AQUÍ (build roto) en vez de mostrar pantallas con huecos en
 * producción. Un error tipo `Type '"campo_x"' does not satisfy 'never'`
 * significa: el frontend lee `campo_x` pero el backend ya no lo manda.
 *
 * Los tipos hechos a mano en `lib/types.ts` siguen siendo la superficie que
 * usan las páginas; esto solo los mantiene honestos.
 */
import type { components } from "./api-types.gen";
import type {
  Almacen, Categoria, Cliente, ClienteExterno, Conexion, Devolucion, EsquemaImpuesto, Factura, FacturaDetail,
  LineaFactura, LineaOC, LineaOrdenCompra, LineaRemision, ListaPrecios, Membership,
  OCRecibida, OCRecibidaDetalle,
  OrdenCompra, OrdenCompraDetail, Precio, Producto, Proveedor, Remision,
  RemisionDetail, Role, Serie, Sucursal,
} from "./types";

type S = components["schemas"];

/** Campos que el frontend espera y el backend NO manda (debe ser `never`). */
type MissingIn<Local, Gen> = Exclude<keyof Local, keyof Gen>;
type Ok<T extends never> = T;

/* eslint-disable @typescript-eslint/no-unused-vars */
type _Remision = Ok<MissingIn<Remision, S["RemisionOut"]>>;
type _RemisionDetail = Ok<MissingIn<RemisionDetail, S["RemisionDetailOut"]>>;
type _LineaRemision = Ok<MissingIn<LineaRemision, S["LineaRemisionOut"]>>;
type _Devolucion = Ok<MissingIn<Devolucion, S["DevolucionOut"]>>;
type _Factura = Ok<MissingIn<Factura, S["FacturaOut"]>>;
type _FacturaDetail = Ok<MissingIn<FacturaDetail, S["FacturaDetailOut"]>>;
type _LineaFactura = Ok<MissingIn<LineaFactura, S["LineaFacturaOut"]>>;
type _Producto = Ok<MissingIn<Producto, S["ProductoOut"]>>;
type _Cliente = Ok<MissingIn<Cliente, S["ClienteOut"]>>;
type _Sucursal = Ok<MissingIn<Sucursal, S["SucursalOut"]>>;
type _Almacen = Ok<MissingIn<Almacen, S["AlmacenOut"]>>;
type _Categoria = Ok<MissingIn<Categoria, S["CategoriaOut"]>>;
type _Serie = Ok<MissingIn<Serie, S["SerieOut"]>>;
type _Proveedor = Ok<MissingIn<Proveedor, S["ProveedorOut"]>>;
type _Precio = Ok<MissingIn<Precio, S["PrecioOut"]>>;
type _ListaPrecios = Ok<MissingIn<ListaPrecios, S["ListaPreciosOut"]>>;
type _EsquemaImpuesto = Ok<MissingIn<EsquemaImpuesto, S["EsquemaImpuestoOut"]>>;
type _OrdenCompra = Ok<MissingIn<OrdenCompra, S["OrdenCompraOut"]>>;
type _OrdenCompraDetail = Ok<MissingIn<OrdenCompraDetail, S["OrdenCompraDetailOut"]>>;
type _LineaOrdenCompra = Ok<MissingIn<LineaOrdenCompra, S["LineaOCOut"]>>;
type _Membership = Ok<MissingIn<Membership, S["MembershipOut"]>>;
type _OCRecibida = Ok<MissingIn<OCRecibida, S["OCRecibidaOut"]>>;
type _OCRecibidaDetalle = Ok<MissingIn<OCRecibidaDetalle, S["OCRecibidaDetailOut"]>>;
type _LineaOC = Ok<MissingIn<LineaOC, S["LineaOCRecibidaOut"]>>;
type _ClienteExterno = Ok<MissingIn<ClienteExterno, S["ClienteExternoOut"]>>;
type _Conexion = Ok<MissingIn<Conexion, S["ConexionOut"]>>;
type _Role = Ok<MissingIn<Role, S["RoleOut"]>>;

export {};
