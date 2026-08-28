# PLAN — Migración del Master (Google Sheets) al Facturador

> Elaborado 2026-08-28 tras análisis exhaustivo verificado contra el código real de los
> dos repos (Facturador @ e5c2d32, bot @ b036c65), la BD de producción (Supabase, solo
> lectura), los Excel masivos reales de SAE (~/Downloads) y la documentación SAE
> (Cristian/SAE-Updates). Sustituye la parte de intake del
> `PLAN-migracion-espejo-smartsupply.md` (13-ago, repo del bot) — la bandeja de OC que ese
> plan pedía ya existe y está en prod; este plan la pone a operar.

## El objetivo

**El Facturador se vuelve el sistema PRIMARIO de recepción y edición de órdenes.**
El Master de Google Sheets sigue corriendo como respaldo (el bot no deja de escribirlo).
Se factura en SAE durante toda la transición: el Facturador genera el Excel masivo de
facturas para SAE y las facturas viven en el Facturador solo como **espejo** de SAE.
Tras ~1 semana estable con todos los clientes, se apaga la facturación en SAE y el
Facturador timbra directo (flujo ya construido: factura → timbre → PDF/XML → REP).

Orden de clientes: **1) Balles + Jubran completo → 2) EHMO + MAFAN → 3) semana de
estabilidad → 4) corte de facturación.**

### Reglas de oro (no negociables durante la transición)

1. **El bot escribe el Master PRIMERO y el Facturador después** (ya es así en
   `cmd_add`): si el Facturador se cae, la operación no se detiene.
2. **Las facturas de clientes espejeados solo existen como ESPEJO** (decisión del dueño
   28-ago: quiere verlas en el Facturador, con sus anotaciones, guardadas en la BD, para
   estados de cuenta y para que el equipo se adapte al flujo completo — sin timbrar
   nada). Una factura con `origen = ESPEJO_SAE` tiene **candados duros**: nunca llama al
   PAC (ni timbrar, ni cancelar ante Facturama, ni REP fiscal). Y mientras dure la
   transición, un **candado por cliente espejeado** impide crear/timbrar facturas
   normales de esos clientes por accidente. Eso es lo que elimina el doble CFDI.
3. **NADIE escribe a la BD de SAE** — ni el Facturador ni el conector (los contadores
   de SAE ya rompieron la facturación dos veces). El conector SAE es SOLO LECTURA; la
   escritura hacia SAE es únicamente el Excel masivo importado a mano en Aspel.
4. **Folios de factura los sigue numerando SAE** hasta el corte de cada cliente.

---

## Estado verificado (2026-08-28)

### Lo que YA existe y funciona

| Pieza | Estado | Evidencia |
|---|---|---|
| Bandeja `/oc` completa: ingesta idempotente, resolución de cliente por equivalencias, remisión manual y de un clic con guardas estrictas | ✅ en prod | `oc_recibidas.py`, 28 tests |
| Bot manda OCs de Balles/Jubran (PDF) a la bandeja tras escribir el Master | ✅ cable puesto | `sheets_push.py:1117-1153` |
| Conexión `fi_ss_` activa (1 clave, último uso 27-ago) | ✅ | tabla `conexiones` |
| 13 grupos WhatsApp sincronizados (8 activos) | ✅ | `grupos_whatsapp` |
| Clientes Balles/Jubran/EHMO/MAFAN con RFC real, G01/99/PPD y series propias (ZHGO/RZHGO compartida Balles+Jubran; ZEHMOHOS; ZMAFAN) | ✅ | BD prod |
| 65 equivalencias CONFIRMADAS (36 UBICACION de Tabasco, 12 WHATSAPP, 6 PROYECTO…) | ✅ | `cliente_externos` |
| producto_clientes: Jubran 508, Balles 469, EHMO 64, MAFAN 40 (claves SAE por cliente → NoIdentificacion del CFDI) | ✅ | BD prod |
| Lista de precios Balles y Jubran (508 precios) asignada a Balles, Jubran y MAFAN | ✅ | `lista_asignaciones` |
| Import de Excel SAE/Master → remisiones (folios sin ceros, fechas serial) | ✅ | `importar_remisiones.py`, PR #32 |
| Espejo a nivel remisión: `factura_sae` + `su_pedido` + estado RESERVADO; 63 remisiones importadas de SAE (52 Balles, 11 Jubran) ya lo usan | ✅ | migr. 0051/0052 |
| Timbrado REAL Facturama en prod (multiemisor ON), REP/cobranza F1-F3, estado de cuenta con antigüedad, facturación por lote | ✅ construido | `.env.prod`, `cobranza.py` |
| El bot genera hoy los Excel masivos SAE (pedido 22 col, factura 27 col con folio real leído de SAE en vivo) | ✅ en el bot | `cmd_massivo`, `cmd_massivo_factura`, `_xls_massivo` |

