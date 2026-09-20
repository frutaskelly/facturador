# AUDITORÍA — Retiro del Master Órdenes

> Encargo: `BRIEFING-AUDITORIA-RETIRO-MASTER.md`. Propuesta auditada: `PLAN-retiro-master-ordenes.md`.
> Fecha: 19–20 de septiembre de 2026. **Solo lectura**: no se modificó ningún repositorio, no se
> escribió en producción ni en el SAE, no se cambió de rama en el bot ni en el correo.
> Se auditó el **árbol de trabajo** (el bot tiene 6,730 renglones sin commitear), nunca HEAD, salvo
> donde se dice explícitamente que se comparó contra HEAD.
> Las lecturas de producción fueron `SELECT` acotados al inquilino vivo
> `cristian-gerardo-zarate-orozco` (`0114d0d2-1e9b-47d1-b5de-5c6062ae94d8`; hay 4 inquilinos).
> Ninguna contraseña ni valor de configuración aparece en este documento.

---

## 0. EL OBJETIVO DE REFERENCIA

> Formulado por el dueño el 20 de septiembre de 2026. **Sustituye a la frase del encargo** («todo debe
> funcionar de la misma manera; únicamente se quita el Master y la sincronización») como objetivo
> contra el que se mide esta auditoría, por decisión suya. Es más preciso: las cinco metas son
> verificables una por una.

1. **Sustituir el Master de órdenes que vive en Google Sheets por el Facturador.**
2. **WhatsApp pide precios al Facturador.**
3. **WhatsApp puede modificar remisiones en el Facturador, y se ven en vivo.**
4. **WhatsApp se comunica únicamente con el Facturador.**
5. **Creación de productos en SAE: WhatsApp se lo pide al Facturador, y el Facturador pide la
   creación en el Facturador y en el SAE.**

### El hueco de cada meta, medido

| Meta | ¿Existe hoy? | Lo que falta | Evidencia |
|---|---|---|---|
| **1** | A medias: la ingesta ya crea la remisión en el mismo request | La **red** (detector de órdenes perdidas que no lea la hoja), el estado de negocio (P1) y los reportes (P3, P4) | `config.py:93`, `oc_recibidas.py:432`; §4 y §3 |
| **2** | **No.** Lee `PRECIO_X_PROD` del SAE | Cambiar la fuente en 5 sitios + desvincular las 7 listas | `sheets_push.py:8101`; `ehmo_pedidos.py:2041, 4491, 4772, 6232` |
| **3** | **Sí, ya funciona**, y «en vivo» ya es cierto: el `PATCH` es síncrono | El **contrato** (P2). Hoy escribe sin él, y por eso reescribió nueve remisiones firmadas | `remisiones.py:677`; `docs/ESTADO.md:78-95` |
| **4** | **No.** El bot habla con Sheets, con el SAE y con Drive | **131 lecturas** y **11 escrituras** directas al SAE, más 86 `runSheets`, ~42 sitios de hoja en EHMO y 12 llamadas propias del correo | 96 + 37 `_sae_query`; 9 + 2 `_sae_exec`; `index.js:3798`; `email_watcher.py:303` |
| **5** | **No.** El bot escribe `INVE` directo, y solo en 02 y 03 | Un **aplicador del lado del Facturador** + alta de producto en 04 y 05 | `sheets_push.py:7889`; `ehmo_pedidos.py:11320` |

**Lo que esta tabla revela y el plan no tenía contado:** la meta 4 es la más cara de las cinco. El
plan mide el retiro en «86 llamadas a la hoja»; pero «hablar únicamente con el Facturador» obliga a
mover además **131 lecturas al SAE** que nadie había censado — la ficha de producto, las listas, los
folios, las facturas, la conciliación. Ese es el tamaño real de «cliente delgado».

**El orden sale de las metas mismas: la 2 y la 5 son precondición de la 4.** No se pueden cortar las
lecturas al SAE hasta que el Facturador sepa contestar precios (meta 2) y hasta que el catálogo esté
completo en las cuatro empresas (meta 5). Al revés, el chat se queda mudo.

**Corrección declarada sobre el aplicador:** la decisión del dueño sobre precios (§12.1) **canceló** el
aplicador para precios, y eso sigue en pie. Pero **la meta 5 lo resucita para productos**: «el
Facturador pide la creación en el SAE» es exactamente un aplicador, y no se puede evitar porque el
masivo rechaza claves que no existen en `INVE` (`export_sae.py:25`). La diferencia con lo que el plan
proponía es que ahora tiene un solo caso de uso — el alta de producto — en vez de catálogo y precios
completos.

---

## 1. DICTAMEN

**NO RETIRAR TODAVÍA.** El Master no es hoy un almacén redundante: es el único término de
comparación de la red que detectaría una orden perdida, y esa red no falla ruidosamente al
apagarlo — **se pone verde**.

**Dónde me aparto, medido contra el objetivo de referencia del §0.** Las cinco metas del dueño son
correctas y alcanzables; no discuto ninguna. Me aparto en **el orden**, que su formulación no fija:
**la meta 1 no puede ir primero.** Las metas 2 y 5 son precondición de la 4, y ninguna de las cinco
nombra la pieza que las sostiene — **la red**: el detector de órdenes perdidas
(`facturador_conciliar.py:12`), el dedup de reenvíos (`sheets_push.py:488`) y la detección de versión
vieja (`sheets_push.py:1169`) viven hoy en la hoja y no tienen sustituto. Poner la meta 1 antes que su
red es el único punto en el que esta auditoría contradice al dueño. A cambio de no retirarlo aún, se
conserva la única red que hoy avisa cuando una orden no llega.

**Opción recomendada como próxima acción: F (otra cosa primero)**, acotada a cuatro entregables
que son precondición de cualquiera de las otras cinco. **Mejor destino a un año: A, la del dueño** —
retiro directo con escritura al Facturador— pero llegando por el camino que construye primero el
detector que no lee la hoja. Destino y próxima acción **no coinciden**, y por eso se dicen por separado.

### Las tres razones que la hacen ganar

1. **La red muere en silencio y además miente.** Con el Master archivado, `folios_master_ehmo()`
   devuelve `{}` sin lanzar error (`facturador_conciliar.py:52-54`) y el lado Balles deja su fallo en
   `res["errores"]` (`facturador_conciliar.py:173,182`), clave que el disparador **nunca lee**: solo
   consulta `r.error` singular (`index.js:6969-6972`) y con todo vacío ejecuta el `return` de «día
   limpio: no se molesta a nadie» (`index.js:6977`). El criterio «0 OCs perdidas»
   (`facturador_conciliar.py:4`) quedaría verde permanente **por ausencia de fuente**. Peor: al leer
   una carpeta sin Master, el código **crea uno nuevo vacío** (`gc.create`, `ehmo_pedidos.py:2884`;
   `sheets_push.py:158-161`) — una lectura con efecto de escritura que resucita lo que se acaba de retirar.
2. **El contrato que haría segura la escritura directa no existe.** `propuesta` da **0 coincidencias**
   en `backend/app/models/` y **0** en `backend/migrations/versions/`; `dry_run` no aparece en todo el
   árbol. P2 no está «por construir»: está **construido al revés** (`PLAN:139`), y ya cobró nueve
   remisiones impresas y firmadas (`docs/ESTADO.md:78-95`). El candado que nació de ahí protege
   **solo las partidas**: su propio comentario dice «El resto del PATCH —fecha de entrega, notas—
   sigue pasando» (`remisiones.py:696-697`), y `export_sae_at` **no tiene ninguna guarda** (grep en
   `remisiones.py`: cero).
3. **La palanca de retroceso del plan no existe.** `master.activo` / `MASTER_ACTIVO` / `master_activo`
   dan **0 coincidencias** en todo el bot. El apagado no puede ser por perfil ni gradual: sería
   atómico para los cuatro motores a la vez. Y la propia conciliación tampoco se puede apagar por
   perfil sin editar código (`PERFILES_EHMO` fijo en `facturador_conciliar.py:35`; el timer no pasa
   `--perfil`, `index.js:6968`).

### El hallazgo que tumbaría este dictamen

Que apareciera un detector de huecos **independiente de la hoja** ya construido. Lo busqué:
`grep -rn 'conciliar|faltante|perdida' backend/app` no devuelve ningún detector; la conciliación
sustituta que el plan describe sobre la tabla `acciones` (`PLAN:564-565`) no tiene una línea escrita.
Si mañana existiera y corriera fuera del proceso de WhatsApp, el dictamen pasaría a **RETIRAR CON
CONDICIONES** sobre la opción A.

### Primera acción concreta, con su reversa

**Acción:** commitear el bulto del bot (6,730 inserciones en 11 archivos) en una rama con nombre,
con `probar.py` corriendo de verdad como puerta, **sin mergear a `main`**. Dentro de ese bulto va el
arreglo del pendiente 10 y el motor `cmd_une_remisiones_ehmo` (`ehmo_pedidos.py:9513`) cuyo disparador
ya está en `main` desde hace días (`HEAD:index.js:1639`) — es decir, **`main` hoy no arranca completo**.

**Reversa:** `git branch -D` de la rama; el árbol de trabajo no se toca y producción se sigue
construyendo de disco, así que la operación no se entera. Coste de equivocarse: cero.

---

## 2. MAPA DE TUBERÍAS (W1)

