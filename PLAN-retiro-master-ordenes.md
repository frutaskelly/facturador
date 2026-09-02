# PLAN — Retiro del Master Órdenes

> Propuesta del 1 de septiembre de 2026, sin código. Verificada contra el código del bot
> (`index.js`, `sheets_push.py`, `ehmo_pedidos.py`), el Facturador en `main e03912c`, el
> `PLAN-migracion-master-facturador.md` del 28-ago y 30 días de bitácora del router del bot
> (2 al 31 de agosto). Versión legible con tablas y diagrama: artifact «Retiro del Master Órdenes».

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
  estado de cuenta, cierre de periodo). El Facturador ya hace bien el primero (bandeja + remisiones,
  0 OCs perdidas en la conciliación cada 6 h); le faltan piezas del segundo y casi todo el tercero.
- **46 comandos vivos en dos motores** que no comparten código (`sheets_push.py` para Balles/Jubran,
  `ehmo_pedidos.py` para EHMO/MAFAN). 7,294 comandos en 30 días solo en la tubería de Balles.
- **El bot se vuelve un cliente delgado**: un comando = una llamada con vista previa y aplicar. El bot
  conserva la conversación; el Facturador conserva la verdad. Un solo cruce de productos: el del
  Facturador (los alias del bot ya se migraron el 28-ago).
- **El SAE sigue igual hasta el corte**: factura, importa masivos, recibe altas de producto con
  confirmación. El espejo de catálogo lleva al Facturador lo que nazca en el SAE, por comando del bot
  o por captura directa en Aspel.
- **El Master se apaga por perfil con un interruptor**: Balles+Jubran → EHMO Pachuca → Villahermosa.
  Queda de solo lectura dos semanas y se archiva.

| Etapa | Trabajo Facturador | Trabajo bot | Calendario |
|---|---|---|---|
| 0 · Congelar y contrato | 2–3 d | 1–2 d | semana 1 |
| 1 · Paridad de lectura | 6–8 d | 3–4 d | semanas 2–3, con el Master vivo |
| 2 · Paridad de escritura | 6–8 d | 4–5 d | semanas 3–5 |
| 3 · Catálogo y precios | 5–7 d | 2–3 d | semanas 5–6 |
| 4 · Apagado por perfil | 1 d | 1 d | 3 semanas de calendario, un perfil a la vez |
| 5 · Limpieza | 1 d | 3–4 d | al terminar el último perfil |

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
   sistema; el Facturador nunca escribe en la base del SAE.
5. **Las reglas de la casa se conservan**: folios del sistema sin ceros ni espacios; el masivo deja
   rastro y no estampa; escrituras cruzadas con confirmación; lo facturado no se toca.
6. **Quién hizo qué**: la «Nota WhatsApp» del Master se vuelve actor externo en la bitácora.

## Los comandos, uno por uno

Estado: EXISTE (ya lo hace el Facturador) · PARCIAL · FALTA · SE QUEDA (sigue en SAE/bot) · DECISIÓN.

### A · Entrada de órdenes
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| PDF de OC/requisición (Balles, Jubran) | 605 | Master+Summary, Drive, alarmas contra SAE; bandeja en 2º | La bandeja cruza y cotiza; el bot arma el acuse con esa respuesta | EXISTE |
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
| `agrega <producto> a la lista de <proyecto> en <$>` (EHMO) | ≈31 | Transacción SAE + relleno del Master | Igual + espejo puntual + re-evaluación de OCs pendientes | FALTA |
| `actualiza el precio a <$> <CLAVE>`, precios OC, categoría, ficha | 250 | Escriben SAE con «sí» | Igual + espejo puntual | FALTA |
| `¿precio de <CLAVE>?` / ficha | 1,296 | sqlcmd al SAE en cada pregunta | Catálogo y listas del Facturador espejeadas; sin túnel | PARCIAL |
| `dame la lista de precios` | ≈18 | Lista 3 / 5–9 del SAE | PDF/Excel de la lista (existen); falta permiso (P9) | PARCIAL |
| `busca SAT` | — | Catálogo en el bot | Catálogo SAT del Facturador, ya al alcance | EXISTE |
| `cotiza · CLAVE · Desc` / `actualizar rq` | — | Cotizador aparte; corrección escribe SAE | Cotizador del Facturador (replica el PDF); permiso (P9) + espejo puntual | PARCIAL |