### Los huecos (esto es lo que hay que construir/sembrar)

| # | Hueco | Impacto | Dónde se cierra |
|---|---|---|---|
| H1 | **El Facturador NO genera ningún Excel masivo para SAE** (solo importa; verificado con grep exhaustivo) | Bloquea el objetivo 1 | Etapa 1.3 |
| H2 | **Faltan equivalencias SAE `02:7`→Balles y `02:6`→Jubran** (solo existen 02:4, 02:5, 03:1) y **NOMBRE `JUBRAN`** (lo único que lo distingue de Balles en su grupo compartido) | OCs de Jubran quedan ambiguas; el export no puede poner el nº de cliente | Etapa 0.1 |
| H3 | **Sin outbox bot→Facturador**: si la API está caída, la OC queda solo en el Master y nunca llega a la bandeja (3 reintentos inmediatos; reenviar el PDF muere en el dedup antes del bloque del Facturador) | El "primario" pierde órdenes en silencio | Etapa 0.2 |
| H4 | **La bandeja no permite editar cantidad/presentación ni agregar/quitar partidas** (solo producto y precio); sin filtros por cliente/fecha ni paginación (limit=200) | El flujo diario de captura/corrección no es operable | Etapa 0.3 |
| H5 | **EHMO/MAFAN (pipeline de fotos) no manda nada al Facturador** (verificado: 0 referencias en `ehmo_pedidos.py`) | Fase 2 imposible sin esto | Etapa 2.1 |
| H6 | **EHMO sin lista de precios** en el Facturador; sus listas reales viven por PROYECTO (xlsx del bot); tabla `proyectos` vacía; sin series de Tabasco (ZEHMOVH, SAE empresa 03) | Partidas EHMO sin precio; Tabasco sin serie | Etapa 2.2 |
| H7 | El bot manda `perfil='ehmo'` por default incluso para grupos Balles; el directorio de grupos solo se sincroniza al "conectar" | Namespacing de equivalencias sucio; pantalla Conexiones desactualizada | Etapa 0.2 |
| H8 | Almacén ALM-01 no es default; inventario en cero (62 remisiones RESERVADAS sin stock) | Rompe flujos que resuelven almacén/stock | Etapa 0.1 / D4 |
| H9 | El modelo `Factura` no soporta facturas espejo (sin `origen`, folio siempre del contador propio, uuid solo del PAC) — verificado | Bloquea el espejo real que pidió el dueño | Etapa 1.4 |

---

## Arquitectura de la transición

```
        WhatsApp (PDFs Balles/Jubran · fotos EHMO/MAFAN)
                          │ bot (sin cambios de fondo)
            ┌─────────────┴──────────────┐
            ▼ 1º (respaldo, como hoy)     ▼ 2º (PRIMARIO, con outbox)
     Master Google Sheets          FACTURADOR  /oc  (bandeja)
                                          │ asignar / corregir / un clic
                                          ▼
                                   REMISIÓN (serie del grupo, precios de lista)
                                          │ selección por lote
                                          ▼
                              EXPORT EXCEL MASIVO SAE  ←← pieza nueva (H1)
                                          │ import manual en Aspel (como hoy)
                                          ▼
                                   SAE factura y timbra
                                          │ folio ZHGO nnn
                                          ▼
                        remision.factura_sae (espejo, RESERVADO)
                        + vista "Facturas SAE" (agrupada por folio)
```

