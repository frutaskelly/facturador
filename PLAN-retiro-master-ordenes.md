# PLAN — Retiro del Master Órdenes

> Propuesta del 1 de septiembre de 2026, sin código. Verificada contra el código del bot
> (`index.js`, `sheets_push.py`, `ehmo_pedidos.py`), el Facturador en `main e03912c`, el
> `PLAN-migracion-master-facturador.md` del 28-ago y 30 días de bitácora del router del bot
> (2 al 31 de agosto). Versión legible con tablas y diagrama: artifact «Retiro del Master Órdenes».
> Decisiones D9 a D17 resueltas por el dueño el 2 de septiembre de 2026 (marcadas en el texto);
> D18 (dónde vive el original de cada orden) queda propuesta.

## Objetivo

El bot de WhatsApp deja de escribir y leer la hoja de Google («Master Órdenes» de Balles/Jubran y
«Master EHMO» por perfil). Cada comando del chat se resuelve contra el Facturador, y todo producto
o precio que nazca en el SAE aparece en el Facturador por un espejo de catálogo. La operación se ve
igual desde el teléfono; cambia dónde vive la verdad. Este plan es independiente del corte de
facturación del SAE, que sigue su calendario por cliente.

## Resumen

- **El Master hace tres trabajos**: almacén de órdenes, estado de negocio (SIN CLAVE / SIN PRECIO /
  PRECIO EN CONFLICTO, lote EXTRA / REPOSICIÓN, fecha de bodega y reparto, amarre con la factura,
  quién hizo qué) y reportes (resumen de órdenes, hoja de armado, sin precio, Master Facturas,
  estado de cuenta, cierre de periodo). El Facturador ya hace bien el primero (bandeja + remisiones con
  ingesta idempotente); la conciliación de cada 6 h existe, pero no avisó de 14 órdenes perdidas
  que se recuperaron a mano el 31-ago (pendiente 10, se cierra antes de apagar nada); le faltan
  piezas del segundo y casi todo el tercero.
- **46 comandos vivos en dos motores** que no comparten código (`sheets_push.py` para Balles/Jubran,
  `ehmo_pedidos.py` para EHMO/MAFAN). 7,294 comandos en 30 días solo en la tubería de Balles.
- **El bot se vuelve un cliente delgado**: un comando = una llamada con vista previa y aplicar. El bot
  conserva la conversación; el Facturador conserva la verdad. Un solo cruce de productos: el del
  Facturador (los alias del bot ya se migraron el 28-ago).
- **El SAE sigue igual hasta el corte**: factura, importa masivos, recibe altas de producto con
  confirmación. Los pedidos y las facturas entran al SAE SOLO por el Excel masivo que genera el
  Facturador; nada se inyecta directo (D9). El espejo de catálogo lleva al Facturador lo que nazca en
  el SAE, por comando del bot o por captura directa en Aspel: el SAE manda y el Facturador lo sigue (D10).
- **El Master se apaga por perfil con un interruptor**: Balles+Jubran → EHMO Pachuca → Villahermosa.
  Queda de solo lectura dos semanas y se archiva.

| Etapa | Trabajo Facturador | Trabajo bot | Calendario |
|---|---|---|---|
| 0 · Congelar y contrato | 2–3 d | 5–6 d | semana 1 |
| 1 · Paridad de lectura | 6–8 d | 5–6 d | semanas 2–3, con el Master vivo |
| 2 · Paridad de escritura | 7–9 d | 6–8 d | semanas 3–5 |
| 3 · Catálogo y precios | 5–7 d | 5–6 d | semanas 5–6 |
| 4 · Apagado por perfil | 1 d | 2–3 d | 3 semanas de calendario, un perfil a la vez |
| 5 · Limpieza | 1 d | 4–5 d | al terminar el último perfil |

Días de trabajo aproximados: Facturador 21 a 29, bot 27 a 34. La columna del bot incluye lo que cada fase del plan del agente exige para que su etapa funcione: en la 0, secretos fuera de disco, prueba automática real y perfil declarado por grupo; en la 1, el comparador ampliado y las 86 llamadas a la hoja redirigidas; en la 2, los once estados en memoria convertidos a propuestas; en la 3, el conector SAE de solo lectura; en la 4 y la 5, la conciliación sustituta y el borrado con lista cerrada. Las etapas 1 a 3 corren con el Master vivo y un comparador que exige resultados idénticos antes de avanzar.

## Comandos más usados (30 días, bitácora `logs/router_decisions.jsonl`)

| Comando | Veces |
|---|---|
| consulta de precio / ficha de producto (`ver_producto`) | 1,296 |
| pregunta libre (`smart`, incluye resumen de órdenes) | 613 |
| intake de PDFs (`add`) | 605 |
| confirmaciones sí/no | 515 |
| pendientes de la OC | 448 |
| alta / cambios de producto en el SAE | 305 / 250 |
| **pendiente abandonado** (conversación colgada) | 236 |
| resumen sin precio (mayoría la alerta automática) | 219 |
| actualiza OC + líneas | 155 |
| masivo pedido / factura | 127 / 28 |
| hoja de armado | 72 |
| bodega / fechas | 44 |
| crear pedido SAE (escribe FACTP02) | 37 |

EHMO no lleva bitácora; aproximado por `logs/bot.log`: factura 75, masivo 54, agrega OC 42,
armado 35, agrega a la lista 31, lista de precios 18, sin precio 13, reemplaza 11, sin clave 11.

Los 236 «pendiente abandonado» son la fricción más alta: la memoria de la confirmación vive en el
proceso del bot y se pierde al reiniciar. La propuesta la mueve al Facturador como propuesta con
vigencia.