Censo reconciliado por dos pasadas independientes. Totales verificados: `runSheets(` da **87**
coincidencias = **86 llamadas + la definición** (`index.js:3798`); `gspread` 9 en `sheets_push.py` y
10 en `ehmo_pedidos.py`; `facturador_client.py` expone **31** funciones.

### 2.1 Resumen por motor

| Motor | Lecturas hoja | Escrituras hoja | Escrituras SAE | Llamadas Facturador | Total |
|---|---|---|---|---|---|
| `sheets_push.py` (Balles/Jubran) | 48 | 24 | 9 | 1 | 82 |
| `ehmo_pedidos.py` (EHMO/MAFAN) | 20 | 22+3 infra | 3 | 18 | ~91 |
| `index.js` (Node) | — | — | — | 86 `runSheets` | 86 |
| `email_watcher.py` (correo) | vía subprocess | vía subprocess | vía subprocess | **0 directas** | — |

### 2.2 Las escrituras al SAE que sobreviven a cualquier opción

Los 9 sitios son **todos** los call sites de `_sae_exec_sql` (definida en `sheets_push.py:3104`, que
**no reintenta** — `:3068`):

| archivo:línea | Qué escribe | Destino en el estado objetivo |
|---|---|---|
| `sheets_push.py:7898` | `INSERT INVE02` + `PRECIO_X_PROD02` (alta de producto) | Al Facturador con aplicador (D24) |
| `sheets_push.py:8213` | UPSERT `PRECIO_X_PROD02` lista 3 | Al Facturador con aplicador (D24) |
| `sheets_push.py:8284` | `UPDATE INVE02.LIN_PROD` (categoría) | Al Facturador con aplicador |
| `sheets_push.py:8429`, `:8442` | `UPDATE INVE02` + UPSERT precio | Al Facturador con aplicador |
| `sheets_push.py:8482` | `UPDATE INVE02 STATUS='A'` | Al Facturador con aplicador |
| `sheets_push.py:8851` | `cmd_prefactura_sae`: `FACTF02`, `PAR_FACTF02`, `CUEN_M02`, `FOLIOSF02`, `TBLCONTROL02` | **Se retira (D9)** — comando ya quitado, código vivo |
| `sheets_push.py:9104` | `_actualizar_pedido_sae_core`: `UPDATE PAR_FACTP02`/`FACTP02` | **SIN SUSTITUTO** (ver 2.3) |
| `sheets_push.py:9315` | `cmd_pedido_sae`: `INSERT FACTP02` + `PAR_FACTP02` | **Se retira (D9)** → masivo |
| `ehmo_pedidos.py:11320`, `:11358` | `INSERT INTO {_t('INVE')}` / UPSERT precio (empresa 03) | Al Facturador con aplicador |

> Nota de método: en `ehmo_pedidos.py` el literal `INSERT INTO INVE03` **no existe** — es f-string
> con `_t()`. Quien re-audite con grep literal obtendrá cero.

### 2.3 Columna SIN SUSTITUTO — la lista de trabajo real del retiro

**Bloque duro (funcionalidad sin equivalente en el Facturador):**

| # | archivo:línea | Qué hace |
|---|---|---|
| 1 | `sheets_push.py:9104` | Editar un pedido **ya insertado** en SAE. `PATCH /remisiones/{id}` actualiza la remisión, nada reexporta el pedido vivo |
| 2 | `ehmo_pedidos.py:9009-9022` | Bitácora de incidencias con foto (pestaña propia) |
| 3 | `ehmo_pedidos.py:9519` | Fusionar dos folios en uno. **No existe endpoint de unión** (grep `unir\|merge\|fusion` en `remisiones.py`: cero) |
| 4-5 | `ehmo_pedidos.py:5872`, `:5952` | Hoja de armado (Excel y PDF) |
| 6 | `ehmo_pedidos.py:6582` | Insumo del pronóstico de compra |
| 7 | `ehmo_pedidos.py:7066` | Lista de compras |
| 8 | `ehmo_pedidos.py:7877` | Quién pide qué producto |
| 9 | `ehmo_pedidos.py:8816` | Calendario de entregas |

**Bloque de infraestructura** (desaparece con la hoja, solo requiere borrado ordenado):
`sheets_push.py:160` (crea el libro), `:355-382` (`ensure_tabs`), `:459`, `:1195-1196`, `:1285-1287`,
`:1302-1339` (`cmd_reformular`), `:10029` (cierre de rango); `ehmo_pedidos.py:2808-2890`, `:2857-2868`, `:4879-4888`.

### 2.4 Filas huérfanas (solo bot / solo hoja)

- **Requisiciones**: cortan antes del Master (`sheets_push.py:1132-1135`, `return` antes de `client()`
  en `:1141`) y por tanto **nunca llegaron al Facturador**. El bot sí las persiste como snapshot
  re-cotizable (`sheets_push.py:606-642`, `cmd_actualizar_rq` `:670`); el Facturador **no persiste
  ninguna** (0 `db.add`/`db.commit` en `cotizador.py`; 0 hits de `requisicion` en `models/`).
  En EHMO es peor: `procesar_requisicion` (`ehmo_pedidos.py:4519`) **sí escribe** en el Master (`:4560`, `:4599-4604`).
- **Cierre de periodo**: sin destino **por decisión** (D13, `PLAN:663`).
- **Canal de correo**: el único camino al Facturador es efecto secundario de escribir la hoja.

---

## 3. INVENTARIO DE PÉRDIDA (W2)

Encabezados: `MASTER_HDR` 25 columnas (`sheets_push.py:62-67`), `SUMMARY_HDR` 17 (`:68-71`),
`SUMMARY_FACT_HDR` R–V (`:77`), `MASTER_FACT_HDR` 17 (`:78-80`).

> **El orden de columnas es un contrato.** El comentario `sheets_push.py:72-76` dice «media docena de
> funciones leen el Summary por índice». El conteo real es **22 funciones**. En `ehmo_pedidos.py` el
> contrato está centralizado en `P_*`/`COL_*` (`:4837-4839`, `:2718`) con **338 accesos**.

### 3.1 Veredictos (47 campos)

| Veredicto | N | Campos |
|---|---|---|
| **MIGRA** | 37 | Folio, cliente, RFC, partidas, cantidades, precios, subtotales, notas del documento, amarre de factura, importes fiscales de encabezado |
| **MODELAR (P1)** | 6 | **Reparto** (0 hits de `reparto` en `backend/app/`; el bot ni lo manda), **Nota WhatsApp** (no viaja en `enviar_oc`), **tipo de partida EXTRA/REPOSICIÓN** (0 hits en modelos y migraciones), **Nueva Nota**, **Nota de Master Facturas**, **Fecha del documento** |
| **SE PIERDE** | 2 | **Requisición Folio** (nadie lo cubre), **DESC %** por línea |
| **MIGRA parcial** | 2 | Fecha/Hora de envío WhatsApp; detalle fiscal por partida del espejo |

### 3.2 Las cuatro bajas que el plan no vio

Verificadas contra el código por una lente adversarial que intentó refutar los veredictos MIGRA:

1. **«Nueva Nota» NO migra.** Es la nota que **escribe el equipo** después de la captura
   (`sheets_push.py:4274`). Pero `enviar_oc` se llama **una sola vez**, al ingerir el documento
   (`sheets_push.py:1232`, único call site), y `facturador_client.py` no tiene ninguna función de
   actualización de partidas. Todo lo que el equipo escribe después se queda en la hoja y **muere con ella**.
2. **La fecha del documento se escribe y nadie la lee.** Queda en `oc_recibidas.payload`
   (`oc_recibidas.py:410`), pero ningún código del backend lee `payload["fecha"]`, y la remisión no la
   hereda (`oc_recibidas.py:1199` toma la del request, no la del documento).
3. **El DESC % por línea es una pérdida que ninguna pieza P1–P12 nombra.** El parser sí lo extrae
   (`sheets_push.py:477`, `:1200`), pero `_linea_fact` no lo manda (`:1091-1110`) y el schema no lo
   acepta (`schemas/oc_recibida.py:15-22`).
4. **El reporte «Master Facturas» solo migra a medias.** El espejo deja `iva_importe`/`ieps_importe`
   **en cero** por línea pese a que las columnas existen (`facturas.py:1361-1370` vs `factura.py:161-164`),
   y el encabezado mete el IEPS dentro de `iva_trasladado = total − subtotal` (`facturas.py:1343`).

### 3.3 Reportes

| Flujo | Fuente hoy | Destino |
|---|---|---|
| Estado de cuenta | `sheets_push.py:6725` | **CON DESTINO** — `cobranza.py:386,455,477` |
| Master Facturas | `sheets_push.py:4207` | **PARCIAL** — factura y totales sí; detalle fiscal por partida y nota, no |
| Cotización de requisición | `sheets_push.py:905` | **CON DESTINO** — `precios.py:122` |
| Listas de precios | `sheets_push.py:5340` | **CON DESTINO** — `lista_export.py:45,62,83` |
| Hoja de armado (×2 motores) | `sheets_push.py:2528`; `ehmo_pedidos.py:5864,5942` | **SIN DESTINO** (P4 sin construir) |
| Resumen de órdenes EHMO | `ehmo_pedidos.py:5284` | **SIN DESTINO** (P3 sin construir) |
| Persistencia de requisiciones | `sheets_push.py:606-642` | **SIN DESTINO** |
| Revisión/conciliación EHMO | `ehmo_pedidos.py:5517`, `:5740` | **SIN DESTINO** |
| Cierre de periodo | `sheets_push.py:9924` | **SIN DESTINO por decisión** (D13) |