Anclas de conciliación (ya existen las tres): `origen_externo = WA:<jid>:<folio>`
(OC), `su_pedido` (OC del cliente en la remisión), `Observaciones = "OC <folio> …"`
(en SAE, convención actual del bot que el export respeta).

---

## Etapa 0 — Cimientos (sin tocar la operación; ~1-2 días de trabajo)

### 0.1 Sembrar datos en el Facturador
- `cliente_externos`: **SAE `02:7`→Balles, SAE `02:6`→Jubran** (H2); **NOMBRE de
  JUBRAN** con la palabra exacta impresa en su PDF (tomarla de una OC real de Jubran).
- Marcar **ALM-01 como almacén default** (H8).
- Confirmar con el dueño si **MAFAN en la lista de Balles/Jubran** es intencional (D2).

### 0.2 Bot: hacerlo digno de un sistema primario (repo SmartSupply/bot)
- **Outbox** para `enviar_oc` (H3): mismo patrón que `logs/agente_outbox.jsonl` +
  drenador periódico; y que `flushBatch` lea `res.facturador` y avise al grupo interno
  cuando el envío falle (hoy muere en stderr).
- **Perfil correcto por grupo** (H7): los grupos Balles/Jubran dejan de viajar como
  `'ehmo'` (decisión D5: perfil propio `balles` o vacío).
- **`sincronizar_grupos` al arrancar el bot** (hoy solo corre al "conectar").
- Comando de rescate: reenviar una OC del Master a la bandeja a mano.

### 0.3 Facturador: bandeja lista para el flujo diario (H4; mayormente frontend —
el backend ya acepta líneas arbitrarias en `crear-remision`)
- Editar **cantidad** y **presentación** por partida, y **agregar/quitar partidas**,
  en el staging del modal de la OC (la OC original no se toca: es evidencia; los
  cambios van en las líneas de la remisión, como ya hace producto/precio).
- **Filtros por cliente y rango de fechas + paginación + búsqueda** (copiar el patrón
  server-side de /remisiones) y un **resumen del día** (llegadas / automáticas / a mano).
- Botón **"abrir la remisión"** al crearla, y **alta manual de OC** (canal MANUAL) para
  órdenes que lleguen fuera de WhatsApp.

### 0.4 Checkpoint de la etapa
Prueba E2E controlada: un PDF real de Balles y uno de Jubran → bandeja → cruce →
remisión de un clic. Se valida: idempotencia (mismo PDF dos veces = una OC), Jubran
resuelve sin ambigüedad (con la nueva NOMBRE), serie RZHGO, precios de la lista.
**No se avanza a Etapa 1 sin esto en verde.**

---

## Etapa 1 — Balles + Jubran en vivo (se sigue facturando en SAE)

### 1.1 Go-live de recepción
Operación diaria en la bandeja. El Master sigue escribiéndose igual (respaldo).
**Conciliación diaria automática**: script/comando que compara el Summary del Master
contra `GET /oc-recibidas` del día — toda OC del Master debe estar en la bandeja.
Cualquier faltante = bug del cable o del outbox; se corrige antes de seguir.

### 1.2 Remisiones desde la bandeja
Meta operativa: la mayoría de las OCs de Balles/Jubran salen con **"remisión de un
clic"** (sus claves ya están en `producto_clientes`); lo que caiga a manual alimenta
el aprendizaje (alias + códigos). Los ajustes finos se hacen editando la remisión.

### 1.3 Export del Excel masivo SAE (H1 — la pieza nueva grande)
**Backend** `POST /remisiones/export-sae` (+ UI de selección por lote en /remisiones):
- Dos modos: **FACTURA (27 columnas)** y **PEDIDO (22 columnas)** — layouts verificados
  byte a byte contra los .xls reales; archivo **.xls (Excel 97-2003, xlwt)** como los
  que SAE ya importa; una fila por partida, cabecera repetida; SAE agrupa por
  FOLIO+CLIENTE+FECHA.
- **CLIENTE** = número SAE de `cliente_externos` (`02:7`→`7`); si hay claves de más de
  una **empresa** SAE (02 vs 03 Tabasco), se genera **un archivo por empresa**.
- **CLAVE** = `producto_clientes.codigo_cliente` (la clave que ese cliente ya factura
  en SAE). Partida sin código de cliente = error visible antes de generar.