## Seis reglas de diseño

1. **Un comando, una llamada.** El bot pide y muestra; no arma renglones ni calcula totales.
2. **Vista previa y aplicar.** Toda operación que cambia algo devuelve antes/después con un número
   de propuesta (vigencia 15 min). El «sí» del mismo participante aplica esa propuesta.
3. **Un solo cruce de productos.** El bot manda texto, cantidad y unidad del cliente; el Facturador
   devuelve clave, precio y estado por partida.
4. **El SAE manda en catálogo y precios hasta el corte.** Nada se da de baja por iniciativa del
   sistema; el Facturador nunca escribe en la base del SAE, y los pedidos y facturas entran a Aspel
   únicamente por el Excel masivo (D9). La única escritura directa que queda es el alta de producto y
   precio desde el chat, con confirmación, como hoy.
5. **Las reglas de la casa se conservan**: folios del sistema sin ceros ni espacios; el masivo deja
   rastro y no estampa; escrituras cruzadas con confirmación; lo facturado no se toca.
6. **Quién hizo qué**: la «Nota WhatsApp» del Master se vuelve actor externo en la bitácora.

## Los comandos, uno por uno

Estado: EXISTE (ya lo hace el Facturador) · PARCIAL · FALTA · SE QUEDA (sigue en SAE/bot) · SE RETIRA (por decisión del dueño: D9, D13).

### A · Entrada de órdenes
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| PDF de OC/requisición (Balles, Jubran) | 605 | Master+Summary, Drive, alarmas contra SAE; bandeja en 2º | La bandeja cruza y cotiza; el bot arma el acuse con esa respuesta; el archivo viaja con la orden (P12), Drive de respaldo | EXISTE |
| Foto/Excel de pedido EHMO | ≈ | Cruce en el bot, Pedidos+Resumen, hojas por día a Drive; espeja | El bot lee la foto; cruce y hojas por día del Facturador (P4) | PARCIAL |
| Foto de extras/reposiciones | ≈ | Lote EXTRA/REPOSICIÓN + Papelera; espeja | Tipo de partida en orden y remisión (P1) | PARCIAL |
| `nueva OC <ubicación> <día>` | ≈ | Master EHMO; **no espeja** | Ingesta con canal MANUAL (el modelo ya lo admite) | PARCIAL |

### B · Edición de la orden
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| `actualiza OC 518` + líneas (kilos) | 155 | Reescribe cantidades en el Master | Edición de partidas con vista previa (P2); pendiente → orden, remisión → remisión, facturada → rechazo | FALTA |
| `actualiza/borra/agrega renglón N OC F` | <10 | Una fila del Master | P2 por partida | FALTA |
| `bodega <folio> <fecha>` / reparto | 44 | Summary G/H (eje del armado) | Fechas en orden y remisión (P1) + endpoint por lote (P2); «¿bodega o reparto?» se queda en el bot | FALTA |
| `cancelar OC <folio>` | 12 | Marca CANCELADA; avisa pedido/factura | Descartar (existe); aviso desde el espejo | EXISTE |
| EHMO: kilos, agrega, quita, partida a extra, recruce, semana | ≈3·42·6·8·—·2 | Editan Master y reenvían estado final; si ya hay remisión, diverge | Edición directa con vista previa (P2); lote y semana como campos (P1) | PARCIAL |
| `OC <folio> reemplaza <a> con <b>` | ≈11 | Alias en JSON local; **no espeja** | Alias del Facturador (endpoint ya al alcance de la clave) | EXISTE |
| `remisiones` / `une remisión` | nuevo, sin commitear | JSON local + Master | Unir remisiones con vista previa (P2); no debe nacer en el Master | FALTA |

### C · Armado y entrega
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| `hoja de armado` / `lista de compras` (Balles): por OC, fecha, rango, semana, periodo; FRUVE/SECOS | 72 | PDF pivote del Master; pide bodega o excluir | P4 desde remisiones/órdenes, eje = fecha de bodega (P1), FRUVE/SECOS por categoría | FALTA |
| `hoja de armado <folio>` / `armado pdf` (EHMO) | ≈35 | Excel/PDF por entrega con extras y reposiciones | P4 por entrega (ubicación × día) con tipos de partida | FALTA |

### D · Catálogo y precios (SAE ↔ Facturador)
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| `crea producto CLAVE — UNIDAD — DESCR $` (Balles) | 305 | INSERT INVE02 + PRECIO_X_PROD02 con «sí» | Igual en SAE + espejo puntual (P6): producto, código SAE y precio en segundos | FALTA |
| `agrega <producto> a la lista de <proyecto> en <$>` (EHMO) | ≈31 | Transacción SAE + relleno del Master | Igual + espejo puntual + re-evaluación de órdenes pendientes | FALTA |
| `actualiza el precio a <$> <CLAVE>`, precios OC, categoría, ficha | 250 | Escriben SAE con «sí» | Igual + espejo puntual | FALTA |
| `¿precio de <CLAVE>?` / ficha | 1,296 | sqlcmd al SAE en cada pregunta | Catálogo y listas del Facturador espejeadas; sin túnel | PARCIAL |
| `dame la lista de precios` | ≈18 | Lista 3 / 5–9 del SAE | PDF/Excel de la lista (existen); falta permiso (P9) | PARCIAL |
| `busca SAT` | — | Catálogo en el bot | Catálogo SAT del Facturador, ya al alcance | EXISTE |
| `cotiza · CLAVE · Desc` / `actualizar rq` | 8 · 0 | Cotizador aparte; corrección escribe SAE | Cotizador del Facturador (replica el PDF); permiso (P9) + espejo puntual | PARCIAL |