---

## 4. TABLA DE MODOS DE FALLA (W3)

Nueve escenarios contra el código, en los dos órdenes de escritura y las cuatro tuberías.
Orden real de `cmd_add` (`sheets_push.py:1113`): parseo → corte sin folio → **corte requisición (1132)**
→ apertura de hoja (1141) → **duplicado_exacto (1144)** → **version_vieja (1169)** → Drive (1184) →
borrado de filas previas (1186) → **hoja (1203/1205)** → **Facturador (1232, en try propio que no propaga)**.

| # | Escenario | HOY (hoja primero) | INVERTIDO (Facturador único) |
|---|---|---|---|
| 1 | Hoja caída | **La orden muere en los dos lados.** La excepción salta en `:1141`/`:1203`, antes de `enviar_oc` (`:1232`); el outbox nunca se entera porque el encolado vive **dentro** de `enviar_oc` (`facturador_client.py:387,406`). La conciliación no puede verla: no está en ninguno de los dos lados | No aplica: la hoja salió de la ruta |
| 2 | Facturador caído | La hoja conserva la orden; outbox encola tras 3 intentos (`facturador_client.py:369,400-407`); drena en ≤5 min (`index.js:6958`) | Outbox es el **único** registro; su fallo de append (hoy inofensivo, `:218-231`) pasa a ser pérdida total |
| 3 | Bot caído, correo vivo | El correo sigue escribiendo por subprocess; **nadie drena el outbox** (`email_watcher.py:488-492` solo drena sus correos internos) | Igual, y sin hoja de respaldo |
| 4 | Ambos caídos | Igual que el 1: la hoja mata la corrida antes del Facturador. EHMO avisa «el pedido salió igual» (`ehmo_pedidos.py:10860`) | Degrada a **retraso** (outbox ≤5 min); pero nadie vigila el outbox mientras crece |
| 5 | Red intermitente | Reintento auto-limpio: `delete_folio_rows` (`:1186`) borra filas parciales | Idempotencia por `origen_externo` absorbe el reenvío |
| 6 | 4xx (≠429) | **No se encola**: va a `logs/facturador_rechazadas.jsonl` (`facturador_client.py:390-399`). Ese archivo **no tiene un solo lector** en todo el sistema | Igual, y sin conciliación que lo detecte después |
| 7 | 5xx | 3 intentos con backoff → encola → drena | Igual, pero sin hoja si el drenado nunca corre |
| 8 | Timeout | **El mejor blindado hoy**: idempotencia + outbox; no se pierde ni se duplica. *Salvo* la captura en pantalla: reintentar tras timeout crea **una segunda remisión sin candado** | El outbox se vuelve el único registro; EHMO ni siquiera tiene camino directo (`espejar_folios_bandeja` lee **del Master**, `ehmo_pedidos.py:3690-3691`) |
| **9** | **Master archivado y una orden no llegó** | — | **NADIE SE ENTERA.** Ver abajo |

### 4.1 El escenario que decide el dictamen

| Tubería | Quién se entera, y en cuánto |
|---|---|
| WhatsApp Balles/Jubran | Acuse en el grupo, en segundos (`index.js:3494-3498`). Si ese mensaje se pierde de vista, **nadie más**: sin Master no hay conciliación |
| WhatsApp EHMO/MAFAN | Warning en el acuse (`ehmo_pedidos.py:4006-4011`). Igual |
| **Correo** | **Nadie, nunca.** `cmd_add` devuelve `ok: True` aunque el Facturador falle (el fallo viaja en el campo `facturador`, `sheets_push.py:1266-1267`), y `email_watcher.py` **no inspecciona ese campo en ninguna línea** ni lee el exit code (`:301-315`). Ya es la tubería ciega **hoy** |
| Captura en pantalla | **Nunca tuvo red** en ningún orden de escritura: no pasa por el bot y la conciliación solo compara contra la hoja |

**Precedente que lo respalda** (no hipotético): el 3-sep la conciliación llevaba días detectando OCs
perdidas y **ni una alerta llegó** porque el socket estaba muerto — 9 órdenes de Balles/Jubran fuera
de la bandeja sin que nadie se enterara (`index.js:6984-6990`). «Existe el detector» no equivale a
«alguien se entera». Y las 14 órdenes perdidas las narra el propio código: «así se perdieron TODAS
las OCs de Balles/Jubran del envío en vivo» (`facturador_client.py:391-396`).

**La búsqueda del sustituto, agotada.** `grep -rn 'conciliar|faltante|perdida'` sobre
`backend/app` devuelve 21 coincidencias y **ninguna es un detector de ingesta faltante**: son merma
de inventario (`schemas/factura.py:59-60`), precios faltantes de remisión (`remisiones.py:149-168`) y
reconciliación de timbrados muertos (`services/cfdi.py:202`, `services/facturama.py:227`). Además
**el backend no tiene ningún planificador**: `APScheduler|BackgroundScheduler|celery|@repeat_every`
→ cero resultados. Y el único rastro que sobreviviría, `logs/facturador_rechazadas.jsonl`, solo
aparece en quien lo escribe (`facturador_client.py:232`): **no tiene lector**.

**Un detalle que empeora el 4xx:** el acuse dice «NO SE PUDO ENCOLAR — reenviar a mano»
(`index.js:3496-3498`) porque en un 4xx no viene el campo `encolada`. El texto es engañoso: la orden
**sí** quedó registrada, en un archivo que nadie lee.

---

## 5. RELOJ DEL SISTEMA (W4)

Periodos leídos del **literal del código**, nunca del comentario.

| Pasada | archivo:línea | Periodo literal | Etiqueta |
|---|---|---|---|
| Espejo SAE (facturas + precios) | `index.js:6920`, periodo en `:6930` | `15 * 60 * 1000` | **SOBREVIVE** |
| Poller botón «Sincronizar SAE» | `index.js:6938`, periodo en `:6948` | `60 * 1000` | **SOBREVIVE** |
| Drenado del outbox de OCs | `index.js:6951`, periodo en `:6958` | `5 * 60 * 1000` | **SOBREVIVE** (pero solo en el proceso de WhatsApp) |
| **Conciliación Master ↔ bandeja** | `index.js:6968`, arranque `:7016`, periodo `:7017` | `20 min` + `6 h` | **MUERE** |
| Conciliación Facturador ↔ SAE | `index.js:6998` | mismo timer | **SOBREVIVE por fuente, hoy ENCADENADA** (ver abajo) |
| Vigía de remisiones | `index.js:10295-10316` (motor de 60 s, `:10609`) | lun–sáb, 7–19 h | **MUERE** |
| Comparador de masivos | `facturador_comparar.py` | CLI manual, sin timer | **MUERE** |
| Sincronizar grupos | `index.js:6906` | one-shot 8 s | **SOBREVIVE** |
| Watchdog de conexión | `index.js:10613-10633` | 5 min / corte a 8 min | **SOBREVIVE** |
| Outbox de WhatsApp | `index.js:7288`, cierre `:7323` | `20000` ms | **SOBREVIVE** |

**La frase que el encargo pide explícita:** *la sincronización que el dueño quiere quitar es la del
Master (conciliación Master↔bandeja y vigía de remisiones); **el espejo del SAE no es esa
sincronización** y debe seguir corriendo — es la que trae las facturas y los precios desde el SAE.*

### 5.1 Dos hallazgos del reloj

1. **El espejo corre al doble de lo que dice toda la documentación.** El código pone 15 minutos
   (`index.js:6930`) y lo llaman «30 min» el comentario contiguo (`:6912`), el del poller (`:6936`) y
   cuatro lugares del lado Python (`facturador_espejo.py:17,65,79,437`). La caché de huellas (TTL 6 h)
   y el flock se dimensionaron sobre 30.
2. **Retirar la hoja apagaría de rebote la vigilancia Facturador↔SAE.** La conciliación contra SAE
   (`index.js:6998`) no depende del Master, pero los `return` de `index.js:6971` («falló la mitad
   Master») y `:6977` («día limpio») salen de **toda** la función antes de llegar a ella.

---

## 6. CONTRATO, CANDADOS Y PERMISOS (W5)

### 6.1 El contrato de propuesta: NO existe

`propuesta` → **0 hits** en `backend/app/models/`, **0** en `backend/migrations/versions/`.
`dry_run` → **0** en todo el árbol. Los 3 hits del briefing en `backend/app` son comentarios.

Lo que sí existe: pares preview→aplicar **mediados por una persona** donde el «aplicar» reenvía los
datos otra vez, sin token que el servidor valide (`productos.py:754`/`:1167`, `ImportIn` en
`schemas/producto.py:323-340`); simulaciones de solo lectura (`remisiones.py:1512`,
`listas_precios.py:763`); y **una sola propuesta persistida en todo el backend**: el ciclo
`export_sae_folio` (`models/remision.py:92` → estampa en `export_sae.py:1036` → consume en
`facturas.py:1458` → limpia en `espejo_cruce.py:204`). **Ese es el molde a copiar para P2.**

De los 20 comandos: **9 escriben**, 11 son de solo lectura; **5** requieren el contrato P2;
`masivo` ya tiene el suyo funcionando.

### 6.2 Matriz estado × campo

