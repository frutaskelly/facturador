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
  AlcancePanel, Almacen, Categoria, Cliente, ClienteExterno, Conexion, ConexionCambio, Devolucion, EsquemaImpuesto, Factura, FacturaDetail,
  LineaFactura, LineaOC, LineaOrdenCompra, LineaRemision, ListaAsignacion, ListaPrecios, Membership,
  OCRecibida, OCRecibidaDetalle, OpcionesPanel,
  OrdenCompra, OrdenCompraDetail, Precio, Producto, Proveedor, Proyecto, Remision,
  RemisionDetail, Role, Serie, Sucursal,
} from "./types";
import type {
  AltaSaePedida, AltaSaeResumen, ArticuloSae, CambioSaePedido, ClaveBuscada, ClaveSaeEstado,
  EmpresaEnSae, UnidadSae,
} from "@/components/UnidadesClavesSae";

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
type _ListaAsignacion = Ok<MissingIn<ListaAsignacion, S["ListaAsignacionOut"]>>;
type _Proyecto = Ok<MissingIn<Proyecto, S["ProyectoOut"]>>;
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
type _AlcancePanel = Ok<MissingIn<AlcancePanel, S["AlcancePanel"]>>;
type _OpcionesPanel = Ok<MissingIn<OpcionesPanel, S["OpcionesPanelOut"]>>;
type _ConexionCambio = Ok<MissingIn<ConexionCambio, S["ConexionCambioOut"]>>;
type _Role = Ok<MissingIn<Role, S["RoleOut"]>>;

// Unidades y claves SAE del editor de producto (2-oct-2026). Sus tipos viven en
// el componente, no en `types.ts`, pero leen y MANDAN campos del backend: lo que
// se manda también se verifica, porque un campo que el backend no conoce se
// pierde en silencio (pydantic lo ignora) y el alta sale sin él.
type _ClaveBuscada = Ok<MissingIn<ClaveBuscada, S["ClaveSaeBuscadaOut"]>>;
type _ArticuloSae = Ok<MissingIn<ArticuloSae, S["ArticuloSaeOut"]>>;
type _EmpresaEnSae = Ok<MissingIn<EmpresaEnSae, S["ArticuloSaeEmpresaOut"]>>;
type _ClaveSaeEstado = Ok<MissingIn<ClaveSaeEstado, S["ClaveSaeEstadoOut"]>>;
type _SolicitudSae = Ok<MissingIn<NonNullable<ClaveSaeEstado["solicitud"]>, S["SolicitudSaeResumenOut"]>>;
type _AltaSaeResumen = Ok<MissingIn<AltaSaeResumen, S["AltaSaeOut"]>>;
type _AltaSaePedida = Ok<MissingIn<AltaSaePedida, S["AltaSaeProductoIn"]>>;
// Una unidad que el backend no acepta en `altas_sae` sería un 422 al guardar.
type _UnidadSae = Ok<Exclude<UnidadSae, S["AltaSaeProductoIn"]["unidad"]>>;
type _CambioSaePedido = Ok<MissingIn<CambioSaePedido, S["CambioSaeIn"]>>;

export {};