### E · Pedidos y facturas en el SAE
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| `crea pedido/factura massivo` | 127·28·≈54 | .xls del bot; OC en columna FOLIO (pendiente 12) | Export del Facturador (existe, ya en el cliente del bot) + folio real leído del SAE (P7); único camino de pedidos y facturas al SAE (D9) | PARCIAL |
| `crear pedido SAE OC <folio>` | 37 | INSERT FACTP02 con «sí» | Se retira (D9): el pedido entra solo por el masivo de pedido del Facturador con folio real; el mismo comando responde con el masivo | SE RETIRA |
| `factura de la OC` / `¿timbrada?` / `pendientes de la OC` | 5·448 | FACTF02 + CFDI02 | Desde el espejo; falta lectura al alcance (P8) | PARCIAL |
| `factura ZHGO 301` (PDF), totales, ejemplo factura | 19 | SAE | PDF sigue del SAE; totales del espejo | SE QUEDA |
| `amarra OC con factura` | ≈ | JSON local | El espejo liga por OC y folio interno; manual = corregir su_pedido | EXISTE |
| `master facturas` / `conciliación` (viernes 17 h) | — | Master Facturas; Master↔SAE | Espejo 30 min + conciliación Facturador↔SAE (existen) + entregas sin facturar/dobles (P5) | PARCIAL |

### F · Consultas y reportes
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| `resumen de órdenes` (por semana; falta facturar antes de facturado) | en 613 · ≈8 | Summary/Resumen; Excel en EHMO | Resumen del Facturador (P3): texto y Excel por día/semana/cliente/punto | FALTA |
| `sin precio` / `sin clave` + alerta 7/10/13/16/18 h | 219·≈24 | Master | Consulta agregada de la evaluación de bandeja (P5); horas iguales (D14) | PARCIAL |
| `revisión` / `detalles OC` | ≈2 | Master | Detalle (existe) + duplicados/gemelas (P5) | PARCIAL |
| `estado de cuenta` | 30 | Sheet aparte + SAE | Cobranza del Facturador (existe); pagos de Balles/Jubran se capturan en el Facturador desde ya (D12) | PARCIAL |
| `impuestos` / IVA / IEPS | ≈60 | IMPU02 | Sin cambio | SE QUEDA |
| pregunta libre | 613 | Claude + Master | Claude + resúmenes del Facturador | PARCIAL |
| `cierra el periodo con carpeta X` | — | Archiva pestañas | Desaparece; Excel del periodo a Drive a petición (D13) | SE RETIRA |

### G · Operación del bot
`grupos`, `ayuda`, manuales, `prueba alerta`, `conectar facturador`: sin cambio.

## Piezas nuevas del Facturador

- **P1 Estado de negocio en la orden y en la remisión**: fecha de entrega en bodega y de reparto;
  tipo de partida (normal/extra/reposición); semana y día; actor externo en la bitácora. Visibles y
  editables en bandeja y remisión; la importación del Excel del Master (que ya lee «Entregar
  Bodega») deja de tirar el dato.
- **P2 Edición de partidas por API con vista previa**: cantidad/kilos, agregar, quitar, recruzar,
  reemplazar alias, cambiar semana, fechas por lote, cancelar, unir remisiones. Antes/después con
  número de propuesta; aplicar exige ese número. Pendiente → orden; remisión → remisión; facturada →
  rechazo; con `export_sae_at` → aviso (pendiente 11).
- **P3 Resumen de órdenes**: por día/semana/cliente/punto de entrega, falta facturar antes de
  facturado con subtotal, leyendo el estado del espejo. Texto para el chat y Excel.
- **P4 Hoja de armado**: pivote producto × folio por fecha de bodega/rango/semana/periodo, FRUVE y
  SECOS por categoría; variante EHMO por entrega con extras y reposiciones. PDF/Excel con membrete.
- **P5 Consultas de bandeja agregadas**: sin precio / sin clave con importe, pendientes de la OC,
  duplicados y gemelas, entregas sin facturar o facturadas dos veces contra el espejo.
- **P6 Espejo de catálogo SAE → Facturador**: el conector de facturas lee también INVE y
  PRECIO_X_PROD por empresa. Producto nuevo → se crea con presentación, clave SAT, código SAE para
  cada cliente de esa empresa (`producto_clientes.codigo_cliente`) y alias con la descripción. Precio
  nuevo/cambiado → lista correspondiente (3 → Balles/Jubran, 5–9 → proyectos, Tabasco → EHMO VH).
  Nunca desactiva ni borra; duplicados se desempatan por partidas facturadas; si la lista SAE no
  existe o difiere de lo facturado, toma el precio de la última factura no cancelada y avisa. Espejo puntual por
  clave tras cada comando que escribe el SAE, y re-evaluación de órdenes pendientes. Sentido único
  hasta el corte: el SAE manda y el Facturador lo sigue (D10).
- **P7 Masivos con el folio real del SAE**: el bot deja su .xls; usa el export del Facturador y le
  pasa el siguiente folio real del SAE (`_sae_sig_folio_pedido` existe; hoy solo la llama el motor
  EHMO, y solo contra la empresa 02; `cmd_massivo` de Balles no la usa). Cierra el
  pendiente 12 y D1. Con D9 es el ÚNICO camino de pedidos y facturas hacia el SAE: `cmd_pedido_sae`
  (INSERT directo en FACTP02) se retira y el bot deja de escribir FACTP02/FOLIOSF02.