| Campo | BORRADOR | CONFIRMADA | IMPRESA | EXPORTADA | FACTURADA |
|---|---|---|---|---|---|
| Partidas | pasa | pasa | **BLOQUEA (409)** solo si `conexion_id` — `remisiones.py:698` | **pasa** | BLOQUEA — `:654-660` |
| Fechas | pasa | pasa | **pasa** (`:696-697`, textual) | **pasa** | BLOQUEA |
| Notas | pasa | pasa | **pasa** | **pasa** | BLOQUEA |
| Precios | pasa | pasa | pasa (si no viene en `lineas`) | **pasa** | BLOQUEA |
| `su_pedido`, sucursal | pasa | pasa | pasa | **pasa** | BLOQUEA |

- **El candado de impresa protege solo partidas y solo frente a una conexión.** Una persona en
  pantalla puede reescribirlas; el bot no.
- **`export_sae_at` no tiene guarda alguna**: grep en `remisiones.py` devuelve cero. Una remisión ya
  exportada al masivo se puede repreciar sin aviso. Es el pendiente 11 del plan, **abierto**.
- **Candado con red:** el de impresa **sí tiene prueba** — `tests/test_remisiones_api.py:653` (conexión)
  y `:668-684` (`impresa_at` + `assert 409`).
- **Sin candado optimista:** 0 hits de `If-Match`/`version`/`etag` en `remisiones.py`. En el triángulo
  vigía + persona + WhatsApp, **gana el último que escribe**. El único `flock` del bot está en el
  espejo (`facturador_espejo.py:98`), no en el vigía.
- **El doble export al masivo NO es un candado**, es un aviso: `export_sae.py:558-566` escribe en
  `res.avisos` (no `res.errores`) y **sin `continue`**; la prueba que lo cubre lo certifica como aviso
  — `test_export_repetido_avisa_pero_no_bloquea` espera **200** (`tests/test_export_sae.py:314-330`).
- **«Sin revisar no se exporta a SAE»** (`export_sae.py:527-535`) es **vigente pero sin red**:
  `revision_pendiente` no aparece en `tests/test_export_sae.py`.

### 6.4 Un hallazgo nuevo: la carrera del folio de pedido en el SAE

El encargo pedía verificar que los consecutivos salen de `TBLCONTROL02` y no de `MAX+1`. La respuesta
es **las dos cosas, y la peligrosa es la del documento**:

- Los consecutivos **internos** de `TBLCONTROL02` sí son atómicos (`SET @var = ULT_CVE = ULT_CVE + 1`).
- Pero el **folio del documento** sale de `MAX(TRY_CAST(...))+1` (`docs/sae/crear_pedido_SAE.sql:24`)
  **fuera de la transacción**: el `BEGIN TRAN` está 58 líneas después (`:82`), sin hint de bloqueo y
  **sin `IF EXISTS` sobre el folio calculado**.
- La prefactura **sí** tiene esa guarda (`IF EXISTS … RAISERROR('folio ya existe')`,
  `sheets_push.py:8770`). **El pedido no.**

Dos pedidos creados a la vez pueden tomar el mismo folio. Y `_sae_exec_sql` **no reintenta**
(`sheets_push.py:3068`) — lo cual aquí protege, porque un reintento duplicaría.

### 6.3 Permisos

`PERMISOS_CONEXION` = 6 permisos fijos (`rbac.py:57-64`). La conexión los recibe **directo del
código**: `permissions=set(PERMISOS_CONEXION)` (`rbac.py:276`), sin pasar por el catálogo.

**`factura:espejo` no está sembrado — confirmado en código y en producción.** Cero hits en
`migrations/versions/`, y la tabla `permissions` de producción tiene `factura:cancelar`,
`factura:eliminar` y `factura:gestionar`, pero **no** `factura:espejo`. Funciona igual gracias a
`rbac.py:276`. Cualquier pantalla que intente asignarlo a un rol humano no lo encontrará.

**Permisos nuevos por opción** (contados contra los decoradores reales de cada endpoint):

| Opción | Permisos nuevos | Cuáles |
|---|---|---|
| **A · retiro directo** | **3** (4 si no se funden las dos escrituras) | `precio:leer`, `catalogo:espejo` (fusión de catálogo + precios de lista), `cobranza:leer` |
| **B · por etapas** | **3 en total, escalonados** | Etapa 1 = **0** (órdenes, remisiones, cruce, masivos y pendientes caben enteros en los seis actuales); luego uno por etapa |
| **C, D, E, F** | **0** | — (E sube a 3 solo si además contesta precios; F sube si arranca por P6) |

Todas las opciones comparten **una migración**: sembrar `factura:espejo` en el catálogo.

**Dos falsos huecos, verificados:** «editar partidas con propuesta» (P2) cae en `remision:gestionar`
(`remisiones.py:682`, `oc_recibidas.py:958`) y el «folio sugerido» (P7/P9) también
(`remisiones.py:1886`, `:1905`). **Cuestan cero permisos** — el contrato P2 no amplía la frontera.

---

## 7. HOJA DE ASERCIONES (W6)

### 7.1 Del PLAN

| # | Aserción | Veredicto | Evidencia | ¿Mueve el dictamen? |
|---|---|---|---|---|
| 1 | El espejo corre cada 30 min | **REFUTADA** | `index.js:6930` = `15 * 60 * 1000` | No, pero invalida el dimensionado de caché y flock |
| 2 | 86 llamadas a la hoja | **CONFIRMADA** | 87 coincidencias = 86 llamadas + def en `:3798` | No |
| 3 | Existe interruptor `master.activo` | **REFUTADA** | 0 coincidencias en todo el bot | **Sí**: el apagado sería atómico, no por perfil |
| 4 | 2,989 / 6,671 renglones sin commitear | **REFUTADA (creció)** | Hoy: 11 archivos, **6,730** inserciones, 315 borrados | Sí: la Fase 0 sigue empeorando |
| 5 | P6a (precios) hecho | **REFUTADA** | `clave_sae` es columna **nueva y distinta** de `sku` (migr `0079:44`); el depósito **no la menciona ni una vez** (grep = 0): cruza por `sku` o por el código viejo por cliente (`listas_precios.py:248-249`) | Sí: regresión silenciosa viva |
| 6 | Las 7 listas espejadas | **CONFIRMADA contra producción** | Exactamente 7: 02:3, 02:5, 02:6, 02:7, 02:8, 02:9, 03:4 | No |
| 7 | El corte desvinculó 5 listas de precios | **REFUTADA en vigencia, con una lectura peor** | Las 5 (SAE5–SAE9) **siguen vinculadas** hoy, con 6 asignaciones vivas. Pero el corte **sí corrió**: es un único `DO $$` atómico (`corte_pachuca_ehmo_mafan.sql:64-71`, con `IF n <> 5 THEN RAISE EXCEPTION`), y `PLAN:45` describe la fila FMAFAN absorbida. La única lectura que sobrevive a las dos evidencias: **el corte se aplicó y algo revinculó las 5 listas después, sin quedar registrado en el repositorio** | Sí: hay un cambio de estado en producción que nadie registró, y el script de reversa afirma como vigente lo contrario (`:34-38`) |
| 8 | «Outbox + conciliación» cubren la pérdida de órdenes | **REFUTADA** | La conciliación lee el Master (`facturador_conciliar.py:12,47,76`) | **Sí: es el corazón del dictamen** |
| 9 | La reversa se aplicó | **CONFIRMADA contra producción** | ZEHMOHOS espejo=true folio 912; ZMAFAN espejo=true folio 188; sin FEHMOHOS/FMAFAN; sin referencias rotas | No |
| 9b | Río Libre es el único cliente nativo | **REFUTADA** | Con `tipo_documento='FACTURA'` hay **dos** series con `espejo_sae=false`: `RIO` (Río Libre, folio 44) y `GZ` (folio 0, nunca usada), apuntada por `clientes.serie_factura_id` de **CLI-010 GERARDO ALEJANDRO ZARATE ALVAREZ**, que además trae `clientes.espejo_sae=false` | Sí: rompe el criterio de salida de la Fase 5 tal como está escrito |
| 10 | 137 remisiones de Pachuca en borrador sin factura | **REFUTADA (156)** | Acotado a la sucursal Pachuca viva y a `deleted_at IS NULL`: **156** (EHMO 120, MAFAN 23, Balles 11, Río Libre 1, Jubran 1). Ningún subconjunto razonable da 137, y **no es desfase temporal**: ninguna se creó después del 18-sep | Sí: son 19 remisiones más de las presupuestadas para el masivo |
| 11 | El `main` del bot no arranca limpio | **CONFIRMADA con precisión** | Disparador `runEhmo(['uneremision'])` **sí** está en `HEAD:index.js:1639`; el motor `cmd_une_remisiones_ehmo` **no** está en HEAD y sí en disco (`ehmo_pedidos.py:9513`) | Sí: refuerza la puerta de higiene |
| 12 | `npm test` es `echo Error && exit 1` | **CONFIRMADA** | `package.json:7` | Sí |
| 13 | Los días (21–29 / 27–34) siguen vigentes | **REFUTADA, y el PLAN se contradice consigo mismo** | La tabla de `PLAN:115-120` suma Facturador **22–29**, no 21–29; y la otra tabla del mismo documento, «Las siete fases» (`PLAN:511-516`), da al bot **30–38**, no 27–34. Dos tablas incompatibles | Sí: es la cifra con la que se decide |
| 14 | **46 comandos en dos motores** | **REFUTADA en las dos mitades** | 46 es el total de **un solo** motor: `ehmo_pedidos.py:11531-11539` despacha 46 verbos; `sheets_push.py` despacha **75**. Y los motores son **cuatro**, no dos (el propio PLAN lo admite en `:505`, `:517`). Además hay **27** `cmd_` nuevos sin commitear | **Sí, es el que más lo mueve**: el censo de comandos está a menos de la mitad del real, y la Fase 4 («cero referencias a Sheets») se estimó sobre un objeto mal medido |
| 15 | Redirigir las 86 llamadas apaga Sheets | **REFUTADA** | Hay una **cuarta tubería** fuera de `index.js`: `email_watcher.py:93,303` con **12 llamadas propias** a `run_sheets`; y `facturador_conciliar.py:52` abre el libro por su cuenta | Sí: falta censar una tubería entera, que además no está en git |
| 16 | Once estados `pending*` (1 Map + 10 variables) | **REFUTADA: hoy son 15** | 1 Map (`index.js:368`) + 14 `let` (`:401,403,405,407,409,414,987,992,993,994,995,996,998,1045`) | Sí: +4 estados a convertir |
| 17 | «>27,000 líneas en tres archivos» (`PLAN:640`) | **REFUTADA** | 10,635 + 11,038 + 11,754 = **33,427** | Sí, al alza |
| 18 | «73 `.bak` de código» (`PLAN:529`) | **REFUTADA** | **93** en la raíz del bot (los 33 de `data/` sí son exactos) | No |
| 19 | «7,294 comandos en 30 días» (`PLAN:101`) | **REFUTADA** | `logs/router_decisions.jsonl`: 2–31 ago → **7,153**; todo agosto → 7,448. Ninguna ventana da 7,294. Y `ver_producto` es **1,243**, no 1,296 | No |
| 20 | El PLAN sobre su propia reversa | **SE CONTRADICE TRES VECES** | `PLAN:35` («arranca en diagnóstico y aborta»), `PLAN:45` («la reversa ya se aplicó») y `PLAN:133` («**no hay script de reversa**», en la revisión más reciente, la del 19-sep) | Sí: quien retome no sabe cuál creer |
| 21 | `registrarAccion` existe y nadie la llama | **CONFIRMADA** | `agente_db.js:142` (definición), `:171` (export), **cero** sitios de llamada | Sí: es la medida que el plan usa para las fases siguientes |
| 22 | El agendador del espejo de catálogo falla siempre | **CONFIRMADA en la letra, REFUTADA en la consecuencia** | Termina con excepción, sí: `logs/claves_sae.launchd.err` acaba en `PermissionError` al abrir su bitácora (TCC de macOS sobre `~/Documents`). **Pero el trabajo sí se hace**: muere en la línea 57, *después* del `os.chdir` (`:48`) y del bucle de empresas (`:52-56`). Verificado contra producción: `claves_sae` tiene 2,043 claves de la 02, 1,067 de la 03 y 1,075 de la 04, **las tres sincronizadas hoy a las 05:30**. Lo que se pierde es el **reporte por empresa**, que solo se vuelca a ese log | Sí, pero al revés de lo que parecía: el riesgo es de **visibilidad**, no de validación. Si una empresa empezara a fallar, nadie se enteraría |
| 23 | **La empresa 05 no tiene una sola clave espejada** | **HALLAZGO NUEVO** | `claves_sae` en producción solo tiene filas de 02, 03 y 04. Para la 05, `catalogo_sae` devuelve vacío y `export_sae.py` **se salta la validación de claves entera** (`if cat:`, comentario en `:263`): no bloquea, exporta, y el SAE rechaza después en silencio | **Sí**: el dueño pidió alta de producto en las cuatro empresas. Para la 05, el espejo de claves va **antes** que el alta |

