// POS — tipos y helpers compartidos por las estaciones y Ajustes › Punto de venta.
// El flujo es CONFIGURACIÓN por tenant (tenants.config.pos): el orden de etapas
// es fijo, lo configurable es cuáles están prendidas ("pedido" siempre).

export const ETAPAS_ORDEN = ["pedido", "caja", "almacen", "salida"] as const;
export type Etapa = (typeof ETAPAS_ORDEN)[number];

export const ETAPA_LABEL: Record<Etapa, string> = {
  pedido: "Pedido",
  caja: "Caja",
  almacen: "Almacén",
  salida: "Salida",
};

export const ETAPA_DESC: Record<Etapa, string> = {
  pedido: "Captura del pedido (siempre activa)",
  caja: "Cobro contado/crédito y ticket",
  almacen: "Surtido de mercancía",
  salida: "Entrega al cliente",
};

export type PosConfig = {
  activo: boolean;
  etapas: Etapa[];
  credito: boolean;
  inventario_sale_en: "cobro" | "surtido" | "entrega";
  serie_id: string | null;
  permitir_sobregiro: boolean;
  ticket: { formato: "80mm" | "carta"; auto_imprimir: boolean };
  // Solo en GET (derivados por el backend para el usuario actual):
  etapas_visibles?: Etapa[];
  puede_configurar?: boolean;
};