- **P8 Lectura del espejo para el bot**: factura de la OC, estado SAT, totales, pendientes. El PDF
  sigue saliendo del SAE.
- **P9 Permisos de conexión acotados**: leer espejo, leer listas y cotizar, editar partidas con
  propuesta, espejar catálogo, folio sugerido. Nunca `producto:gestionar` ni `cliente:gestionar`.
  Detalle: `factura:espejo` no está sembrado en el catálogo de permisos (solo en código).
- **P10 Bitácora de eventos para el bot** (cola que el bot consulta cada minuto): orden convertida
  en remisión desde la pantalla, partida sin cruzar, factura llegada por el espejo. Opcional para
  apagar el Master; necesario para que los grupos se enteren de lo hecho en pantalla.
- **P11 Datos previos**: pendiente 7 (equivalencia VH + reabrir 35 órdenes), 8 (183 links Drive), 10
  (causa raíz de las 14 órdenes perdidas), 9b (código del cliente en el cotizador). El interruptor solo
  se mueve con los cuatro en cero.
- **P12 El original de cada orden vive en el Facturador**: hoy el bot descarga el PDF/foto a una
  carpeta de la Mac sincronizada por OneDrive y lo sube a Google Drive; el Facturador solo guarda
  el link (`archivo_url`) y 183 órdenes llegaron sin él. El bot manda el archivo junto con la orden
  y el Facturador lo guarda en su propio almacenamiento (Supabase), con link interno en orden y
  remisión; un script re-sube los originales que ya están en Drive. OneDrive/Drive quedan de
  respaldo durante la transición y después son opcionales.

## Cambios en el bot

- **Un cliente único**: las dos primitivas que escriben la hoja se reemplazan por
  `facturador_client.py`. Parseo de PDF/fotos se queda; cruce, precios y totales se van.
- **Interruptor `master.activo` por perfil** (hay que crearlo y exige perfil declarado por grupo;
  hoy Balles/Jubran y EHMO Pachuca viajan sin perfil). Los 6 grupos apagados nacen directo al Facturador.
- **Propuestas en vez de memoria**: de los 11 estados `pending*`, 7 se reemplazan por el número de
  propuesta del Facturador y 4 aclaraciones puras quedan en memoria con TTL; un reinicio no pierde
  la propuesta.
- **Comparador ampliado** (`facturador_comparar.py` ya compara masivos): resumen, armado, sin
  precio y ficha se generan de los dos lados durante las etapas 1–3.
- **Timers cambian de fuente, no de hora**: alerta sin precio, conciliación de viernes y pregunta
  libre leen el Facturador; la conciliación Master↔bandeja se apaga perfil por perfil; el conector
  de facturas gana el espejo de catálogo.
- **Se borra al final**: `alias_aprendidos.json`, `PROYECTO_LISTA_SAE`, lista local de precios,
  `remisiones.json`, `facturas_oc.json`, cierre de periodo, `cmd_pedido_sae`, `cmd_prefactura_sae` y
  `_actualizar_pedido_sae_core` (escrituras directas de pedidos y facturas), el generador propio de
  masivos (`_xls_massivo`, `cmd_massivo*`) y todo el acceso a Google Sheets. Detalle por fases en
  «Plan del agente».

## Plan por etapas

0. **Congelar el Master y fijar el contrato** (semana 1). Nada nuevo entra por el Master; lo sin
   commitear en el bot (remisiones, une remisión, bodega en pedido por ubicación) se commitea o se
   guarda en rama y su versión definitiva se hace sobre el Facturador. Contrato de vista previa y
   aplicar y su núcleo operativo (crear, consultar y aplicar una propuesta persistida con vigencia,
   sin operaciones de negocio todavía), actor externo, idempotencia por mensaje, permisos P9.
   Cerrar P11. *Comprobación*: una propuesta de prueba creada desde el chat sobrevive a un reinicio
   del bot y del backend, y se aplica con el «sí» del mismo participante.
1. **Paridad de lectura** (semanas 2–3; P3, P4, P5, P8, ficha). Cada reporte se genera de los dos
   lados y el comparador exige igualdad. Se empiezan a capturar en el Facturador los pagos de
   Balles y Jubran (D12). *Comprobación*: 5 días hábiles con los 5 reportes idénticos en los 3
   perfiles.
2. **Paridad de escritura** (semanas 3–5; P1, las operaciones de P2 sobre el núcleo de la Etapa 0,
   y P12 con D18). Los comandos de EDICIÓN escriben el Facturador primero y el Master como sombra,
   solo cuando la Etapa 0 haya cerrado la causa raíz del pendiente 10; la ENTRADA de órdenes sigue
   Master primero mientras el perfil esté encendido (regla de oro 1 del 28-ago). La entrada deja de
   cruzar en el bot y manda el original con la orden: archivo si se aprueba D18, link de Drive si no. *Comprobación*: conciliación
   Master↔bandeja en cero 5 días con el Master ya como copia.
3. **Catálogo y precios** (semanas 5–6; P6, P7). Espejo por empresa validado renglón por renglón
   (como la lista de Tabasco el 1-sep); espejo puntual tras comandos; masivos con folio real. Se
   retiran `crear pedido SAE`, `actualizar pedido` y la prefactura que sigue en el código: desde aquí
   pedidos y facturas llegan al SAE solo por el masivo (D9).
   *Comprobación*: las 7 listas vivas iguales renglón por renglón, SAE = Facturador (02 → 3
   Balles/Jubran, 5 CEREZOS, 6 SEGURIDAD PÚBLICA, 7 SECRETARIO NERI, 8 DIF, 9 HOSPITALES; 03 → 4
   EHMO TABASCO; fuera las de fábrica y la 4 CHANEQUES); producto creado por el bot aparece en la
   bandeja antes del siguiente pedido; 2 masivos de pedido y 2 de factura importados sin error.
