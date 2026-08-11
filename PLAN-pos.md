# Plan — Punto de Venta (POS) v2 con flujo configurable

> Handoff para construir el POS en sesiones siguientes. Basado en el análisis del POS
> de v1 (`/Users/michelzarate/Documents/Archive/Smart Supply/cadena-de-suministro-ai`,
> `backend/app/api/v1/pos_workflow.py` + `frontend-next/app/pos/*`) y en el motor de
> remisiones/facturas que v2 ya tiene en producción (2026-07-30).

## Qué tenía v1 (lo bueno que se conserva)

- **Multi-estación (POV por rol)**: Pedido (captura) → Caja (cobro) → Almacén (surtido)
  → Salida (entrega) + Operaciones (tablero de supervisión con stepper de 5 pasos).
- **Máquina de estados sobre la remisión**: `PENDIENTE_PAGO → PAGADO → EN_SURTIDO →
  LISTO_ENTREGA → ENTREGADO` (+ CANCELADO pre-listo). Cada transición valida el estado
  actual, aplica side-effects (crédito/inventario/folio), estampa `asignado_<etapa>_id`
  + timestamp, y hace broadcast WebSocket al canal de la siguiente etapa (redis pub/sub).
- **Cobro contado/crédito**: `monto_efectivo + monto_credito = total`; el crédito valida
  límite y carga al `saldo_actual` del cliente (servicio `credito.py`).
- **Colas por etapa** (`GET /pos/colas/{etapa}`): cada estación ve solo lo suyo.
- **Captura con borradores multi-pestaña** (varios pedidos abiertos a la vez).
- Componentes: `PosNav`, `PosHeader`, `PosDetalleModal`, `PosTracking`, `ClienteSearchBar`,
  `lib/pos` + `lib/pos_workflow`. ~4,000 líneas front + ~1,050 back.

**Limitación de v1 que v2 corrige:** el pipeline era FIJO. El requisito nuevo es que el
flujo se configure por cliente/tenant.

## Diseño v2 — el flujo es CONFIGURACIÓN, no código

### El setting (corazón del plan)

`tenants.config.pos` (JSONB ya existente; mismo patrón que `correo`) + página
**Ajustes › Punto de venta**:

```json
{
  "activo": true,
  "etapas": ["pedido", "caja", "almacen", "salida"],
  "credito": true,
  "inventario_sale_en": "surtido",      // "cobro" | "surtido" | "entrega"
  "serie_id": null,                      // serie para folios POS (null = resolver normal)
  "permitir_sobregiro": false,
  "ticket": { "formato": "80mm", "auto_imprimir": false }
}
```

- **Cliente chico (mostrador)**: `etapas: ["pedido","caja"]` → capturas, cobras,
  entregas en el momento; el inventario sale al cobrar.
- **Operación completa (bodega)**: las 4 etapas; el inventario sale al surtir o entregar.
- "pedido" es obligatoria; el resto se prende/apaga. La UI de Ajustes muestra el
  pipeline resultante como preview visual y valida combinaciones.
- El backend deriva la máquina de estados DEL CONFIG: `siguiente_etapa(config, actual)`.
  Un solo motor genérico de transiciones (`POST /pos/remisiones/{id}/avanzar`), no un
  endpoint por transición como v1 — así agregar/quitar etapas no toca código.

### Mapeo sobre el motor v2 existente (regla: NO duplicar motores)

El documento POS **es una remisión** (como v1), montada sobre lo que ya está en prod:

