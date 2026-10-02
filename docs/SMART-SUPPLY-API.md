# API del Facturador para el panel de Smart Supply

Contrato de `GET /api/v1/smart-supply/*`. Lo consume el sincronizador de
app.smartsupply.mx (repo `SmartSupply/web`), que guarda copias en su propia base
y nunca lee la del Facturador. Código: `backend/app/api/v1/smart_supply.py`,
alcance en `backend/app/services/smart_supply.py`, pruebas en
`backend/tests/test_smart_supply_api.py`.

## Autenticación y alcance

- Base: `https://api.facturador.mx/api/v1`.
- `Authorization: Bearer fi_ss_…`: una clave de conexión tipo
  **`SMART_SUPPLY_PANEL`**, una por cuenta (bodega/plaza). El dueño la genera y
  la revoca en Ajustes › Conexiones › «Smart Supply · panel». El Facturador
  guarda solo su SHA-256 y los últimos 4 caracteres.
- La clave trae un solo permiso: **`abasto:leer`**, de solo lectura. El router
  publica solo `GET`.
- Las claves del bot (`SMART_SUPPLY`) y de Mini Conta (`MINI_CONTA`) reciben
  403. Una persona OWNER sí lee, sin límite, y sirve para probar.
- Revocar o regenerar la clave corta en el siguiente request, porque las claves
  no se cachean. Una clave revocada da 401.
- Cloudflare rechaza el User-Agent por defecto de urllib (error 1010). Hay que
  mandar uno propio, por ejemplo `SmartSupplyPlataforma/1.0 (+app.smartsupply.mx)`.

Lo que comparte cada clave (`conexiones.alcance`, lo edita el dueño):

| Campo | Ejemplo (Tabasco) | Qué abre |
|---|---|---|
| `plaza` | `Tabasco` | El nombre de la cuenta, y el tope de los perfiles: una OC que entra por perfil solo se ve si es de esta plaza o si todavía no tiene. Es obligatoria si hay perfiles |
| `series` | `["ZEHMOVH"]` | Lo facturado: facturas de esas series |
| `series_remision` | `["RZEHMOVH"]` | Lo remisionado y las OC que se volvieron remisión de esas series |
| `perfiles` | `["EHMO:villahermosa"]` | Las OC de la plaza (o sin plaza) cuyo `origen_externo` empieza con `<perfil>:`, aunque no tengan remisión o se hayan descartado. Siempre en dos tramos, `canal:origen`: `MANUAL` o `EHMO` solos se rechazan con 422 |
| `remisiones` | `true` | `/remisionado` (si no, 403) |
| `oc` | `true` | `/oc` y `/oc-lineas` (si no, 403) |
| `catalogo` | `true` | `/catalogo` (si no, 403) |

Una clave del panel sin alcance no lee nada.

La plaza de una OC es la suya o, si no la trae, la de su remisión. El perfil no
alcanza para separar plazas: el mismo grupo de WhatsApp puede pedir para dos, y
`MANUAL:<uuid>` es el canal de captura a mano de toda la empresa. Por eso un
perfil solo abre las OC de la plaza de la clave. Una OC que todavía no tiene
plaza sí la ven todas las claves que tienen su perfil.

## Reglas comunes

- Las fechas son de México (`America/Mexico_City`). `desde` y `hasta` son
  inclusivas y el rango máximo es de **93 días** (si no, 422).
- **Paginación por llave, nunca por offset.** Cada respuesta es
  `{items, limit, siguiente}`. La página siguiente se pide con
  `despues=<siguiente>`, y `siguiente: null` marca la última. El cursor es
  opaco y un cursor inválido da 422.
- `limit` va de 1 a 5000 y por omisión es 1000.
- Las cantidades e importes viajan como texto decimal, sin ceros de relleno.
  Los importes son sin IVA.
- `kg` solo aparece cuando se sabe: la unidad es KILO, o es una presentación
  con factor de un producto cuya unidad base es KILO. Si no, va `null`.
  `kg_estimado` indica que el factor está marcado como estimado (por ejemplo, la
  PIEZA de sandía).
