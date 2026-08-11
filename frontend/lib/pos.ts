// POS — tipos y helpers compartidos por las estaciones y Ajustes › Punto de venta.
// El flujo es CONFIGURACIÓN por tenant (tenants.config.pos): el ORDEN de
// `etapas` es el flujo real, "pedido" siempre va primero, y se pueden agregar
// etapas propias (etapas_custom) que declaran qué rol las trabaja.

export const ETAPAS_CANONICAS = ["pedido", "caja", "almacen", "salida"] as const;
export type EtapaCanonica = (typeof ETAPAS_CANONICAS)[number];

export const ETAPA_LABEL: Record<EtapaCanonica, string> = {
  pedido: "Pedido",
  caja: "Caja",
  almacen: "Almacén",
  salida: "Salida",
};

export const ETAPA_DESC: Record<EtapaCanonica, string> = {
  pedido: "Captura del pedido (siempre va primero)",
  caja: "Cobro contado/crédito y ticket",
  almacen: "Surtido de mercancía",
  salida: "Entrega al cliente",
};

// Permisos de acción del seed: quién trabaja una etapa custom.
export const PERMISOS_ETAPA = [
  { value: "pedido:capturar", label: "Tomador (captura)" },
  { value: "pedido:cobrar", label: "Cajero (cobro)" },
  { value: "pedido:surtir", label: "Bodeguero (operación)" },
  { value: "pedido:entregar", label: "Repartidor (entrega)" },
] as const;

export type EtapaCustom = { id: string; nombre: string; permiso: string };

export type PosConfig = {
  activo: boolean;
  etapas: string[];                    // ORDEN = flujo real (ids canónicos y custom)
  etapas_custom: EtapaCustom[];
  credito: boolean;
  inventario_sale_en: string;          // id de etapa del flujo | "crear"
  serie_id: string | null;
  permitir_sobregiro: boolean;
  ticket: { formato: "80mm" | "carta"; auto_imprimir: boolean };
  // Solo en GET (derivados por el backend para el usuario actual):
  etapas_visibles?: string[];
  etiquetas?: Record<string, string>;
  puede_configurar?: boolean;
};

/** Etiqueta de una etapa: canónica, custom o el id tal cual. */
export function etiquetaDe(cfg: PosConfig, etapa: string): string {
  if ((ETAPAS_CANONICAS as readonly string[]).includes(etapa)) {
    return ETAPA_LABEL[etapa as EtapaCanonica];
  }
  return cfg.etapas_custom.find((c) => c.id === etapa)?.nombre ?? cfg.etiquetas?.[etapa] ?? etapa;
}

/** Slug para el id de una etapa nueva a partir de su nombre. */
export function slugEtapa(nombre: string): string {
  return nombre
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 30);
}