### 7.2 Del BRIEFING (el encargo también se equivoca)

| Aserción del briefing | Veredicto | Evidencia |
|---|---|---|
| «`grep -c 'runSheets('` da 87» → implica 87 llamadas | **IMPRECISA** | Son 87 coincidencias pero **86 llamadas**: la 87ª es la definición (`index.js:3798`). El PLAN («86») tenía razón y el briefing lo reporta como errata del plan |
| «`git diff --shortstat` da 9 archivos y 6,648 inserciones» | **DESACTUALIZADA** | Hoy: 11 archivos, 6,730 inserciones |
| «Una corrida verde puede estar saltada porque el fixture hace `pytest.skip`» | **CONFIRMADA, y condicional** | Medido dos veces. Con el contenedor del 5434 **arriba**: 639 pruebas, **0 saltadas**, exit 0. Con la base **inalcanzable** (puerto muerto): **108 passed, 531 skipped, exit 0 — verde**. Es decir, el 83.1 % se salta en silencio. Agravante: `pytest.ini:4` fija `addopts = -q` y la línea de resumen **no se imprime**, así que la señal que delata el falso verde es exactamente la que no se ve. El disparador ya no es el default (`conftest.py:13-16` apunta al 5434), pero sí lo es **que el contenedor no esté arriba** |
| «La base de pruebas corre como superusuario con la RLS apagada» | **REFUTADA** | `rbac.py:367-378` (`get_tenant_db`) hace `SET LOCAL ROLE app_user` + GUC de inquilino dentro de la transacción; en el 5434 `relrowsecurity = t` en `oc_recibidas`, `remisiones`, `clientes` y `productos`. El rol baja a no-superusuario |
| Las líneas citadas (`index.js:6965-7017`, `:10291-10350`, `sheets_push.py:1113-1275`) | **DESFASADAS** | El árbol creció; las reales están en este documento |
| «`su_pedido` HO-34VIL-MIE ya rompió producción» | **NO VERIFICABLE DESDE EL CÓDIGO** | Sin mención en el PLAN ni en `docs/ESTADO.md`; solo un comentario en `oc_recibidas.py:1131-1141` |

### 7.3 Parche propuesto para el PLAN (redactado, **NO aplicado**)

```
--- PLAN-retiro-master-ordenes.md
 1. Línea 51 («lo inmediato»): «las 137 remisiones de Pachuca» →
    «las 156 remisiones de la plaza Pachuca en borrador sin factura (verificado
    contra producción el 20-sep-2026: EHMO 120, MAFAN 23, Balles 11, Río Libre 1,
    Jubran 1). Tabasco tiene además 183».
 2. Líneas 100, 105 y 291: «46 comandos vivos en dos motores» →
    «121 verbos despachados en cuatro motores (75 en sheets_push.py, 46 en
    ehmo_pedidos.py), más 27 cmd_ nuevos sin commitear».
 3. Toda mención a «espejo cada 30 min» (:340, :682, y corte_pachuca…sql:13-14) →
    «cada 15 min (index.js:6930); los comentarios que dicen 30 están desfasados».
 4. Línea 553: «once estados pending*» → «quince (1 Map + 14 variables)».
 5. Línea 640: «>27,000 líneas en tres archivos» → «33,427».
 6. Línea 529: «73 .bak de código» → «93».
 7. Línea 101: «7,294 comandos en 30 días» → «7,153 en la ventana 2–31 ago»;
    y :250/:310 «ver_producto 1,296» → «1,243».
 8. Líneas 573-574: las líneas citadas de cmd_pedido_sae y cmd_prefactura_sae
    están desfasadas +185. Reales: :9175, :8648, :8928.
 9. Resolver la contradicción de la reversa: :35 dice que aborta, :45 que ya se
    aplicó, :133 que «no hay script de reversa». Las tres no pueden ser ciertas.
10. Línea 47: «el único cliente nativo es Río Libre» → «son dos: Río Libre (RIO)
    y CLI-010 vía la serie GZ, sin usar (folio 0)».
11. P6a: «hecho» → «REABIERTO: el depósito de precios no usa productos.clave_sae
    (grep en listas_precios.py = 0); cruza por sku y por el código viejo por
    cliente (listas_precios.py:249). La docstring de :198 está desfasada».
12. Riesgos: la fila de la cobertura circular ya está bien; añadir que la
    conciliación no solo enmudece — al leer una carpeta sin Master **crea un libro
    nuevo vacío** (ehmo_pedidos.py:2884, sheets_push.py:158-161).
13. Fase 0: «6,671 renglones» → «6,730 al 20-sep»; y precisar que en `main` el
    disparador `uneremision` está commiteado (index.js:1639) y el motor no.
14. Línea 174/219: registrar que las 5 listas del corte están HOY revinculadas
    sin que el repositorio lo explique, y corregir reversa…sql:34-38, que afirma
    lo contrario.
15. Líneas 115-120 vs 511-516: las dos tablas de días son incompatibles
    (22–29 / 27–34 contra 30–38). Elegir una.
```

---

## 8. TABLERO DE LAS SEIS OPCIONES (W8)