| Necesidad POS | Pieza v2 que se reusa tal cual |
|---|---|
| Documento + folio limpio | `Remision` + series (contador atómico, sin ceros) |
| Salida de inventario | `reservar_stock_remision` (salida directa, decisión #2) + sobregiro con autorización (#4/#5) |
| Impuestos/totales | `calcular_linea_producto` + `/remisiones/preview-totales` (cerebro único) |
| Captura de líneas | `ProductoCombobox`, `KeyboardCombobox`, `lib/lineas` (LineaForm, cruce IA, pegado) |
| Devoluciones | `POST /remisiones/{id}/devolucion` (ajusta a lo neto) |
| Facturar lo vendido | Facturar/timbrar/enviar EN LOTE (ya en prod) — el POS no factura, alimenta |
| Ticket/nota imprimible | `remision_pdf` (nueva variante ticket 80 mm) |
| Realtime | Redis del compose (puesto ahí para esto) + patrón de locks `FOR UPDATE` |
| UI base | `DataTableSmart`, `Modal` (a11y), `ConfirmDialog`, `Badge`, tokens del design system |
| Contrato | `export_openapi` + `gen:api` + `api-contract.check.ts` en cada fase |

Nuevo en el modelo (migración): columnas POS en `remisiones` — `pos_etapa` (nullable:
null = remisión normal, no-POS), `pos_asignaciones` JSONB (`{etapa: {user_id, at}}`) —
y tabla `pagos` (v1 la tenía: cliente, fecha, monto, forma_pago, banco, referencia,
remision_id) + permisos RBAC `menu:pos`, `pos:pedido`, `pos:caja`, `pos:almacen`,
`pos:salida` (seeds a presets).

Los acumuladores dormidos de `clientes` (`saldo_actual`, `ventas_ytd`, `ultima_venta_at`,
`ultimo_pago_at`) por fin se activan con el cobro/crédito — estaban esperando esto.

## Fases (cada una: rama → tests → tsc/build → contrato regenerado → deploy)

### Fase 0 — Config + esqueleto (S, ~½ día)
- Setting `pos` en `tenants.config` + `GET/PUT /pos/config` + página Ajustes › Punto de
  venta (toggles de etapas con preview del pipeline, crédito, timing de inventario, serie).
- Migración: `pos_etapa`/`pos_asignaciones` en remisiones, tabla `pagos`, permisos+seeds.
- `PosNav` v2: pestañas dinámicas según etapas activas + permisos del usuario.
- Motor `siguiente_etapa(config)` + `POST /pos/remisiones/{id}/avanzar` +
  `GET /pos/cola/{etapa}` (validando etapa ∈ config).

### Fase 1 — Estación Pedido (M, ~1 día)
- `/pos/pedido`: captura rápida — cliente (combobox con correos/precios), líneas con
  `lib/lineas` completo (cruce IA + pegado Excel gratis), preview fiscal del servidor,
  **borradores multi-pestaña** (patrón v1), historial del día.
- Crear pedido → remisión BORRADOR con `pos_etapa` = primera etapa post-pedido del
  config (o ciclo completo si solo hay pedido+caja: cobrar cierra y entrega).
- Si `inventario_sale_en` la alcanza aquí, aplica salida (con popup de sobregiro).

### Fase 2 — Estación Caja (M-L, ~1–1.5 días)
- Servicio `credito.py` v2 (validar límite → `clientes.limite_credito`; aplicar
  cargo/abono a `saldo_actual`, estampar `ultima_venta_at`/`ultimo_pago_at`).
- `POST /pos/remisiones/{id}/cobrar` con `FOR UPDATE` (patrón de la auditoría):
  efectivo+tarjeta+transferencia+crédito = total; crea filas `pagos`; cambio calculado.
- UI Caja: cola en vivo, modal de cobro con desglose y cambio, ticket 80 mm
  (variante de `remision_pdf`) con auto-print opcional.
- **Corte de caja**: `GET /pos/corte?fecha&usuario` (suma pagos por forma) + vista.

### Fase 3 — Estaciones Almacén y Salida (M, ~1 día)
- Almacén: cola de surtido, checklist por línea, peso real (catch-weight — `pesos` ya
  existe en confirmar), "marcar listo".
- Salida: cola de entrega, quién recoge, "entregar" (si `inventario_sale_en: entrega`,
  aquí corre la salida). Al cerrar el ciclo la remisión queda CONFIRMADA normal →
  entra al flujo de facturación en lote existente.

### Fase 4 — Operaciones + realtime (M, ~1 día)
- Tablero Operaciones: stepper por pedido (v1 `PosTracking`), filtros server-side,
  métricas del día (pedidos, cobrado por forma, pendientes por etapa).
- WebSocket (`/ws/pos/{etapa}`) con redis pub/sub para colas sin recargar;
  **fallback a polling** (las colas de F1–F3 nacen con polling de 10 s vía
  `useResource`, así el realtime es mejora, no dependencia).

## Decisiones de negocio a confirmar (antes de la fase que las toca)

1. **F0**: ¿etapas con nombres/orden fijos (pedido→caja→almacén→salida, prendibles) o
   también orden configurable (p. ej. surtir antes de cobrar)? Recomendado: orden fijo
   prendible en v1 del POS; orden libre después si un cliente real lo pide.
2. **F2**: formas de pago a soportar en caja (efectivo/tarjeta/transferencia/crédito) y
   si el corte de caja exige "fondo inicial" declarado.
3. **F2**: ¿límite de crédito por cliente se captura en Clientes (campo nuevo) o global?
4. **F3**: ticket no fiscal — ¿leyenda/formato legal que quieras (como el "NO FISCAL"
   de la remisión)?

## Referencias v1 (leer antes de cada fase)

- Backend: `backend/app/api/v1/pos_workflow.py` (1,051 líneas — transiciones, colas,
  progreso), `services/credito.py`, `services/pos_ws.py`, modelo `Pago` (factura.py:128).
- Frontend: `app/pos/{pedido,caja,almacen,salida,operaciones}/page.tsx`,
  `components/Pos{Nav,Header,DetalleModal,Tracking}.tsx`, `lib/pos.ts`, `lib/pos_workflow.ts`.