- **FECHA** = hoy en **MM/DD/YYYY** (configurable — trampa real documentada: la PC de
  importación interpreta mes/día/año; con DD/MM entraron facturas con fecha cambiada).
- **Observaciones** = `OC <su_pedido> <observaciones>` (misma convención del bot).
- **FOLIO (facturas)** = serie + consecutivo con el relleno exacto por serie
  (ZHGO=14, ZMAFAN=16, ZEHMOHOS=18). El folio inicial lo **confirma el operador**
  (prellenado con el último `factura_sae` conocido + 1; verificable con el comando del
  bot que lee el consecutivo real de SAE) — decisión D1.
- Al generar el export de FACTURAS, **estampar `factura_sae` en cada remisión del
  lote** (→ RESERVADO): el espejo se llena solo, sin captura manual.
- Candados: remisión ya exportada no se re-exporta sin confirmación (evita duplicados
  en SAE); lote vacío/partidas sin precio bloquean.

**Checkpoint 1.3**: durante 2-3 días, generar el masivo con el Facturador **y** con el
bot en paralelo y comparar (folios, claves, cantidades, precios, totales). Importar a
SAE el del Facturador solo cuando ambos coincidan.

### 1.4 Espejo real de facturas (extensión del modelo `Factura`)
Decisión del dueño: las facturas emitidas por SAE se guardan como facturas del
Facturador — registros de verdad en la BD, no una vista — para estados de cuenta,
reportes y adaptación del equipo, **sin que el Facturador timbre nada**.

- **Modelo**: `facturas.origen = ESPEJO_SAE` + candados (sin PAC: no timbrar, no
  cancelar ante Facturama, no REP fiscal; edición limitada). `serie`/`folio` guardan
  los REALES de SAE ("ZHGO" + 233 caben tal cual en los campos existentes, sin
  consumir folios propios); `uuid` = el fiscal verdadero de `CFDI02.UUID` (nunca
  `FACTF02.UUID`, que es un GUID interno); XML de SAE adjunto si se quiere descarga.
- **Ingesta — el CONECTOR SAE** (decisión del dueño 28-ago: sí hay comunicación
  Facturador↔SAE, con arquitectura de conector que EMPUJA, no backend que jala): un
  agente que corre junto al SAE, lee su BD localmente (solo lectura) y hace `POST
  /facturas/espejo` idempotente por (empresa, serie, folio), con clave de conexión
  propia (tipo `SAE`, permisos solo de espejo — separada de la del bot). Sincroniza:
  folio, cliente, fecha, partidas, totales, observación ("OC n" → liga remisión y
  estampa `factura_sae`), UUID de `CFDI02`, **cancelaciones** (STATUS='C' → CANCELADA),
  **pagos/REP (ZCP)** como abonos, y el **último folio por serie** (prellena el export).
  Captura también lo facturado fuera de nuestro flujo. Inventario-neutral.
  - **v1**: la sync vive en el bot (reutiliza `_sae_query`, acceso probado a 18 tablas).
  - **v2**: se extrae a un conector independiente instalable en la máquina de cualquier
    cliente futuro — **es la herramienta de onboarding** "migra desde SAE sin perder tu
    historia". Solo tráfico saliente HTTPS: sin VPN, sin puertos abiertos.
- **El espejo es GLOBAL desde el día 1** — espejear es solo lectura, sin riesgo: se
  sincronizan TODOS los clientes y TODAS las empresas SAE (02, 03…) desde el arranque.
  Estados de cuenta y reportes completos de inmediato. Las fases por cliente aplican
  solo al INTAKE (bandeja + Excel), no al espejo.
- **Estados de cuenta desde la fase espejo**: las PPD espejo entran con
  `saldo_insoluto = total`; los **pagos/REP de SAE (serie ZCP, FACTG02)** se
  sincronizan como abonos sin timbrar. El estado de cuenta y la antigüedad ya
  construidos funcionan solos. (Esto absorbe la vieja D6: ya no hay backfill al corte.)
- **Candado por cliente espejeado**: mientras el cliente esté en espejo, crear factura
  normal (desde-remisiones / directa) para él devuelve 409.