| Criterio | A · Retiro directo | B · Por etapas | C · Master congelado | D · No retirar | E · Reemplazar | F · Otra cosa primero |
|---|---|---|---|---|---|---|
| **1. Cobertura** (toda fila con sustituto) | ✗ 9 sitios sin sustituto | ◐ los construye | ◐ | ✓ | ◐ | ✓ nada cambia |
| **2. Red sin la hoja** (criterio duro) | ✗ muere y se pone verde (`facturador_conciliar.py:52-54`, `index.js:6977`) | ◐ al final | ◐ conciliador ciego requiere volcado que no existe | ✓ | ◐ P10 sin escribir | ✓ |
| **3. Ninguna orden muere en silencio** | ✗ el correo ya es ciego hoy (`email_watcher.py` ignora el campo `facturador`) | ◐ | ◐ | ◐ | ◐ | ✓ |
| **4. Identidad única** | ✗ `WA:sin-jid:<folio>` (`sheets_push.py:1237`) | ✗ igual | ✗ | ✗ | ✗ | **lo arregla** |
| **5. Nada escribe sin contrato** | ✗ P2 no existe (0 hits) | ✓ lo construye primero | ✓ | ◐ vigía que propone | ◐ | ✓ |
| **6. Reversibilidad** | ✗ sin `master.activo` (0 hits) → atómico | ◐ sin interruptor tampoco | ✓ la hoja sigue viva | ✓ | ◐ | ✓ total |
| **7. Estado intermedio seguro** | ✗ | ◐ | ✓ | ✓ | ◐ | ✓ |
| **8. Frontera de permisos** | ✗ ≥1 nuevo | ✗ ≥1 | ✓ 0 | ✓ 0 | ✓ 0 | ✓ 0 |
| **9. Independencia de D18/D21/D23/D24** | ✗ depende de D24 y D23 | ✗ | ✓ | ✓ | ✗ D24 | ✓ |
| **10. Higiene previa como puerta** | ✗ no la exige | ◐ Fase 0 | ◐ | ◐ | ◐ | ✓ **es** la puerta |
| **14. Reglas de la casa** | ◐ roza «lo firmado no se reescribe» | ✓ | ✓ | ✓ | ✓ | ✓ |

### «Qué me mata» — por opción

- **A:** `facturador_conciliar.py:12`. El día del apagado se apaga el detector, y encima reporta día limpio.
- **B:** el plan por etapas presupone el interruptor `master.activo`, que **no existe** (0 hits). Sin él la Etapa 4 no es gradual.
- **C:** el conciliador ciego necesitaría un volcado Facturador→hoja que **hoy no existe** en ninguna dirección (el flujo es bot→hoja→Facturador).
- **D:** no acerca al estado objetivo del dueño; a un año deja el mismo pegamento frágil.
- **E:** P10 no tiene una línea escrita (`PLAN:141`, «sin movimiento»), y construirlo sin consumidor es trabajo muerto.
- **F:** no retira nada — si el dueño mide progreso por «¿ya se quitó el Master?», parece una semana perdida. Es el precio de la puerta.

**Ganador como próxima acción: F.** **Ganador como destino: A**, condicionada al umbral del criterio 16.

### El umbral explícito de la opción A (criterio 16)

Las tres condiciones deben cumplirse a la vez. Hoy **fallan las tres**:

| Condición | Estado | Línea exacta |
|---|---|---|
| Las guardas de la hoja (duplicado exacto, versión vieja) existen o se pueden mover en el mismo paso | ✗ | `sheets_push.py:488` y `:1169` leen la hoja; del lado del Facturador solo hay idempotencia por `origen_externo` (`oc_recibidas.py:364`), que ante una **segunda versión** del mismo folio **pisa el payload** (`:384-397`) en vez de tratarla como versión |
| Los campos de estado de negocio existen o su ausencia está aceptada por escrito | ✗ | 0 hits de `reparto`, `tipo_partida`, `EXTRA`, `REPOSICION` en `backend/app/models/` y migraciones. P1 no tiene una línea |
| El canal de correo tiene camino propio declarado | ✗ | Llega como `"WHATSAPP"` hardcodeado (`sheets_push.py:1233`) con ancla `WA:sin-jid:<folio>` (`:1237`), pese a que el esquema ya admite `"EMAIL"` (`schemas/oc_recibida.py:11`) |

**Por cuál línea de código exactamente no se puede hoy lo simple:** `facturador_conciliar.py:12`
(la definición de «perdida» que lee el Master) y `sheets_push.py:1237` (la identidad que colisiona
entre clientes por correo).

---

## 9. COSTEO

**Método publicado:** se cuentan (a) sitios de llamada a redirigir, por grep verificado; (b) endpoints
que faltan, por ausencia comprobada en `backend/app/api/v1/`; (c) estados en memoria a convertir, por
grep de `pending`; (d) migraciones nuevas, por campos MODELAR del §3. **No se reusan los días del
plan**: se recuentan contra el código de hoy.

| Concepto | Camino del dueño (A) | Camino por etapas (B) | Diferencia |
|---|---|---|---|
| Sitios de llamada a redirigir | 86 `runSheets` + ~42 sitios de hoja en EHMO + **12 propios del correo** + `facturador_conciliar.py:52` | Los mismos, en 4 tandas | 0 |
| **Comandos a cubrir** | **121 verbos despachados** (75 en `sheets_push.py` + 46 en `ehmo_pedidos.py`), no 46 | Los mismos | 0 |
| Comandos nuevos sin commitear | **27** `cmd_` que el censo del plan no conoce | Los mismos | 0 |
| Endpoints que faltan | 6 (propuesta/aplicar, unión de remisiones, armado, resumen, sin-precio agregado, fechas por lote) | Los mismos + comparador | +1 |
| Piezas P1–P12 exigidas | P1, P2, P5 mínimo. **P12 sale** (D18 = link) | P1–P12 menos P12 | +6 |
| Aplicador de precios (D24) | **0** — cancelado por la decisión del dueño del 20-sep | 0 | 0 |
| Alta de producto en SAE 04 y 05 | **por construir** (hoy solo 02 y 03) | La misma | 0 |
| Notas de crédito + menú + histórico | **obra nueva** (modelo, migración, PAC, UI, espejo) | La misma | 0 |
| Migraciones nuevas | ≥3 (reparto, tipo de partida, nota externa) + 1 (sembrar `factura:espejo`) | Las mismas | 0 |
| Estados `pending*` a convertir | **15** (1 Map + 14 variables), no 11 | 15 | 0 |
| Permisos nuevos | **3** | 3, escalonados (la primera etapa: 0) | 0 en total, ≠ en secuencia |
| Motores afectados | 4 (uno fuera de git) | 4 | 0 |
| **Trabajo previo no negociable** | el mismo de F | el mismo de F | 0 |

**El hallazgo que más mueve el costeo:** el plan dice «46 comandos en dos motores». El despacho real
es de **121 verbos en cuatro motores**, más 27 comandos nuevos que nadie ha commiteado. El criterio de
salida de la Fase 4 («cero referencias a Sheets en los cuatro motores») se estimó sobre un objeto
medido a menos de la mitad de su tamaño. **Los días del plan no se citan aquí porque no se pueden
recontar sobre una base que resultó ser el doble.**

**Rango, no promedio.** Dos recuentos difieren: contando solo lo que el estado objetivo exige
literalmente, **A es menor que B en piezas** (no pide P3/P4/P6/P10). Contando lo que hace falta para
que «todo funcione de la misma manera», **A converge con B**, porque los reportes sin destino del §3.3
son parte de «igual». La diferencia honesta: **A ahorra las etapas de sombra y comparación, no las
piezas.** Lo que A ahorra en calendario lo paga en red: el periodo de sombra **es** la verificación.

---

## 10. PUERTA DE ARRANQUE

Precondiciones no negociables antes del primer cambio que toque el Master.

| # | Precondición | Cómo se comprueba (sin producción) |
|---|---|---|
| 0 | **La suite declara su conteo de saltadas** | `pytest -q -rs` (o `-v`); hoy `pytest.ini:4` fija `-q` y el resumen no se imprime, así que un verde con 531 saltadas es indistinguible de uno con 639 pasadas. Sin esto, ninguna puerta de abajo es verificable |
| 1 | El bot commiteado, con `probar.py` cableado como `npm test` real | `git -C bot status --short` vacío; `npm test` sale 0 y corre `node --check` + `py_compile` + `probar.py` |
| 2 | `main` del bot arranca completo | `git show HEAD:ehmo_pedidos.py \| grep -c cmd_une_remisiones_ehmo` > 0 |
| 3 | El agente de correo bajo git, con marca de canal propia | `git -C email rev-parse --git-dir` responde; y la ingesta recibe `canal="EMAIL"` (hoy `sheets_push.py:1233` manda `"WHATSAPP"`) |
| 4 | Ancla de idempotencia sin colisión entre clientes | Ninguna orden llega con `WA:sin-jid:` — hoy todas las de correo lo hacen (`sheets_push.py:1237`) |
| 5 | **Un detector de huecos que no lea la hoja** | Existe un proceso que compara mensajes/acciones contra `oc_recibidas` y **no importa** `sheets_push` ni `ehmo_pedidos`; y que **no corre** dentro del proceso de WhatsApp |
| 6 | El interruptor `master.activo` por perfil | `grep -rn 'master.activo' bot/` devuelve algo |
| 7 | La conciliación lee `res["errores"]` | `index.js` consulta `r.errores` además de `r.error` (hoy solo `:6969`) |
| 8 | Guarda sobre `export_sae_at` | `grep -n export_sae_at remisiones.py` devuelve una guarda |
| 9 | **El espejo de claves cubre todas las empresas en alcance, y su reporte se puede leer** | `SELECT empresa, max(sincronizado_at) FROM claves_sae` devuelve una fila **por cada empresa en alcance** con fecha de hoy. Hoy cubre 02, 03 y 04 y están al día; **la 05 no tiene ninguna**. Además, `logs/claves_sae.launchd.err` sin `PermissionError`: mientras falle, el reporte por empresa se pierde y una empresa que empiece a fallar no se nota |
| 10 | **Las 7 listas desvinculadas y `espejar_precios` apagado**, junto con el cambio de fuente de la ficha | `GET /listas-precios/espejo/vinculadas` devuelve vacío, y los 5 sitios de la ficha ya no consultan `PRECIO_X_PROD`. Los dos a la vez: si se separan, queda la ventana donde el chat cotiza un precio y el documento cobra otro |