### E · Pedidos y facturas en el SAE
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| `crea pedido/factura massivo` | 127·28·≈54 | .xls del bot; OC en columna FOLIO (pendiente 12) | Export del Facturador (existe, ya en el cliente del bot) + folio real leído del SAE (P7) | PARCIAL |
| `crear pedido SAE OC <folio>` | 37 | INSERT FACTP02 con «sí» | Conservar o unificar: D9 | DECISIÓN |
| `factura de la OC` / `¿timbrada?` / `pendientes de la OC` | 5·448 | FACTF02 + CFDI02 | Desde el espejo; falta lectura al alcance (P8) | PARCIAL |
| `factura ZHGO 301` (PDF), totales, ejemplo factura | 19 | SAE | PDF sigue del SAE; totales del espejo | SE QUEDA |
| `amarra OC con factura` | ≈ | JSON local | El espejo liga por OC y folio interno; manual = corregir su_pedido | EXISTE |
| `master facturas` / `conciliación` (viernes 17 h) | — | Master Facturas; Master↔SAE | Espejo 30 min + conciliación Facturador↔SAE (existen) + entregas sin facturar/dobles (P5) | PARCIAL |

### F · Consultas y reportes
| Comando | Uso | Hoy | Con el Facturador | Estado |
|---|---|---|---|---|
| `resumen de órdenes` (por semana; falta facturar antes de facturado) | 613·≈8 | Summary/Resumen; Excel en EHMO | Resumen del Facturador (P3): texto y Excel por día/semana/cliente/punto | FALTA |
| `sin precio` / `sin clave` + alerta 7/10/13/16/18 h | 219·≈24 | Master | Consulta agregada de la evaluación de bandeja (P5); horas iguales | PARCIAL |
| `revisión` / `detalles OC` | ≈2 | Master | Detalle (existe) + duplicados/gemelas (P5) | PARCIAL |
| `estado de cuenta` | — | Sheet aparte + SAE | Cobranza del Facturador (existe); pagos de Balles/Jubran: D12 | PARCIAL |
| `impuestos` / IVA / IEPS | — | IMPU02 | Sin cambio | SE QUEDA |
| pregunta libre | 613 | Claude + Master | Claude + resúmenes del Facturador | PARCIAL |
| `cierra el periodo con carpeta X` | — | Archiva pestañas | Desaparece; Excel del periodo a Drive a petición (D13) | DECISIÓN |

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
  Nunca desactiva ni borra; duplicados se desempatan por partidas facturadas. Espejo puntual por
  clave tras cada comando que escribe el SAE, y re-evaluación de órdenes pendientes.
- **P7 Masivos con el folio real del SAE**: el bot deja su .xls; usa el export del Facturador y le
  pasa el siguiente folio real del SAE (`_sae_sig_folio_pedido` existe y no se llama). Cierra el
  pendiente 12 y D1.
- **P8 Lectura del espejo para el bot**: factura de la OC, estado SAT, totales, pendientes. El PDF
  sigue saliendo del SAE.
- **P9 Permisos de conexión acotados**: leer espejo, leer listas y cotizar, editar partidas con
  propuesta, espejar catálogo, folio sugerido. Nunca `producto:gestionar` ni `cliente:gestionar`.
  Detalle: `factura:espejo` no está sembrado en el catálogo de permisos (solo en código).
- **P10 Bitácora de eventos para el bot** (cola que el bot consulta cada minuto): orden convertida
  en remisión desde la pantalla, partida sin cruzar, factura llegada por el espejo. Opcional para
  apagar el Master; necesario para que los grupos se enteren de lo hecho en pantalla.
- **P11 Datos previos**: pendiente 7 (equivalencia VH + reabrir 35 OCs), 8 (183 links Drive), 10
  (causa raíz de las 14 OCs perdidas), 9b (código del cliente en el cotizador). El interruptor solo
  se mueve con los cuatro en cero.

## Cambios en el bot

- **Un cliente único**: las dos primitivas que escriben la hoja se reemplazan por
  `facturador_client.py`. Parseo de PDF/fotos se queda; cruce, precios y totales se van.
- **Interruptor `master.activo` por perfil**. Los 6 grupos apagados nacen directo al Facturador.
- **Propuestas en vez de memoria**: los 5 estados `pending*` se reemplazan por el número de
  propuesta del Facturador; un reinicio no lo pierde.
- **Comparador ampliado** (`facturador_comparar.py` ya compara masivos): resumen, armado, sin
  precio y ficha se generan de los dos lados durante las etapas 1–3.
- **Timers cambian de fuente, no de hora**: alerta sin precio, conciliación de viernes y pregunta
  libre leen el Facturador; la conciliación Master↔bandeja se apaga perfil por perfil; el conector
  de facturas gana el espejo de catálogo.
- **Se borra al final**: `alias_aprendidos.json`, `PROYECTO_LISTA_SAE`, lista local de precios,
  `remisiones.json`, `facturas_oc.json`, cierre de periodo y todo el acceso a Google Sheets.

