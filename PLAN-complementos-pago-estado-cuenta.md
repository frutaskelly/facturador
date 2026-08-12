# Plan — Complementos de Pago (REP) + Estado de cuenta del cliente

> Handoff para construir en la próxima sesión. Basado en: el **manual oficial de SAE/Aspel**
> (`/Users/michelzarate/Documents/Claude/Cristian/SAE-Updates/docs/sae/SAE_Manual_Oficial_Aspel.md`),
> ejemplos reales de complementos timbrados (`Revision/Facturas2026/PDF/COMPLEMENTO_DE_PAGO_*.pdf`),
> las decisiones del dueño (abajo) y el motor fiscal de v2 ya en producción (facturas, series,
> Facturama + bitácora de timbrado, envío en lote).

## Decisiones del dueño (2026-08-12)

1. **Solo PPD** (Pago en Parcialidades o Diferido). Las PUE nunca llevan REP.
2. **Flujo SAE**: se registra el **pago** primero, luego se **anexan las facturas** que cubre. Un pago
   puede cubrir varias facturas. Si cubre una factura **parcial**, se aplica como **abono** y deja
   **saldo insoluto** en el estado de cuenta del cliente.
3. Las **20 facturas PPD timbradas** en prod **aún no se pagan** → sin REP retroactivo. Arrancan con
   saldo insoluto = total; se pagan cuando toque.

## Cómo lo hace SAE (lo que replicamos)

- **Estado de cuenta** vive en *Clientes → Expediente del cliente → Información de saldos → pestaña
  Estado de cuenta*. Es **Cuentas por Cobrar**: **cargos** (facturas) y **abonos** (pagos aplicados),
  por documento, con sus conceptos asociados. Cancelar una factura con abonos exige *eliminar la
  cuenta* primero (hay acoplamiento factura ↔ CxC ↔ abonos).
- **REP (Complemento para Recepción de Pagos 2.0)**: timbrado **unitario** — uno a la vez, nunca
  masivo (en SAE el masivo solo registra administrativamente). Referencia el UUID de la(s) factura(s),
  con por-documento: moneda, num. parcialidad, **saldo anterior → importe pagado → saldo insoluto**.
- **Cancelación del REP** (regla SAT 2026): **siempre requiere aceptación** del receptor (positiva
  ficta a 3 días), aun ≤ $1,000. Estado "Sin aceptación" → "Con aceptación". Motivo 01 + relación 04
  para sustitución. (Lo mismo que ya maneja el flujo de cancelación de facturas de v2.)

## Diseño v2 (mapeado sobre lo que ya existe)

### Modelo (migración nueva)

- **`facturas.saldo_insoluto`** (Numeric): al timbrar una PPD arranca = `total`; baja con cada abono.
  0 = saldada. PUE se marca saldada de una vez (no aplica REP).
- **`recibos_pago`** (el pago recibido → REP): tenant, cliente, `fecha_pago`, `forma_pago` SAT
  (cómo pagó de verdad: 01 efectivo / 03 transferencia / 04 tarjeta), `monto`, `moneda`, `num_operacion`
  (referencia/folio bancario), `banco`. Campos fiscales del timbre: `estado` (BORRADOR/TIMBRADO/
  CANCELADO), `uuid`, `xml`, `facturama_id`, `serie`, `folio`, `fecha_timbrado`. `created_by`.
  → Reusa la **bitácora `timbrado_intentos`** (reconciliación anti-doble-timbrado) igual que facturas.
- **`recibo_pago_facturas`** (docto relacionado): `recibo_id`, `factura_id`, `importe_pagado`,
  `num_parcialidad`, `saldo_anterior`, `saldo_insoluto`, `moneda_dr`. La suma de `importe_pagado` de
  un recibo = `recibos_pago.monto`.
- RLS + grants por tenant en ambas tablas (patrón de todas las migraciones).
- Nota: la tabla `pagos` (del POS, caja/corte) se **mantiene aparte** — es efectivo de mostrador, no
  fiscal. El REP es su propio documento. (Enganche POS↔REP: fuera de alcance, ver Pendientes.)

### Backend

- **`services/facturama.py`**: método `create_cfdi_pago(payload)` para el CFDI tipo **P** (complemento
  2.0). **Confirmar el contrato exacto de la API de Facturama** (endpoint y forma del complemento)
  contra su documentación **antes de timbrar el primero** — igual que se hizo con las facturas.
- **`services/rep.py`**: arma el payload del complemento desde `recibos_pago` + sus facturas
  (Emisor del tenant, Receptor del cliente, `Pagos[].DoctoRelacionado[]` con IdDocumento=UUID,
  Serie/Folio, MonedaDR, NumParcialidad, ImpSaldoAnt, ImpPagado, ImpSaldoInsoluto, ObjetoImpDR).