- Al generar el export 1.3 NO se crea la factura espejo todavía: nace cuando la sync
  la ve en SAE (la verdad es SAE, no nuestro export).

**Checkpoint de la etapa**: 3-5 días operando Balles+Jubran 100% por bandeja, con
conciliación diaria en verde y al menos 2 masivos importados a SAE sin error.

---

## Etapa 2 — EHMO + MAFAN (se sigue facturando en SAE)

### 2.1 Bot: conectar el pipeline de fotos (H5)
- `ehmo_pedidos.py` manda cada pedido a `POST /oc-recibidas` al cerrar su escritura al
  Master: `origen_externo = EHMO:<perfil>:<folio interno>` (el folio HO-33ACT-LUN es
  determinista — sirve de ancla); reemplazos/extras re-ingestan el mismo
  `origen_externo` (actualiza el payload mientras no tenga remisión — comportamiento
  ya soportado por la ingesta). Con perfil correcto (`ehmo`|`villahermosa`), las 36
  equivalencias UBICACION de Tabasco cruzan solas la sucursal.
- Mismo outbox de 0.2.

### 2.2 Datos EHMO/MAFAN en el Facturador (H6)
- **Proyectos** (HOSPITALES, DIF, CEREZOS, SEGURIDAD PÚBLICA, SECRETARIO NERI) +
  **listas de precios por proyecto** (peso proyecto=8 en `lista_asignaciones`),
  importadas de las listas reales del bot (Pachuca por proyecto; Villahermosa del xlsx
  reconstruido). Los 92+62 alias aprendidos del bot se migran como `producto_alias`
  con alcance.
- **Series de Tabasco**: ZEHMOVH/RZEHMOVH colgadas de la sucursal EHMO-Tabasco
  (la resolución por sucursal ya existe). El export SAE de Tabasco sale como archivo
  de **empresa 03, cliente 1** (equivalencia `03:1` ya sembrada).
- Completar `producto_clientes` de EHMO (64) y MAFAN (40) desde sus listas.

### 2.3 Checkpoints
Los mismos de la Etapa 1: conciliación diaria bandeja vs Master EHMO (por perfil), y
masivo del Facturador comparado contra el del bot antes de importarlo a SAE.

---

## Etapa 3 — Semana de estabilidad (todo por la bandeja, SAE factura con el Excel del Facturador)

Criterios de éxito medibles, todos los días de la semana:
- **0 OCs perdidas**: bandeja = Master (conciliación automática).
- **100% de remisiones** nacen de la bandeja (0 capturas paralelas).
- **Masivos importan a SAE sin error** y sin duplicados.
- **Espejo completo**: toda remisión facturada en SAE tiene `factura_sae`.
- Conciliación semanal Facturador↔SAE (folios/totales) sin diferencias.

Si algún día falla un criterio, la semana se reinicia al corregir la causa.

## Etapa 4 — Corte: se deja de facturar en SAE (POR CLIENTE)

Realismo de tiempos (dueño, 28-ago): la operación real tiene ~30-40 clientes entre
todas las empresas SAE — la transición completa es de **2-3 meses**, no una semana. Por
eso el corte NO es un evento único: **el candado "cliente espejeado" se quita cliente
por cliente**. Se corta Balles cuando Balles cumple su semana estable, mientras los
demás siguen facturando en SAE con su espejo al día. El conector sigue sincronizando a
los que quedan.

Verificaciones previas (por cada cliente que se corta):
1. **Emisor/CSD**: el tenant del Facturador timbra con el MISMO RFC con el que SAE
   factura hoy (multiemisor ya activo; verificar CSD vigente con `compute_status`).
2. **Continuidad de folios**: con el espejo (1.4) sale gratis — fijar
   `series.folio_actual` = último folio del espejo en las series de ESE cliente.
3. **Saldos abiertos**: ya viven en el espejo (facturas PPD + abonos sincronizados);
   solo verificar contra SAE que cuadren al peso.
4. **Inventario** (D4): cargar existencias o aceptar sobregiro documentado.
5. **Quitar el candado de cliente espejeado** para ese cliente. La sync del espejo se
   apaga hasta que el ÚLTIMO cliente haya cortado (mientras tanto sigue espejeando a
   los que quedan en SAE).