## Plan por etapas

0. **Congelar el Master y fijar el contrato** (semana 1). Nada nuevo entra por el Master; lo sin
   commitear en el bot (remisiones, une remisión, bodega en pedido por ubicación) se commitea o se
   guarda en rama y su versión definitiva se hace sobre el Facturador. Contrato de vista previa y
   aplicar, actor externo, idempotencia por mensaje, permisos P9. Cerrar P11.
   *Comprobación*: una propuesta creada desde el chat sobrevive a un reinicio y se aplica con «sí».
1. **Paridad de lectura** (semanas 2–3; P3, P4, P5, P8, ficha). Cada reporte se genera de los dos
   lados y el comparador exige igualdad. *Comprobación*: 5 días hábiles con los 5 reportes idénticos
   en los 3 perfiles.
2. **Paridad de escritura** (semanas 3–5; P1, P2). Los comandos escriben el Facturador primero y el
   Master como sombra. La entrada de órdenes deja de cruzar en el bot. *Comprobación*: conciliación
   Master↔bandeja en cero 5 días con el Master ya como copia.
3. **Catálogo y precios** (semanas 5–6; P6, P7). Espejo por empresa validado renglón por renglón
   (como la lista de Tabasco el 1-sep); espejo puntual tras comandos; masivos con folio real.
   *Comprobación*: 4 listas iguales SAE = Facturador; producto creado por el bot aparece en la
   bandeja antes del siguiente pedido; 2 masivos importados sin error.
4. **Apagar el Master por perfil** (3 semanas de calendario). Balles+Jubran → EHMO Pachuca →
   Villahermosa; hoja de solo lectura; conciliación Master↔bandeja apagada con cada perfil.
   *Comprobación*: una semana por perfil sin abrir la hoja para resolver nada.
5. **Limpieza**. Borrar código de Sheets y archivos locales; exportar cada Master a Drive como
   archivo muerto; actualizar manuales. El corte del SAE sigue su plan por cliente.

## Decisiones del dueño

| # | Decisión | Recomendación |
|---|---|---|
| D9 | `crear pedido SAE OC` inserta directo en FACTP02 (37/mes). ¿Se conserva o todo va por el masivo? | Conservar hasta el corte de ese cliente; el espejo lee pedidos y pone el folio en la remisión. Unificar después. |
| D10 | ¿Manda el SAE en precios o el Facturador? | Manda el SAE; el espejo escribe automático y avisa al grupo. Después del corte se invierte. |
| D11 | ¿Cómo se representa una reposición? | Tipo de partida REPOSICIÓN: se surte, descuenta inventario, no se factura. |
| D12 | Balles/Jubran no registran pagos en la CxC del SAE. | Capturar sus pagos en el Facturador desde ya. |
| D13 | ¿Se necesita el archivo del cierre de periodo? | No como función; Excel del periodo a Drive a petición. |
| D14 | ¿Horas de la alerta (7/10/13/16/18) y viernes 17? | Idénticas; solo cambia la fuente. |
| D15 | ¿Grupos apagados se encienden directo al Facturador? | Sí; ningún cliente nuevo conoce el Master. |
| D16 | Código del cliente en el cotizador (pendiente 9b). | Leer la equivalencia SAE («02:7»); no abrir el campo autogenerado. |
| D17 | ¿Fotos de EHMO se leen en el bot o en el Facturador? | En el bot por ahora; revisar tras el corte. |

## Riesgos

| Riesgo | Cobertura |
|---|---|
| Una orden se pierde al apagar el Master | Outbox + conciliación existen; interruptor solo con pendiente 10 cerrado y 5 días en cero; Master solo lectura 2 semanas |
| El cruce del Facturador difiere del bot sin que nadie lo note | Etapa 1 compara reportes 5 días; diferencias se corrigen en datos |
| Precio corregido en SAE y el Facturador cobra el viejo | Espejo puntual tras cada comando + pasada de 30 min; lo facturado no se reprecia |
| La clave del bot gana permisos amplios | Permisos acotados (P9); catálogo se escribe por el espejo, no por endpoints de gestión |
| El equipo sigue capturando en la hoja | Solo lectura desde el apagado; ayuda y manuales cambian ese día |
| Se mezclan los dos tenants de prod | Clave y espejo apuntan al tenant que opera; el de pruebas no recibe nada |

## Lo que no cambia

Grupos, perfiles y roles; sintaxis de comandos y confirmación sí/no; acuses, alarmas y PDFs; el SAE
factura e importa masivos hasta el corte por cliente; Drive guarda los originales; alertas y horarios.