4. **Apagar el Master por perfil** (3 semanas de calendario). Balles+Jubran → EHMO Pachuca →
   Villahermosa; hoja de solo lectura; conciliación Master↔bandeja apagada con cada perfil; Drive
   sigue recibiendo los originales como respaldo (con D18 aprobada, el Facturador ya guarda su copia).
   *Comprobación*: una semana por perfil sin abrir la hoja para resolver nada.
5. **Limpieza**. Borrar código de Sheets y archivos locales; exportar cada Master a Drive como
   archivo muerto; actualizar manuales. El corte del SAE sigue su plan por cliente.
   *Comprobación*: cero referencias a Google Sheets en el código del bot y `npm test` en verde
   tras el borrado.

## Plan del agente

Hoja de ruta única del agente de WhatsApp de Smart Supply, 2-sep-2026. Sustituye los planes del bot del 13 y del 21 de agosto y se
subordina a esta propuesta y al plan de migración del 28-ago. Sus fases van aparte (0 a 6) y cada
una dice de qué etapa del retiro depende.

### Qué es el agente después de esto

Un cliente delgado de WhatsApp. Recibe PDFs, fotos, Excel y texto en los grupos, los parsea, pide
confirmación por lista cerrada de sí/no y resuelve cada comando con una llamada al Facturador de
vista previa y aplicar. No guarda ninguna verdad de negocio: sin Master, sin listas de precios
locales, sin cruce propio, sin estados de negocio en memoria. Conserva su identidad de WhatsApp, los
parsers, el router determinista con IA híbrida solo para lecturas, y su base propia (mensajes,
decisiones, acciones, correcciones) como telemetría y aprendizaje. El binario es idéntico para todos
los robots; lo que distingue a un tenant son filas en la base.

### Lo que se conserva

- **21-ago, arquitectura DB-vs-agente**: «binario idéntico, tenant = filas», lista cerrada
  SI_EXACTO/NO_EXACTO, telemetría en `decisiones`/`acciones`, circuito de aprendizaje (F4).
- **13-ago**: el embudo de acceso al SAE. Precisión: son DOS embudos, uno por motor
  (`_sae_query`/`_sae_exec_sql` y `_sae_query_ehmo`/`_sae_exec_ehmo`); los dos se extraen en la Fase 3.
- **21-ago, piloto**: guardas de «factura la OC», runbook de recuperación, cruce compra↔venta con
  Mini Conta. Solo cambian de fecha.
- **28-ago**: espejo SAE, corte por cliente, outbox, y «el bot escribe el Master primero» para la
  ENTRADA de órdenes mientras el perfil siga encendido.
- **De esta propuesta**: las seis reglas, P1-P12, D9-D18.

### Lo que se descarta

- «No hay espejo, corte directo» y «primero un cliente nuevo» (21-ago): superados el 28-ago.
- Series por región (BP/EP/ET…) y «cada región es un cliente»: el Facturador modela cliente, plaza y
  punto de entrega con la serie en el vínculo, y sigue las series reales del SAE.
- Usuario-bot con JWT y refresh cada hora: el bot opera con la clave `fi_ss_` y permisos P9; solo se rota.
- Serie-folio por OC en el Master y `facturas_oc.json`: el Facturador liga orden, remisión y factura.
- La tabla `pendientes` de F2: la propuesta con vigencia la sustituye. El resto de F2 (LIDs fuera del
  archivo de secretos, bloque `ehmo` duplicado) entra en la Fase 0.
- `alias_productos` con unidad y las dos xlsx locales: el vocabulario vive en `producto_alias` con
  alcance, y ahí la unidad cambia el cruce.
- `ubicaciones` y `tenant_recursos` de F3: ya son equivalencias y plazas. Sheets muere; Drive sigue según D18.
- Cualquier fase del router donde la IA despache escrituras o juzgue una confirmación.

### Las siete fases

Puerta de cada fase: `npm test` real (hoy `echo Error && exit 1`): `node --check`, `py_compile` de
los tres motores, `probar.py` con casos dorados, `eval_router.py` y pruebas de contrato de
`facturador_client.py` contra el tenant demo de la BD local (:5434), nunca contra los dos tenants
de producción. Menos de cinco minutos.

| Fase | Va con | Criterio de salida | Bot | Facturador |
|---|---|---|---|---|
| 0 · Piso firme | Etapa 0 | git limpio; 0 secretos en `sheets_config.json`; `npm test` verde; una propuesta de prueba sobrevive al reinicio del bot y del backend; cada grupo activo con perfil declarado | 5–6 d | 2–3 d |
| 1 · Paridad de lectura | Etapa 1 | 5 días hábiles con los reportes idénticos en los 3 perfiles | 5–6 d | 6–8 d |
| 2 · Paridad de escritura | Etapa 2 · D18 solo para P12 | conciliación en cero 5 días con el Master como copia; 0 cruces del bot; toda orden nueva con su original (archivo o link); abandonados 236 → <20/mes | 6–8 d | 7–9 d |
| 3 · Catálogo, precios y masivos | Etapa 3 | las 7 listas vivas iguales; 0 INSERT/UPDATE del bot en FACTP02, PAR_FACTP02, FACTF02, PAR_FACTF02, CUEN_M02, FOLIOSF02, TBLCONTROL02; motores sin lecturas SQL del SAE | 5–6 d | 5–7 d |
| 4 · Apagar el Master y borrar | Etapas 4 y 5 | una semana por perfil sin abrir la hoja; cero referencias a gspread/Sheets en los tres motores | 6–8 d + 3 sem | 1–2 d |
| 5 · Corte del SAE por cliente | Migración 28-ago · Fase 3 · D20 | por cliente: 2 periodos a factura timbrada sin intervención; 0 CFDI duplicados | 3–4 d | 2–3 d |
| 6 · Plataforma | sin calendario | alta de tenant/cliente solo con filas; conciliación de dinero en cero 2 semanas | 5–7 d | 8–12 d |