- `?series=` en `/facturado` y `/remisionado` recorta a esas series. Pedir una
  serie fuera del alcance da **403** explícito.

## Rutas

| Ruta | Parámetros | Llave y orden | Contenido |
|---|---|---|---|
| `GET /smart-supply/alcance` | — | — | `empresa{id,nombre}`, `conexion{id,nombre,pista}` (null si es persona), `sin_limite`, `plaza`, `series`, `series_remision`, `perfiles`, `remisiones`, `oc`, `catalogo`, `plazas[{nombre,series,series_remision,perfiles}]`, `zona_horaria`, `max_dias`, `max_limit`. Sirve para probar y guardar la clave. |
| `GET /smart-supply/oc` | `desde`+`hasta` sobre `campo` = `actualizado` (default), `recibida` o `entrega`; y/o `actualizado_desde` (ISO, ≤ 93 días; sin zona se toma UTC); `estado`, `canal` (csv) | (`actualizado_at`, `id`) | Una fila por OC, DESCARTADA incluida: `id, canal, origen_externo, perfil, folio_externo, remitente, archivo_nombre, recibida_at, fecha_entrega, estado, motivo, cliente_id, cliente, plaza, punto_entrega, proyecto, partidas, documento, cambio_abierto, cambio_resumen, remision{id,folio,serie,estado,fecha_entrega,total}, factura{id,serie,folio,uuid,estado,origen}, actualizado_at`. `actualizado_at = greatest(oc, remisión, factura).updated_at`. |
| `GET /smart-supply/oc-lineas` | `desde`, `hasta` (sobre la entrega) | (`oc_id`, `numero`) | Partidas del documento vigente: `payload_nuevo` si trae partidas, si no `payload`. Excepción: si el cambio se cerró porque el documento volvió a coincidir con la remisión, manda `payload`. Solo PENDIENTE y ASIGNADA. Campos: `oc_id, numero, documento, cambio_abierto, perfil, folio_externo, fecha_entrega, estado, cliente_id, plaza, remision_id, remision_folio, remision_estado, clave_doc, clave (normalizada), descripcion, unidad_doc, cantidad`. Van sin cruzar a producto; el cruce es la remisión. |
| `GET /smart-supply/remisionado` | `desde`, `hasta` (sobre `fecha_entrega`), `series` | (`remision_id`, `numero_linea`) | Líneas de remisiones no canceladas: `remision_id, numero_linea, folio, serie, estado, fecha_entrega, factura_id, facturada, su_pedido, cliente_id, cliente, plaza, punto_entrega, producto_id, sku, producto, clave (SAE de la presentación), clave_producto, presentacion, cantidad_solicitada, cantidad_surtida, cantidad (el peso real si es de peso variable), unidad, factor_kg, kg, kg_estimado, precio_unitario, importe (con el descuento del encabezado prorrateado)`. |
| `GET /smart-supply/facturado` | `desde`, `hasta`, `fecha` = `entrega` (default) o `factura`, `series` | (`factura_id`, `numero_linea`) | Líneas de facturas TIMBRADA tipo I, nativas y espejo: `factura_id, numero_linea, origen, espejo_empresa, serie, folio, uuid, fecha_factura, fecha_entrega, fecha_entrega_origen (remision\|notas\|factura), cliente_id, cliente, su_pedido, remision_ids[], producto_id, sku, producto, clave_sae, descripcion, presentacion, clave_unidad, unidad, cantidad, factor_kg, kg, kg_estimado, importe`. |
| `GET /smart-supply/catalogo` | — | `id` | Productos no borrados (activos y desactivados): `id, sku, nombre, categoria, unidad_base, unidad_sat, peso_variable, activo, clave_sae, presentaciones{PRES:{factor,clave_sae,sat,estimado}}`. No lleva precios ni costos. |

## Antes de encender el sincronizador: el backfill del espejo