---

## 11. SECUENCIA Y REVERSA POR ETAPA (W7)

| Etapa | Qué se verifica | Con qué | Reversa escrita (no ejecutada) |
|---|---|---|---|
| **0 · Higiene** | git limpio, prueba real | `git status`, `npm test`, `pytest -q` (639 pruebas, declarar el conteo de saltadas) | `git branch -D`; el árbol no se toca |
| **1 · Identidad y canal** | `canal="EMAIL"`, ancla propia | Lectura de código + `pytest` de ingesta | Revertir el commit; la ingesta vuelve a `WA:sin-jid:` |
| **2 · Detector independiente** | Detecta un hueco sembrado en la base **local** del 5434 | `pytest` + prueba manual local | Apagar el proceso; nada más depende de él |
| **3 · Contrato P2** | Una propuesta sobrevive a reinicio y exige su número al aplicar | `pytest` + `npx tsc --noEmit` | Migración `downgrade` de la tabla de propuestas |
| **4 · Interruptor + candados** | `master.activo` apaga un perfil; `export_sae_at` bloquea | `pytest`, lectura de código | Volver el interruptor a `true` |
| **5 · Apagado por perfil** | Una semana por perfil sin abrir la hoja | Observación del dueño — **no verificable sin él** | Interruptor a `true`; la hoja sigue viva y escribible |
| **6 · Archivado** | — | — | **IRREVERSIBLE** (ver abajo) |

### 11.1 Lo irreversible, y dónde está su copia

| Elemento | ¿Hay copia? |
|---|---|
| Hoja archivada | Sí, el plan la exporta a Drive como archivo muerto (`PLAN:454-455`). **Ojo**: si el libro sale de la carpeta, el código **crea uno nuevo vacío** (`ehmo_pedidos.py:2884`) |
| `alias_aprendidos.json` | Migrado a `producto_alias` el 28-ago (según el plan) — **no verificable desde el código** |
| `remisiones.json`, `facturas_oc.json` | Sin copia declarada; el plan los borra en la Fase 4 |
| `PROYECTO_LISTA_SAE` | Debe volverse equivalencia del Facturador **antes** de borrarse |
| Código de Sheets | Recuperable por git **solo si el bulto de 6,730 renglones se commitea antes** |

### 11.2 Pasos que tiene que correr el dueño

El push directo a `main` lo bloquea el clasificador de permisos (`docs/ESTADO.md`): el camino es
`gh pr create --base main` y luego el merge. Todo lo que toque producción o el SAE es suyo.

### 11.3 El script de reversa

`backend/scripts/reversa_pachuca_ehmo_mafan.sql` arranca en modo diagnóstico
(`v_solo_diagnostico boolean := true`, línea 51) y aborta a propósito. **Pero su bloque de
comentarios (líneas 33-38) afirma que el corte desvinculó las listas SAE5–SAE9, y contra producción
las 5 siguen vinculadas.** Es exactamente la trampa «un script del repositorio no es un script que se
aplicó», esta vez dentro del script mismo.

---

## 12. PREGUNTAS PARA EL DUEÑO — **CONTESTADAS el 20-sep-2026**

> Las respuestas del dueño están abajo, con sus palabras. Cinco de las siete quedaron cerradas; las
> dos que bloquean el primer paso (1 y 2) siguen abiertas. **Este es el registro que sustituye a la
> lista de preguntas: lo que sigue es lo decidido, no lo propuesto.**

### Lo decidido

| # | Decisión del dueño | Consecuencia verificada |
|---|---|---|
| **3** | **Aceptar la pérdida** de «Requisición Folio» y del descuento por línea | Cero trabajo. Queda como pérdida aceptada por escrito, que era el requisito del criterio 1 |
| **D24** | **Aprobada, con mecanismo propio**: «las listas de precios viven en el Facturador. El SAE es responsable de los códigos SAT únicamente». El Facturador es dueño de los precios **porque genera el Excel masivo con ellos**; el masivo solo fija el precio *del documento*, no la lista de SAE; y **puede asignar cualquier producto sin importar la lista SAE del cliente** | **Mata el aplicador de precios** que D24 implicaba (≈400 líneas): si el masivo no necesita la lista de SAE, esa lista deja de importar y basta **dejar de leerla**. Ver §12.1 |
| **D18** | **LINK**: Drive queda como dependencia permanente; el Facturador no guarda el original | **P12 sale del alcance.** Riesgo heredado: las 183 órdenes sin link siguen sin él, y un link roto no tiene respaldo |
| **D21** | **A mano, después**: las 8 remisiones de Tabasco se corrigen en el SAE manualmente | No se regeneran masivos. Pero **nada impide que vuelva a pasar**: `export_sae_at` sigue sin guarda (§6.2) |
| **7** | **Las notas de crédito entran al Facturador**, con menú para crearlas y ver el histórico | **Obra nueva**: no hay modelo, migración, servicio, endpoint ni UI. Y hay un hallazgo urgente que no depende de construirlo: §12.2 |

### La primera acción del dictamen: **EJECUTADA el 20-sep**

La pregunta 1 quedó autorizada y cumplida. El bulto del bot está congelado en
`claude/fase0-bulto-bot` (`9f378f5`) de `frutaskelly/smartsupply-whatsapp-bot`, **sin mergear**:
`main` sigue en `8e075ff` y el árbol de trabajo quedó intacto, así que producción —que se construye
de disco— no se enteró. Los 6,730 renglones dejaron de existir en un solo disco.

- **Puerta corrida antes de commitear, toda en verde:** `node --check index.js`, `py_compile` de los
  cinco motores, y `probar.py` → **16 casos sin cambios**.
- **Dentro va el arreglo del pendiente 10**, precondición del apagado: `_rechazada_append` y el
  registro `logs/facturador_rechazadas.jsonl` para las OC rechazadas por payload (4xx), el caso de
  las fechas `DD/MM/AAAA` contra un campo `date`.
- **Fuera a propósito, y sigue sin rastrear:** la sesión de WhatsApp `auth.baneada-20260907-172511/`
  (el `.gitignore` cubre `auth/` pero **no** ese nombre — hueco que conviene cerrar), los
  `__pycache__/*.pyc` y `err1.txt`.

### Lo que sigue abierto

| # | Pregunta | Por qué sigue abierta |
|---|---|---|
| **2** | ¿El canal de correo entra al alcance? | Si entra: git + identidad propia antes de tocar la hoja. Si no: hay que apagarlo el día del retiro, porque el interruptor por perfil no lo alcanza |
| **nueva** | La empresa 05: ¿cuál es la regla de desempate cuando un cliente tiene clave en dos empresas? | Es lo que dejó a la 05 fuera (`facturador_espejo.py:57-59`). Sin esa regla no se puede espejar su catálogo, y sin catálogo espejado el masivo de la 05 exporta sin validar nada |

### 12.1 — Lo que cambia con D24, medido

La decisión del dueño **abarata** su propia opción. Trabajo real para que las listas vivan en el Facturador:

| Paso | Costo | Evidencia |
|---|---|---|
| Desvincular las 7 listas | **0 líneas** | `PATCH /listas-precios/{id}` con `sae_empresa`/`sae_lista` en null ya existe (`listas_precios.py:117-139`) y **la pantalla ya lo manda** (`frontend/app/(app)/listas-precios/page.tsx:87-91`) |
| Apagar `espejar_precios` | ~6 líneas | `facturador_espejo.py:530-537`. Sin esto, cualquiera revincula desde esa misma pantalla — **la explicación más probable del misterio de las 5 listas revinculadas** |
| Cambiar la fuente de la ficha del chat | 5 sitios | `sheets_push.py:8101` y `ehmo_pedidos.py:2041, 4491, 4772, 6232`. **Va en el mismo paso**: si no, el chat cotiza el precio viejo del SAE mientras el documento cobra el nuevo — el incidente «ZMAFAN 168» |
| Retirar `cmd_precio_sae` | borrado | `sheets_push.py:8216-8219`: escribiría en una lista que ya nadie lee |
| ~~Aplicador de precios Facturador→SAE~~ | ~~≈400 líneas~~ | **CANCELADO** por la decisión del dueño |

**El riesgo mientras tanto, y es de hoy:** el depósito del espejo **pisa** el precio del Facturador
sin avisar (`listas_precios.py:267-274`; docstring `:188` «SAE manda»). La caché de huellas solo
retrasa (TTL 6 h, y la huella es de lo que manda SAE, no de lo que tiene el Facturador,
`facturador_espejo.py:438-445`), y **el botón «Sincronizar SAE» la ignora** (`facturador_espejo.py:512`):
quien lo apriete borra los precios del Facturador de todas las listas vinculadas, de inmediato y sin
saberlo. Único sobreviviente: un precio que en SAE esté en $0 (`:242-244`).