**Fase 0 · Piso firme** (semana 1, con la Etapa 0)
- Causa raíz del pendiente 10 con test que lo reproduzca; después commit o rama de los 317 renglones
  sin commitear (mismas fechas y archivos que las 14 órdenes perdidas).
- **Perfil explícito por grupo**: hoy Balles/Jubran y EHMO Pachuca viajan con perfil nulo y
  `facturador_client.py` pone `'ehmo'` por omisión; sin perfil `balles` (D5 del 28-ago) el
  interruptor por perfil no puede separarlos.
- **Secretos fuera de disco**: rotar `fi_ss_` (D7), `anthropic_key`, password SAE,
  `agente.service_key`, `cotizador.api_key` y la llave de Google → `EnvironmentVariables` del plist
  o `.env` 600 que cargue `bot.sh`; probar la nueva antes de revocar la vieja. Borrar
  `secretos-backup-20260714.zip`, `qr.png`, 73 `.bak` de código y 33 de `data/`; revocar
  `gemini_key_pendiente`. Canónico `SmartSupply/bot`; congelar `Cristian/SAE-Updates` y rotar sus credenciales.
- `_aprenderLidEquipo` deja de reescribir `sheets_config.json`: los LIDs van a `grupos.config` (o a
  Conexiones del Facturador si D19 lo decide). Unificar el bloque `ehmo` raíz con `perfiles.ehmo`.
- Puente del pendiente 12: `cmd_massivo` llama a `_sae_sig_folio_pedido` (el motor EHMO ya la usa).
- Núcleo de P2 en el Facturador (crear, consultar y aplicar una propuesta con vigencia) y un
  comando de prueba que lo recorre; permisos P9 (`factura:espejo` sembrado); P11 en cero.

**Fase 1 · Paridad de lectura** (semanas 2–3, con la Etapa 1)
- Ficha/precio, resumen, pendientes de la OC, sin precio, hoja de armado y factura de la OC se
  generan de los dos lados. `facturador_comparar.py` crece a esos reportes; la ficha compara precio
  por lista y proyecto, no solo la clave (ZMAFAN 168).
- Las 86 llamadas a `runSheets(` y las lecturas del motor EHMO se redirigen a `facturador_client.py`
  detrás de `master.activo` (hay que crearlo). La ficha no cambia de fuente hasta la Fase 3 (D10).
- `cotiza` se sirve del cotizador del Facturador. Cada comando llama `registrarAccion` (existe,
  nadie la llama): es la medida de las fases siguientes.
- Arranca D12 y con ella el fix de fechas del Facturador (el «hoy» de cobranza sigue en UTC).
- La lista blanca del router híbrido solo crece con lecturas que ya sirve el Facturador.

**Fase 2 · Paridad de escritura** (semanas 3–5, con la Etapa 2; solo P12 depende de D18)
- Toda edición va por P2 sobre P1 (actualiza OC, renglón, bodega/reparto, cancelar, kilos, agrega,
  quita, extra, recruce, semana, une remisión, `actualizar rq`). El cruce del bot deja de correr. El
  original viaja con la orden (P12).
- **Once estados `pending*` en memoria, no cinco** (1 Map + 10 variables). Siete pasan a número de
  propuesta sobre el núcleo de la Fase 0; ante un «sí» suelto el bot pregunta al Facturador qué propuesta viva tiene ese grupo y
  participante. Las cuatro aclaraciones puras (bodega/reparto, fecha, semana, lista) quedan en
  memoria con TTL de 5 min y se vuelcan a disco como ya hace `pendingRenglon`.
- **Regla de escritura mientras el perfil siga encendido**: la ENTRADA sigue Master primero (fila y
  Drive → bandeja con outbox; si el Facturador no responde, acuse «recibida, cruce pendiente» y el
  outbox la reprocesa). Las EDICIONES las calcula el Facturador y el Master se reescribe con esa
  respuesta; sin respuesta, se encola y el chat recibe «en cola». Relajación acotada de la regla de
  oro 1, solo para ediciones.
- P10 entra aquí: lo hecho en pantalla sobre una orden de un grupo llega al grupo en <5 min.
  Detector mínimo de correcciones: respuesta humana que contradice la interpretación → fila en `correcciones`.
- `enviar_oc` y el pipeline de fotos dejan fila en `acciones` (sistema=facturador,
  referencia=origen_externo, wa_id): base de la conciliación sustituta de la Fase 4.
- D17: la visión de fotos sigue en `ehmo_pedidos.py`; libro, reintentos y watchdog en `index.js`.

**Fase 3 · Catálogo, precios y masivos** (semanas 5–6, con la Etapa 3; se cierra antes del primer corte)
- P6 con la regla del dueño: entre dos claves vivas manda lo último facturado; si la lista SAE no
  existe o difiere de lo facturado, el espejo toma el precio de la última factura no cancelada
  (PAR_FACTF) y avisa. `PROYECTO_LISTA_SAE` deja el bot y se vuelve equivalencia del Facturador.