`/facturado` devuelve el `producto_id` y la `presentacion` **tal como los guardó
el espejo**. El espejo los pone por clave SAE desde #313, pero solo en las
facturas que reenvía después del deploy. Las que ya estaban se quedan sin
producto hasta que corre el backfill. Medido el 2-oct-2026 en las facturas
espejo timbradas desde el 1-sep: ZEHMOVH tenía 3,339 de 5,736 líneas sin
producto y ninguna con presentación. En la muestra de Tabasco con entrega del 21
al 23-sep, el importe facturado casi cuadraba con lo remisionado (251,400 vs.
256,620), pero los kg salían 56 % abajo. Con eso la merma sale falsa.

El orden es obligatorio:

1. Desplegar #313 (el espejo que pone producto por clave).
2. Correr el backfill desde `backend/`, primero en seco. El dueño revisa la
   lista de claves gemelas marcadas con ⚠:

   ```sh
   ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave            # en seco
   ./.venv/bin/python -m scripts.backfill_espejo_producto_por_clave --aplicar
   ```

   `--recalcular-ligadas` es otra decisión del dueño; no hace falta para esto.
3. Comprobar con un SELECT que las líneas sin producto bajaron a ~1 %, que es lo
   que no casa por clave:

   ```sql
   SELECT f.serie, count(*) AS lineas,
          count(*) FILTER (WHERE l.producto_id IS NULL) AS sin_producto,
          round(100.0 * count(*) FILTER (WHERE l.producto_id IS NULL) / count(*), 1) AS pct
   FROM facturas f JOIN lineas_factura l ON l.factura_id = f.id
   WHERE f.origen = 'ESPEJO_SAE' AND f.estado = 'TIMBRADA' AND f.tipo_comprobante = 'I'
     AND f.deleted_at IS NULL AND f.fecha >= now() - interval '93 days'
   GROUP BY f.serie ORDER BY lineas DESC;
   ```

4. Solo entonces el sincronizador empieza a leer `/facturado`. Si ya había
   leído antes, tiene que recargar la ventana completa de 93 días. La ventana
   móvil de cada 30 minutos no alcanza lo que el backfill ligó en días viejos.

El sincronizador debe enseñar como calidad del dato el porcentaje de líneas con
`producto_id: null` de cada ventana. Ya viene en cada línea, así que no hace
falta un contador aparte. Si pasa de unos pocos puntos, la merma de esos días
no es confiable.

Notas de datos:

- En `/facturado`, el `producto_id` y la `presentacion` son **los que guardó la
  línea**. El espejo los pone por clave SAE desde #313
  (`services/espejo_productos.py`). Aquí no se recalculan, porque dos copias de
  la regla darían dos respuestas distintas. Una partida que no cruzó sale con
  `producto_id: null` y su `clave_sae`.
- La fecha de entrega del facturado sale de la remisión ligada (la primera), si
  no de las notas de la factura y, si tampoco, de la fecha de la factura. Es la
  misma regla que usa Mini Conta (`services/mini_conta.fechas_de_entrega`).
- La llave del facturado es `(factura_id, numero_linea)`. El espejo borra y
  recrea las líneas en cada reenvío, así que el `id` de línea no sirve de
  llave.
- `actualizado_at` solo se mueve con escrituras del ORM. Lo que se corrige por
  SQL directo (backfills, scripts) no lo mueve. Por eso el sincronizador tiene
  que reemplazar por ventana de vez en cuando (por ejemplo, de noche:
  `campo=recibida` sobre 93 días).

## Cómo lo usa el sincronizador (sugerido)

1. Para guardar la clave, `GET /smart-supply/alcance`: si responde, se guarda
   cifrada (Vault) con su `pista`.
2. Cada 10 minutos, `GET /smart-supply/oc?actualizado_desde=<último − 15 min>`:
   upsert por `id` y reemplazo de las `oc-lineas` de esas OC.
3. Cada 30 minutos, `facturado` y `remisionado` sobre una ventana móvil. Hay que
   **leer todas las páginas antes de borrar** y reemplazar la ventana en una
   sola transacción.
4. De noche, `catalogo` completo y la ventana de 93 días de todo lo demás.
5. Un 401 deja la conexión en error sin borrar la copia anterior.