### 12.2 — El camino crítico real: el viaje redondo del producto

El dueño precisó que **todo entra por el masivo, nadie captura a mano en Aspel**, y que el masivo usa
el catálogo completo del SAE de esa empresa. Eso reordena las prioridades: los precios salen casi
gratis y **el producto es el camino crítico**.

Un producto nacido en el Facturador necesita ir y volver: nace → se crea en `INVE` de su empresa
(**SAE rechaza claves que no existen**, `export_sae.py:25`) → regresa por el espejo de claves para que
el masivo lo reconozca (`export_sae.py:268-269`). Dos huecos:

1. **No hay camino de escritura para 04 y 05.** Solo existe alta para 02 (`sheets_push.py:7889`) y 03
   (`ehmo_pedidos.py:11320`). El dueño pidió alta automática en las cuatro.
2. **La validación falla hacia el lado inseguro, y la 05 está justo en ese hueco.** Sin espejo del
   catálogo para esa empresa, `export_sae.py` **se salta la validación entera** (`if cat:`, comentario
   en `:263`): no bloquea, exporta, y el SAE rechaza después en silencio — el precedente FRESADOMOPZ
   del 14-sep. Verificado contra producción: **`claves_sae` solo tiene 02, 03 y 04; de la 05 no hay
   una sola fila.** Así que si se le da de alta un producto a la 05 sin espejar antes su catálogo, el
   masivo exportará sin validar nada.
   **Corrección declarada:** una versión anterior de este documento decía que el espejo de claves no
   corría. Es falso. El agendador termina con excepción al escribir su bitácora (TCC de macOS sobre
   `~/Documents`), pero **muere después de sincronizar**: las tres empresas tienen sus claves al día
   (02: 2,043 · 03: 1,067 · 04: 1,075, todas del 20-sep 05:30). Lo que se pierde es el reporte por
   empresa, no el trabajo. El arreglo es una línea —mover la bitácora fuera de `~/Documents`— o
   envolverlo en node como ya hace el agente de correo con `lanzador.js`.

**Choque que el dueño debe resolver antes de incluir la empresa 05:** está excluida a propósito, y el
motivo está escrito en el código — *«La 05 se queda fuera a propósito: CODISEL tiene clave en las dos
empresas y colgarlas de la misma plaza detiene el masivo»* (`facturador_espejo.py:57-59`).

### 12.3 — Notas de crédito: lo urgente no es construirlas

El Facturador no puede emitirlas (sin modelo, migración, servicio, endpoint ni UI; solo una columna de
tipo de comprobante clavada en `"I"`). Pero antes que eso:

- **El espejo las ignora.** Lee solo `FACTF04`; las notas de crédito viven en otra tabla
  (`FACTD04`/`FACTG04`) que **ningún archivo del bot consulta jamás**.
- **`GET /reportes/ventas` está sobreestimado con certeza** (`reportes.py:179-187`): suma totales de
  facturas sin restar una sola nota de crédito. Eso es hoy, no es hipótesis.
- **Si la cartera de la 04 está inflada** depende de cómo el SAE registre la aplicación de la NC en
  `CUEN_DET04`: si es abono (`TIPO_MOV='A'`) el saldo ya viene neteado; con otra convención, la
  cartera está inflada por el monto exacto de las notas de crédito. **NO VERIFICABLE DESDE EL CÓDIGO** —
  lo decide un `SELECT` contra el SAE, pendiente de autorización. Precedente de cuánto escala un
  descuadre así: `MSJ_CANC`, ~$1.5M cobrados de más (`models/factura.py:96-99`).

---

## 12-bis. Las preguntas, como se formularon

**Bloquean el primer paso:**

1. **¿Autorizas commitear los 6,730 renglones del bot en una rama con nombre, sin mergear a `main`?**
   Sí → la puerta de higiene se abre esta semana. No → todo lo demás queda congelado y el bulto sigue creciendo.
2. **¿El canal de correo entra al alcance del retiro?**
   Sí → hay que ponerlo en git y darle identidad propia antes de tocar la hoja (+2-3 días).
   No → hay que apagarlo el día del retiro, porque un interruptor por perfil no lo alcanza y es hoy la tubería ciega.
3. **¿Aceptas por escrito perder «Requisición Folio» y el descuento por línea, o hay que modelarlos?**
   Aceptar → cero trabajo. Modelar → +1 migración y tocar el schema de ingesta.

**Pueden esperar:**

4. **D24 — ¿se aprueba que el Facturador sea el origen del catálogo y los precios?**
   Sí → el aplicador al SAE entra al alcance (+5-7 días). No → las 9 escrituras directas al SAE se quedan en el bot como excepción declarada.
5. **D18 — ¿el Facturador guarda el archivo original, o basta el link de Drive?**
   Guardar → P12 entra. Link → Drive queda como dependencia permanente.
6. **D21 — las 8 remisiones de Tabasco que divergen: ¿se regeneran los masivos o se corrige a mano en el SAE?**
7. **Notas de crédito de la empresa 04: ¿se quedan en el SAE hasta el corte (y el espejo debe leerlas para no inflar la cartera), o entran al Facturador?**

> **Fuera de lista, por seguridad y no por alcance** — dos cosas que aparecieron auditando y que no
> se pueden callar, ninguna depende de este dictamen y ninguna se tocó:
>
> 1. `docs/ESTADO.md` (pendiente 5 del 19-sep) registra que la contraseña de la base de producción
>    quedó impresa en un transcript el 17-sep. Rotarla es decisión tuya.
> 2. La tabla `public.respaldo_codigo_sae_20260917` (42 filas, respaldo del backfill de clave SAE)
>    está **sin Row Level Security** en producción — legible y escribible con la llave anon. Todas
>    las demás tablas la tienen activa. La remediación es un `ALTER TABLE … ENABLE ROW LEVEL
>    SECURITY`, pero activarla sin políticas corta todo acceso: es decisión tuya, y no apliqué nada.

---

## 13. REGISTRO DE NO VERIFICABLE DESDE EL CÓDIGO

| Afirmación | Qué haría falta |
|---|---|
| La tasa real de órdenes que caen a REVISAR | Consulta agregada a producción sobre `oc_recibidas` con ventana temporal |
| Que las hojas reales de Google tengan hoy los encabezados que el código escribe | Abrir los libros (no se hizo: la auditoría no toca Google) |
| Que `alias_aprendidos.json` esté íntegramente migrado a `producto_alias` | Comparar el archivo del bot contra la tabla de producción |
| Que el incidente `su_pedido` HO-34VIL-MIE haya roto producción | Sin registro en PLAN ni `docs/ESTADO.md`; solo un comentario en `oc_recibidas.py:1131-1141` |
| Que el conector del espejo esté corriendo ahora mismo y al día | Observar el proceso en la Mac mini |
| Qué personas consumen hoy cada PDF/Excel que genera el bot | Preguntar al dueño |
| El tramo 02→19-sep del proyecto (91 PRs, 16 migraciones) | `docs/ESTADO.md:12-16` declara el hueco; hay que leer `git log 9021dfa..main` |

**Ninguna conclusión del dictamen se apoya en una línea de este registro.** El veredicto descansa en
`facturador_conciliar.py:12,52-54`, `index.js:6969-6977`, la ausencia de `master.activo` (0 hits) y
la ausencia del contrato de propuesta (0 hits en modelos y migraciones) — todo verificado en el
árbol de trabajo.

---

## Apéndice — cómo se hizo

Ocho flujos con lentes en paralelo y verificación adversarial por flujo: **38 agentes**, todos
completados. Censo de tuberías (5 lentes + verificador independiente que rehizo los conteos con greps
propios), inventario de pérdida (3 lentes + refutador que bajó 4 veredictos MIGRA), simulacro de
apagón (9 escenarios + árbitro que rechazó las conclusiones sin cita), reloj del sistema (4 lentes +
cierre), contrato/candados/permisos/concurrencia (4 lentes + verificador), aserciones (confirmadora y
refutadora **a ciegas sobre la misma lista**, más una lente de base de producción, más una
conciliadora que solo aceptó el veredicto con evidencia), y reversa/pruebas/higiene.

El límite de gasto cortó el workflow cuatro veces. Mientras tanto, el auditor principal ejecutó
directamente varias de las lentes pendientes; al completarse el workflow, **sus resultados se
contrastaron contra los míos y las lentes corrigieron tres conclusiones mías**, que quedaron
incorporadas: el veredicto de la suite de pruebas (yo la declaré refutada; es **condicional**), la
inferencia sobre el corte de Pachuca (no es que el script no corriera: **algo revinculó las listas
después**) y el conteo de remisiones de Pachuca (156, no 157). Donde hubo discrepancia, gana la
evidencia con `archivo:línea`.

La suite del backend se corrió **dos veces**, siempre contra la base **local** del 5434 y nunca
contra producción: con la base arriba, **639 pruebas, 0 saltadas, exit 0**; con la base inalcanzable,
**108 pasadas, 531 saltadas, exit 0**. Las lecturas de producción fueron `SELECT` de una sentencia,
acotados al inquilino vivo, sobre el proyecto `qwffsaxoeehwwdzaytqb` de la organización de Frutas
Kelly; no se tocaron los proyectos Mini Conta ni smart_supply, ni se usó ninguna herramienta de
escritura.