- **Se retiran las escrituras directas de pedidos y facturas (D9)**: `cmd_pedido_sae` (:8990),
  `_actualizar_pedido_sae_core` (UPDATE FACTP02/PAR_FACTP02), `cmd_prefactura_sae` (:8463, sigue en
  el código aunque el comando se quitó el 03-ago) y el generador propio de masivos (`_xls_massivo`,
  `cmd_massivo*`). El comando devuelve el export del Facturador con el folio sugerido de `_sae_sig_folio_pedido`.
- Las lecturas SQL del SAE de los dos motores pasan a `conector_sae/` (solo lectura, clave de
  conexión tipo SAE separada de la del bot). Las escrituras que quedan (producto, precio, categoría,
  cambios de ficha, `actualizar rq`) viven en un módulo pequeño del bot, con «sí» y espejo puntual.
  El conector nunca escribe.
- Candado del pendiente 11: no repreciar remisiones con `export_sae_at` sin regenerar o corregir el
  masivo. Extender `SERIES_POR_EMPRESA` a 04/05 cuando abra el onboarding.

**Fase 4 · Apagar el Master por perfil y borrar** (3 semanas + 1, con las Etapas 4 y 5; no espera al corte)
- `master.activo=false` Balles+Jubran → EHMO Pachuca → Villahermosa; hoja de solo lectura dos
  semanas; timers cambian de fuente sin cambiar de hora (D14); ayuda y manuales el mismo día.
- Conciliación sustituta cada 6 h: todo PDF/imagen de grupo cliente en `mensajes` debe tener su fila
  en `acciones` y su `oc_recibidas` por ancla (`WA:<jid>:<folio>` / `EHMO:<perfil>:<folio>`).
- Borrado con lista cerrada: acceso a Google Sheets, cierre de periodo (D13), `_xls_massivo`,
  `cmd_massivo*`, `cmd_pedido_sae`, `cmd_prefactura_sae`, `alias_aprendidos.json`,
  `PROYECTO_LISTA_SAE`, las 2 xlsx, `remisiones.json`, `facturas_oc.json`, `pedidos_sae.json`,
  `prefacturas_sae.json`, `bodega_overrides.json`, `folios_grupo.json`, `estado_cuenta_sheet_id`,
  `estado_cuenta_proveedor` y el bloque `grupos` del config (queda la tabla). Cada Master exportado a
  Drive como archivo muerto; Drive sigue de respaldo según D18.
- Líneas de los tres motores medidas antes y después, publicadas en ESTADO.

**Fase 5 · Corte del SAE por cliente** (2–3 meses, con las Etapas 3 y 4 de la migración del 28-ago;
depende de la Fase 3 para ese perfil, NO del apagado del Master; depende de D20)
- «factura la OC», «registra pago» y «estado de cuenta» como vista previa, «sí» y aplicar, con las
  guardas del 21-ago (candado $0, anti-duplicado por OC, método de pago explícito, verificar factura
  existente antes de crear).
- Antes del primer corte: runbook de recuperación (Baileys sin sesión, Facturador caído a medio
  timbrado, clave revocada), REP F3 y zona horaria confirmados en prod, temas E–K del manual como
  casos dorados, respaldo de la Mac Mini.
- Checklist por cliente: CSD/RFC verificados; `series.folio_actual` = último folio del espejo;
  saldos PPD cuadrados al peso contra SAE; inventario según D4; candado fuera.
- Al cortar el último cliente desaparece la escritura al SAE; `conector_sae/` queda de consulta
  histórica; D10 se invierte.

**Fase 6 · Plataforma** (sin calendario; se abre por cliente o tenant nuevo)
- Base del agente: columna de tenant/conexión en `grupos` y `perfiles` (hoy `cliente` es texto sin
  tenant); `perfiles` mínimos para parsear y rutear; `feature_flags`; `conexiones_sae` con password en
  Vault; `sheets_config.json` = bootstrap.
- Aprendizaje: `correcciones` → casos dorados y few-shot; `eval_casos`/`eval_corridas` para el examen
  sombra por cliente nuevo; `reglas_negocio` con las decisiones del dueño.
- Onboarding con `PLAN-onboarding-clientes-sae.md` (A–E) para las 4 razones sociales nuevas del SAE
  y los 6 grupos apagados, diferidos por el dueño (pendiente 4); nacen directo al Facturador (D15).
- Mini Conta en dos piezas: compra por WhatsApp (no depende del corte; paralelo a Fases 1–4) y cruce
  compra↔venta leyendo facturas espejo desde la Fase 3, con sus candados (RESTRICTIVE en `ventas`,
  rol `capturista` existente, límite propio a `/api/leer-nota`).

### Decisiones que abre este plan

- **D19** Tres registros del mismo grupo (bloque `grupos` del config, tabla `grupos` del agente,
  `grupos_whatsapp` del Facturador). Propuesta: manda el Facturador (pantalla Conexiones); hasta la
  Fase 4 sin cambio.
- **D20** Para un cliente ya cortado, ¿el bot aplica con «sí» timbrado, pago y alta de producto
  (permiso de conexión nuevo, acotado por cliente) o solo prepara la propuesta y se aplica en
  pantalla? P9 hoy excluye todo CFDI nativo. Se decide antes del primer corte.
- **D21** Las 8 remisiones de Tabasco que divergen del Excel subido al SAE (pendiente 11):
  regenerar los masivos o corregir en el SAE a mano.

### Riesgos propios del agente

- 317 renglones sin commitear en las mismas fechas y archivos que las 14 órdenes perdidas.
- Refactor sin red sobre >27,000 líneas en tres archivos: nunca reescribir, solo redirigir detrás de
  `master.activo` y borrar al final; `npm test` como puerta.