El corte: las remisiones nuevas se facturan en el Facturador (lote ya construido:
timbrar/PDF/XML/correo N), REP al cobrar, estado de cuenta en línea. SAE queda de
consulta histórica. **El Master sigue como respaldo del intake** (el bot no cambia).
Primer día: doble verificación de cada CFDI (UUID en el SAT, receptor, claves).

---

## Decisiones abiertas (del dueño)

| # | Decisión | Recomendación |
|---|---|---|
| D1 | Folio SAE del export de facturas | **DECIDIDO (28-ago, v2): el conector reporta el último folio por serie en cada sync → prellenado en vivo.** El operador confirma al arranque; cuando la sync demuestre confiabilidad, se vuelve automático. |
| D2 | ¿MAFAN debe estar en la lista de precios de Balles/Jubran? | Verificar — huele a placeholder. |
| D3 | ¿Las requisiciones (cotizaciones) entran a la bandeja? | Después del corte; hoy no tocan el Master tampoco. |
| D4 | Inventario: ¿cargar existencias antes del corte o aceptar sobregiro? | Diferir a Etapa 4; la operación espejo no lo necesita. |
| D5 | Perfil del bot para grupos Balles/Jubran | `balles` propio (limpia el namespacing de UBICACION/PROYECTO). |
| D6 | ~~Saldos PPD abiertos de SAE al corte~~ | **ABSORBIDA por el espejo (1.4)**: facturas + abonos se sincronizan desde la fase espejo. |
| D7 | Rotar la clave `fi_ss_` y limpiar secretos del bot (backups .pre-migracion, logs) | Hacerlo en Etapa 0/1 — está marcado como riesgo desde el 27-ago. |
| D8 | ¿Desde cuándo se importa el histórico al espejo? (¿solo facturas con saldo abierto + nuevas, o desde julio para reportes?) | Abierto — definir el rango antes de la primera sync. |

## Riesgos vigilados

- **Fechas MM/DD** (ya causó facturas con fecha cambiada en silencio) → formato
  configurable + verificación post-import.
- **Folio adelantado** rompe el import de SAE → folio confirmado + relleno exacto.
- **Re-import duplica** pedidos en SAE → candado "ya exportada" + Observación `OC n`.
- **Doble CFDI** → regla de oro 2 (facturas espejo bloqueadas del PAC + candado 409
  para facturas normales de clientes espejeados hasta Etapa 4).
- **Precio de lista vs precio de la OC** (±0.01) → divergencias caen a manual; mantener
  la lista del Facturador alineada con la lista 3 de SAE durante la transición.
- **API caída** → outbox (H3) + conciliación diaria como red final.

## Apéndice — datos duros

- Clientes SAE (empresa 02): **4=MAFAN (ZMAFAN), 5=EHMO (ZEHMOHOS), 6=JUBRAN, 7=BALLES
  (ambos ZHGO)**; Tabasco: empresa 03, cliente 1 (ZEHMOVH).
- Layout FACTURA masiva (27 col): `FOLIO | CLIENTE | FECHA | SU PEDIDO | CLAVE |
  CANTIDAD | PREC | COL82 | COL9..COL21 | METODO PAGO | FORMA PAGO SAT | USO CFDI |
  COL22 | COL23 | Observaciones` — hoja "Facturas", .xls; PPD/99/G01.
- Layout PEDIDO masivo (22 col): `FOLIO | CLIENTE | FECHA | SU PEDIDO | CLAVE |
  CANTIDAD | PRECIO | COL1..COL14 | Observaciones` — hoja "Pedidos".
- Relleno de FOLIO por serie: ZHGO=14 chars, ZMAFAN=16, ZEHMOHOS=18 (serie + número
  alineado a la derecha).
- UUID fiscal de SAE: `CFDI02.UUID` (+`XML_DOC`); **`FACTF02.UUID` NO es el fiscal**.
- Tenant Facturador: `0114d0d2` (cristian-gerardo-zarate-orozco); conexión bot:
  1 clave `fi_ss_` ACTIVA (pista 2F10).