- **`api/v1/recibos_pago.py`**:
  - `POST /recibos-pago` — registra el pago (BORRADOR): cliente, fecha, forma, monto, referencia +
    las facturas que cubre con importe por factura. Valida: todas PPD timbradas del mismo cliente,
    importe ≤ saldo insoluto de cada una, suma = monto. Calcula parcialidad y saldos.
  - `POST /recibos-pago/{id}/timbrar` — timbra el REP ante el PAC (con `FOR UPDATE` + bitácora, como
    facturas); al éxito, **descuenta el saldo insoluto** de cada factura y estampa UUID/XML.
  - `POST /recibos-pago/{id}/cancelar` — cancela ante el PAC (motivo/sustitución), **revierte** los
    saldos insolutos. Respeta la regla "requiere aceptación".
  - `GET /recibos-pago` + `GET /{id}` (con sus facturas), PDF y XML descargables/enviables (reusa el
    envío por correo en lote existente).
  - `GET /clientes/{id}/estado-cuenta` — **el estado de cuenta**: facturas PPD del cliente con
    total/saldo insoluto/estado, sus abonos (recibos aplicados), y el saldo total. Base para la vista.

### Frontend

- **Nueva pantalla `Cobranza / Recibos de pago`** (nav, permiso nuevo `menu:cobranza` sembrado a
  ADMIN/FACTURISTA):
  1. **Registrar pago** — cliente → fecha/forma/monto/referencia → tabla de sus facturas PPD con saldo,
     capturas cuánto aplicar a cada una (validación en vivo: no exceder saldo, la suma cuadra con el
     monto). Guarda BORRADOR.
  2. **Timbrar** — emite el REP; toasts con ambiente real (sandbox/producción) como en facturas.
  3. Lista de recibos con PDF/XML/enviar y cancelar (con el modal de motivo SAT + aviso de aceptación).
- **Estado de cuenta del cliente** — desde la pantalla de Clientes, acción "Estado de cuenta":
  facturas PPD con saldo pendiente, abonos aplicados, saldo total, **+ antigüedad de saldos estilo
  SAE** (ver abajo).

### Antigüedad de saldos — igual que SAE (decisión cerrada)

SAE la calcula por **fecha de VENCIMIENTO**, no por fecha de emisión:
- `fecha_vencimiento` de cada factura = `fecha_factura + dias_credito` del cliente (campo ya existe).
- `dias_vencida = hoy − fecha_vencimiento` (`DATEDIFF` en la consulta de CxC del manual, §BD-7).
- **Intervalos de 30 días** (el default de SAE, "Días por período"), con una columna **"Por vencer"**
  para lo que aún no vence (`fecha_vencimiento >= hoy`): **Por vencer · 1-30 · 31-60 · 61-90 · 90+**.
- Solo cuenta facturas **no saldadas** (`saldo_insoluto > 0`) a una **fecha de corte** (default hoy).
- Reporte en el Expediente (SAE lo pone en *Información de saldos → Antigüedad de saldos*), y como
  columna/resumen en el estado de cuenta del cliente.
- Contrato regenerado (`export_openapi` + `gen:api` + `api-contract.check.ts`) en cada fase.

## Fases (cada una: rama → tests → tsc/build → contrato → deploy)

1. **F1 — Modelo + estado de cuenta (S-M):** migración (saldo_insoluto, recibos_pago, junction, RLS);
   `saldo_insoluto` se inicializa al timbrar facturas PPD + backfill de las 20 actuales; endpoint y
   vista de **estado de cuenta** (solo lectura). Entrega valor inmediato sin tocar timbrado todavía.
2. **F2 — Registrar pago + REP (M-L, fiscal):** `services/rep.py` + Facturama `create_cfdi_pago`
   (contrato confirmado); registrar pago, timbrar REP, descontar saldos, PDF/XML/enviar. El corazón.
3. **F3 — Cancelación + cierre (S-M):** cancelar REP (revierte saldos, regla de aceptación) y pulido
   (antigüedad de saldos si se aprobó, envío en lote de recibos).

## Riesgos / cuidado (es timbrado real)

- **Confirmar el contrato de Facturama para CFDI de pago** antes de F2 — es el único desconocido.
- El **saldo insoluto es la fuente de verdad** del estado de cuenta: todo movimiento (timbrar/cancelar
  REP) debe ajustarlo dentro de la misma transacción con `FOR UPDATE`, como el kardex de inventario.
- **Acoplamiento cancelación factura ↔ abonos** (regla SAE A): no permitir cancelar una factura PPD que
  ya tiene abonos aplicados sin cancelar antes su(s) REP — validar en el `cancelar_factura` existente.
- Fecha de pago del REP: capturable (puede ser anterior a hoy) para reflejar el pago real.

## Referencias

- Manual SAE: `SAE-Updates/docs/sae/SAE_Manual_Oficial_Aspel.md` (§1.3 aceptación REP, §1.5 estados,
  §1.8-A estado de cuenta/CxC, §3.10-3.11 complementos y emisión).
- Complementos reales timbrados: `Revision/Facturas2026/PDF/COMPLEMENTO_DE_PAGO_*.pdf`.
- Memoria: [[timbrado-facturama]] (rechazos del PAC y sus fix), [[facturas-cruce-modulos]].