- Precio equivocado antes de la paridad del espejo (ZMAFAN 168): la ficha no cambia de fuente hasta
  que las 7 listas vivas sean iguales.
- Si el Facturador cae tras el cambio de fuente, se callan las alertas: outbox y aviso al grupo
  interno cubren también las lecturas programadas.
- Apagar un perfil sin conciliación deja pérdidas silenciosas (pendiente 10): la conciliación
  sustituta corre antes de mover el interruptor.
- La visión de fotos sigue en el bot (D17) y sigue frágil (60 s por foto vs 25 s de SIGTERM): libro,
  reintentos y watchdog se conservan hasta el corte.
- La Mac Mini es un solo punto físico de falla: respaldo y runbook antes del primer corte.

## Decisiones del dueño

El dueño resolvió D9 a D17 el 2-sep-2026. Quedan abiertas D18 (OneDrive) y D19–D21, que abre el plan del agente.

| # | Decisión | Resolución o recomendación | Estado |
|---|---|---|---|
| D9 | `crear pedido SAE OC` inserta directo en FACTP02 (37/mes). ¿Se conserva o todo va por el masivo? | **Decidido**: el sistema solo genera el Excel masivo de pedido y el de factura; no se inyectan pedidos ni facturas directas en el SAE. El comando se retira en la Etapa 3. | DECIDIDO |
| D10 | ¿Manda el SAE en precios o el Facturador? | **Decidido**: manda el SAE y el Facturador lo sigue; el espejo escribe automático y avisa al grupo. Después del corte se invierte. | DECIDIDO |
| D11 | ¿Cómo se representa una reposición? | **Decidido**: tipo de partida REPOSICIÓN: se surte, descuenta inventario, no se factura. | DECIDIDO |
| D12 | Balles/Jubran no registran pagos en la CxC del SAE. | **Decidido**: capturar sus pagos en el Facturador desde ya; son los únicos clientes cuyo estado de cuenta no puede venir del espejo. | DECIDIDO |
| D13 | ¿Se necesita el archivo del cierre de periodo? | **Decidido**: no como función; Excel del periodo a Drive a petición. | DECIDIDO |
| D14 | ¿Horas de la alerta (7/10/13/16/18) y viernes 17? | **Decidido**: idénticas; solo cambia la fuente. | DECIDIDO |
| D15 | ¿Grupos apagados se encienden directo al Facturador? | **Decidido**: sí; ningún cliente nuevo conoce el Master. | DECIDIDO |
| D16 | Código del cliente en el cotizador (pendiente 9b). | **Decidido**: leer la equivalencia SAE («02:7»); no abrir el campo autogenerado. | DECIDIDO |
| D17 | ¿Fotos de EHMO se leen en el bot o en el Facturador? | **Decidido**: en el bot por ahora; revisar tras el corte. | DECIDIDO |
| D18 | La orden original vive en Drive (y en la carpeta OneDrive de la Mac); el Facturador solo guarda el link. ¿Se sigue así? | **Propuesta**: el Facturador guarda el archivo (P12); OneDrive/Drive de respaldo durante la transición, opcionales después. | ABIERTA |
| D19 | Tres registros del mismo grupo (config del bot, tabla `grupos` del agente, `grupos_whatsapp` del Facturador). | **Propuesta**: manda el Facturador (pantalla Conexiones); hasta la Fase 4 sin cambio. | ABIERTA |
| D20 | Cliente ya cortado: ¿el bot aplica con «sí» timbrado, pago y alta de producto, o solo prepara la propuesta? P9 excluye CFDI nativo. | **Propuesta**: permiso de conexión nuevo acotado por cliente cortado, siempre con vista previa y «sí». Antes del primer corte. | ABIERTA |
| D21 | 8 remisiones de Tabasco divergen del Excel subido al SAE (pendiente 11). | **Propuesta**: regenerar esos masivos desde el Facturador antes de la Fase 3. | ABIERTA |

## Riesgos

| Riesgo | Cobertura |
|---|---|
| Una orden se pierde al apagar el Master | Outbox + conciliación existen; interruptor solo con pendiente 10 cerrado y 5 días en cero; Master solo lectura 2 semanas |
| El cruce del Facturador difiere del bot sin que nadie lo note | Etapa 1 compara reportes 5 días; diferencias se corrigen en datos |
| Precio corregido en SAE y el Facturador cobra el viejo | Espejo puntual tras cada comando + pasada de 30 min; lo facturado no se reprecia |
| La clave del bot gana permisos amplios | Permisos acotados (P9); catálogo se escribe por el espejo, no por endpoints de gestión |
| El equipo sigue capturando en la hoja | Solo lectura desde el apagado; ayuda y manuales cambian ese día |
| El pedido del SAE deja de crearse al instante desde el chat (37/mes) | El mismo comando devuelve el masivo de pedido con folio real y avisa que se importa en Aspel; el rastro queda en la remisión |
| Se mezclan los dos tenants de prod | Clave y espejo apuntan al tenant que opera; el de pruebas no recibe nada |

## Lo que no cambia

Grupos y roles (el bot enruta por grupo y rol; los perfiles se declaran una vez en la Fase 0 —
Balles/Jubran → `balles`, EHMO Pachuca → `ehmo` — sin cambiar lo que cada grupo puede pedir);
sintaxis de comandos y confirmación sí/no; acuses, alarmas y PDFs; el SAE factura e importa los
masivos de pedido y de factura hasta el corte por cliente; los originales siguen llegando a Drive
durante la transición, con su link en cada orden (y, con D18 aprobada, el archivo en el
Facturador); alertas y horarios.
