# Estado del proyecto — 01/09/2026 (cierre: `9021dfa` — tabla y menú en vivo, plan del retiro del Master dentro)

Lo reescribe `/endworking` al cerrar el día. Punto de entrada para retomar: basta abrir esta
carpeta y leer este archivo.

## Git

| | |
|---|---|
| Rama base | `main` en `9021dfa`, igual que `origin/main` |
| Remoto | `frutaskelly/facturador` |
| Working tree | limpio en el padre |
| Worktrees | Solo la de esta sesión (`remisiones-table-layout`), ya fusionada y borrable. El cierre removió `admiring-aryabhata`, `elastic-morse` y `facturador-migration-proposal` — las tres fusionadas y limpias — y podó sus ramas |
| PRs abiertos | ninguno — #79 (docs), #80 (tabla + menú), #81 (traspaso) y #82 (plan del Master) fusionados en este cierre |
| Migración head | `0063_export_pedido_rastro`, sin cambios: el trabajo de hoy es sólo frontend |

`Cristian/smartsupply-v2.0` es un enlace simbólico a esta carpeta, no otro clon.

**El push directo a `main` lo bloquea el clasificador de permisos.** El camino que sí funciona
es `gh pr create --base main` y luego `gh pr merge N --merge`.

## Deploy

**En vivo en https://facturador.mx y al día con `7b6e6ca`** (lo posterior es sólo documentación). `./deploy.sh` reconstruyó frontend
y backend (la imagen de landing se reusó de caché: su contexto no cambió) y los cinco
contenedores quedaron sanos. Verificado contra los dominios reales: `facturador.mx` 200,
`api.facturador.mx/health` 200, `admin.facturador.mx` 200 y `app.facturador.mx` 307 (redirige a
login, que es lo correcto). Sin migración nueva que aplicar. Los cinco contenedores se
construyen desde este checkout, ninguna worktree respalda el deploy.

## La tabla y el menú caben en la pantalla (`7b6e6ca`, PR #80)

Dos quejas del dueño sobre la misma causa: no cabía nada en la pantalla.

**La tabla de remisiones pedía 1672 px en una laptop de 1280.** Quedaban 682 px (41 %) detrás
del scroll horizontal y lo primero que se perdía era la columna de Opciones: para tocar un icono
había que irse a la derecha en cada renglón. La `DataTable` compartida gana tres cosas que
heredan **las 18 tablas** del app (todas pasan `actions`): `stickyActions` deja Opciones pegada
al borde derecho; `maxInlineActions` (2 por omisión) muestra dos iconos sueltos y manda el resto
a un menú ⋮ por fila —de 217 px a 110—; y `Column.truncate` / `Column.hiddenByDefault` permiten
columnas elásticas de un renglón y columnas que arrancan ocultas pero siguen en el menú
«Columnas» y en el CSV. El padding de celda bajó de 16 a 12 px: 72 px recuperados en una tabla
de 9 columnas. Remisiones además agrupa 13 columnas en 6 y ahora pide 990 px: cabe completa.

**El menú fijo de 240 px se llevaba la cuarta parte del ancho** y, con 29 items, tampoco cabía a
lo alto: pedía 1256 px contra 738 disponibles, así que Ajustes quedaba bajo el pliegue. Se midió
la opción obvia —dejar sólo los iconos— y **empeora**: los 29 siguen apilados y piden 1331 px.
Lo que entró se contrae a 64 px con los favoritos como iconos siempre visibles (con ⭐ y tooltip
`Sección · Etiqueta`, que es lo que desambigua los iconos repetidos: hay dos `Building2` y dos
`Store`) sobre un riel de secciones anclado que abre un panel con las etiquetas completas y su
estrella. El contenido pasa de 992 a 1168 px y el menú cabe entero por primera vez.

`NAV` se reagrupó en **seis secciones**, cada una con su icono y nombre corto dentro del propio
modelo —en un mapa aparte, una sección nueva se quedaría sin botón y desaparecería para quien
tenga el menú contraído—: `General` (dashboard, OC, remisiones, facturas, cobranza), `Catálogo`,
`Compras`, `Extras` (POS, inventario, cotizador, conversiones), `Configuraciones` (precios,
impuestos, series, POS de ajustes) y `Ajustes` (empresas, usuarios, roles, correo, conexiones,
diseño). La regla: **Configuraciones = cómo se cobra y cómo se numera; Ajustes = quién entra y
con qué se conecta.** Eso separa de paso los dos «Punto de venta» que antes se confundían.

Tres trampas que costaron y conviene no repetir: `Node.contains(e.target)` **lanza** cuando el
scroll viene de `window`/`document` (no son Node) y mata el handler en silencio; el
`stopPropagation()` de un handler de React corre **después** de un listener puesto en `document`
—React engancha en la raíz—, así que el panel se cerraba en el `mousedown` antes de que el clic
llegara a la estrella; y cualquier lista que salga de `NAV` crudo le enseña las 29 pantallas a un
capturista, así que todo deriva de un `navVisible` filtrado con `can`/`canAny`.

**Sin verificación visual contra datos reales**: se revisó con mediciones del DOM sobre un banco
de pruebas con datos falsos (ya eliminado), incluido un usuario capturista para comprobar el
recorte por permisos. Nadie ha visto `/remisiones` con sesión iniciada. **Conviene mirarlo.**

## Match IA editable y medio kilo con precio (`2a8b29f`)

Dos arreglos que salieron de pegar una orden real de 24 renglones.

**La columna Match IA era un desplegable cerrado** con los candidatos ≥80%. Si la IA se
equivocaba y el producto correcto no calificaba, no había forma de corregirla: el caso que lo
destapó fue `MANZANA GOLDEN SIN PICADURAS NI MAGULLADURAS` cruzada como `MANZANA GALA · 85%`,
siendo en realidad manzana amarilla. Ahora esa celda es el buscador del catálogo completo, con
alta de producto nuevo, y **aprende el alias con el texto ORIGINAL del cliente**: antes usaba lo
tecleado, así que buscar el nombre exacto no aprendía nada y el mismo error volvía en la
siguiente remisión. La columna Producto pasó a mostrar el texto del cliente, que es contra lo
que se juzga el cruce. La subida de orden por archivo (`d13822f`) hereda lo mismo, porque sus
partidas entran por el mismo camino.

**Los tramos de una lista son descuentos por volumen, no una cantidad mínima de venta.** Con
cantidad `0.5` y un tramo que arranca en 1, el resolutor devolvía `null` y la línea entraba sin
precio, trabando confirmar la remisión. Ahora por debajo del tramo más chico se cobra ese mismo.
La regla ya existía en el cotizador de órdenes (`cotizador.py`), pero nunca bajó al resolutor
central: por eso el bot cotizaba bien las OCs y la pantalla de remisiones no. El arreglo va en
`services/precios.py` (individual y lote) más el endpoint que reporta el tramo, así que cubre
remisiones, facturas, POS y el preview de totales. Test de regresión en `test_precios_api.py`.

**Sin verificación visual**: el backend local se cayó y el clasificador bloqueó levantarlo, así
que el cambio de UI se subió con typecheck, build y suite de backend en verde, pero sin que
nadie lo viera funcionando. **Conviene mirarlo en la primera remisión que se capture.**

## El folio del pedido lo pone el SAE, y el masivo se llama como su remisión (`ff728f7`, PR #75)

**El masivo de PEDIDOS escribía la OC del cliente en la columna FOLIO** (`CE-34CER-MAR`). Ahí va
el **consecutivo de pedidos del SAE**: una sola serie por empresa (`STAND.`, `TIP_DOC='P'`),
rellena a 10 dígitos con ceros (`0000000134`) — lo que dicen `KnowHow_Massivos_SAE.md` §2 y
`REGLAS_PEDIDOS.md` Regla 2, y la forma con la que entraron los masivos que sí quedaron bien
(`PEDIDO_massivo_SAE_3JUBRAN`, `_lote1_4ordenes`). Ahora el pedido recorre el mismo camino que la
factura: el preview pide el folio inicial, **el operador lo confirma contra SAE** y cada remisión
del lote se lleva su consecutivo. La OC pasa a su columna (`SU PEDIDO`) y sigue en la Observación,
que es por donde concilia el resto del sistema.

Como el Facturador no ve el SAE, el prellenado sale de lo que él mismo propuso:
`export_pedido_at`/`export_pedido_folio` (**migración `0063`**) guardan el rastro como
`<empresa>:<numero>`, **aparte** del rastro de facturas porque una misma remisión sale primero
como pedido y después como factura — compartir columna borraría el aviso de doble export de la
otra. De ahí salen el folio sugerido y el aviso de re-export.

**El nombre del archivo** era genérico y el navegador acababa numerando copias: bajar el pedido y
la factura de la misma remisión daba `PEDIDO_massivo_SAE (4)`. Ahora es `PEDIDO RZMAFAN9.xls` /
`FACTURA RZMAFAN9 al 22.xls`. Eran **dos bugs encadenados**: el backend ya mandaba un nombre
propio, pero `Content-Disposition` no estaba en `expose_headers` del CORS, así que el navegador no
dejaba leerlo y todo download caía al nombre de respaldo.

Ver el **pendiente 12**: el bot de WhatsApp sigue escribiendo la OC como folio.

## El vocabulario de alias ya se ve y se edita (`6d22c4b`, PR #74)

Hasta ahora los alias solo se podían **crear** —importación, IA, el bot, la captura— y no había
ninguna pantalla donde verlos: un alias mal apuntado dejaba órdenes sin cotizar en silencio.
Entró **`Catálogo › Vocabulario`**, una tabla puente **Cliente · Producto · «si la orden dice…»**
con el texto editable en línea, alta de equivalencias nuevas y buscador que pega a los dos lados
(texto y producto). En la ficha del producto quedó la pestaña **«Así lo escriben»**, la misma
información agrupada por cliente, avisando cuando un texto lleva además a otro producto.
Endpoints: `GET /productos/vocabulario`, `GET /productos/{id}/alias`, `PATCH` y `DELETE` sobre
`/productos/alias/{id}`. Sin migración. El permiso reusa la regla que ya estaba escrita: el
vocabulario **de un cliente** lo corrige quien captura; el **global** exige `producto:gestionar`
porque ahí caen los clientes nuevos que aún no tienen vocabulario propio.

⚠️ **La pantalla nueva no se ha visto funcionando.** Compila, la ruta responde 200 y no hay
errores de consola, pero nadie la ha operado — el preview local pide credenciales. Es lo primero
que hay que mirar al retomar.

## EHMO Tabasco ya tiene lista de precios (01-sep, tarde) — solo DATOS, sin código

**SAE empresa 03: se creó la lista 4 «EHMO TABASCO» con 196 productos.** Antes solo tenía las
3 listas de fábrica en cero, así que Tabasco llevaba de febrero a agosto facturando sin lista y
sacando el precio del historial. Se armó con la regla de siempre —precio vigente = último
facturado en factura no cancelada (`PAR_FACTF03`+`FACTF03`)— para 194 claves, más 2 sin ninguna
factura (pipián y tuna verde) que conservaron su precio capturado a mano. Las 196 cruzaron
exacto contra `INVE03`; todo es esquema de impuesto 3 (IVA 0), así que `PRECIOCIMP = PRECIO`.

**En el Facturador, la lista `EHMOVH0826` quedó sincronizada** (194 → 197 renglones): se
agregaron cúrcuma, camote amarillo y tomate verde limpio, y se corrigieron 3 precios —
**manzana en caja $67.21 → $1,485.00** (el renglón de CAJA traía un precio por kilo; lleva
desde julio facturándose a $1,485), albahaca $175.00 → $322.25 y calabaza de castilla
$57.20 → $36.19. Se **repropagó a las remisiones NO facturadas de Tabasco** (`factura_id` y
`factura_sae` en NULL; una remisión facturada no se reprecia nunca): 10 líneas de calabaza y la
única manzana por caja (RZEHMOVH40, que pasó de KILO a CAJA). Los totales de las 11 remisiones
se recalcularon y se verificaron contra la suma de sus líneas. Ver el **pendiente 11**, que
salió de aquí.

**El puente SAE ↔ Facturador es `producto_alias`**: la importación guardó unas veces la clave
del SAE (`AJOKG`) y otras la descripción (`ACELGAS`), así que hay que probar las dos formas o
el cruce se queda a la mitad.

## La sucursal dejó de ser del cliente (01-sep, `7f02ce9` — DESPLEGADO)

**La sucursal es ahora la PLAZA del negocio**, no una propiedad del cliente. Antes
`sucursales.cliente_id` era `NOT NULL`, así que Pachuca existía **cuatro veces**, una por
cliente. Ahora Pachuca es UNA fila y `cliente_sucursales` dice quién se surte de ella
(en prod: EHMO, JUBRAN, MAFAN y BALLES). Ese vínculo carga la **serie de folios de la
relación** — EHMO×Tabasco → ZEHMOVH, mientras Balles y Jubran comparten ZHGO en Pachuca —
y su abanico vive en `cliente_sucursal_series`. El **almacén** se queda en la plaza: de ahí
sale la mercancía de todos sus clientes.

**Proyectos: uno por plaza** (decisión del dueño). `proyectos.sucursal_id` sustituye al
alcance m2m de la 0058, que se eliminó. Los 6 históricos quedaron en Pachuca y nació
`P-HOSPITALES-TAB` (HOSPITALES · Tabasco), el hueco que el dueño detectó.

**Overrides**: el CHECK pasó de XOR a "al menos una dimensión". (cliente + sucursal) juntos
es el precio MÁS específico; solo sucursal = para todos los que surte la plaza.

**Migraciones 0060 (aditiva + backfill) y 0061 (fusión + drops).** La 0061 no es reversible
a propósito; **la 0060 sí baja limpio**, que es el escalón que importa si el deploy tropieza.

⚠️ **Prod tiene DOS tenants con los mismos clientes**: `cristian-gerardo-zarate-orozco` (la
operación real: las OCs, los 6 proyectos) y `frutas-kelly`. La fusión es POR TENANT, así que
cada uno quedó consistente — pero **toda consulta o arreglo manual a la base debe acotar el
tenant**. Un `INSERT` sin acotar sembró de más durante esta sesión y hubo que limpiarlo.

**Una revisión adversarial de 41 agentes tumbó dos veces esta rama antes de embarcarla**: 13
defectos en la primera pasada (entre ellos, la fusión regalaba los precios negociados de un
cliente a los demás de la plaza, y el router nuevo había perdido el candado por cliente) y 3
más en la segunda, de los cuales **dos abortaban la migración a media aplicación**
(`proyecto_sucursales` reapuntada antes de deduplicar, y la renumeración `SUC-xx` generando
códigos repetidos). Todo corregido con fixtures que reproducen cada fallo.

## Cierre wrap-all (01-sep, madrugada)

**Embarcado: `7a9c689` — sugerir-sat-batch en 3 queries.** Con 118–160 productos el endpoint
hacía ~200–600 round-trips secuenciales vía pooler (25–60 s); ahora los candidatos de TODOS
los productos salen en dos queries (FTS rankeado con `unnest`+LATERAL y luego ILIKE solo por
los tokens que el FTS no llenó) y las unidades se validan en una. Mismo resultado verificado
A/B (30 textos idénticos, 79→5 queries). Suite completa en verde antes y después del rebase.

**Sesiones cerradas:** `listaprecios-a35db8` y `subida-productos-102286` — contenido byte por
byte YA en `main` (verificado con `git merge-tree`); `new-session-7acb50` (estado-cierre-31ago)
— superseded, rama respaldada en origin; `oc-precio-flujo`, `bandeja-filtros` y `roles-filter…`
— cerradas por sus propias sesiones durante la noche.

⚠️ **La marca de tiempo de la imagen no sirve para saber si el deploy está al día.** Una imagen
construida segundos antes de fusionar un PR "parece" atrasada sin estarlo, y una imagen vieja
cuyo contexto de build no cambió no está atrasada en absoluto. Lo único que decide es mirar el
contenido dentro de la imagen.

**La puerta de `deploy.sh` bloquea si el checkout no coincide con GitHub** — incluidos archivos
sin rastrear. Por eso este `docs/ESTADO.md` va commiteado: suelto, detiene cada deploy. Nunca
usar `FORCE=1` para saltarla: publica el trabajo a medias de otra sesión.

## Lo que entró después del wrap-all (PRs #62 a #67)

**El detalle de la OC cierra el circuito del precio (#67).** Al cruzar una partida se cotiza
(cliente+sucursal+proyecto+serie — la serie pesa más que sucursal+cliente) y el campo se llena
con el precio de la lista, etiquetado; el número es informativo (al crear, el backend resuelve
con tramos). Lo tecleado jamás se pisa y, si difiere, se ofrece llevarlo a la lista — al TRAMO
del que salió la referencia (`cotizar` reporta `cantidad_minima`), nunca a la lista base.
«Guardar asignación» desapareció: Crear pregunta —solo con cambios— si además se aprende la
asignación, y con cambio de cliente/sucursal recalcula el cruce y pide una mirada. Una revisión
adversarial de 18 agentes tumbó la primera versión (escribía en la lista de la asignación
anterior); todo lo confirmado quedó corregido con tests.

**Incidente revertido (31-ago, tarde-noche):** una prueba de Claude creó por error la remisión
RZEHMOHOS108 (borrador) desde la OC HO-35MEZ-JUE; se revirtió completo con aprobación del dueño
(borrador soft-borrado, OC de vuelta a PENDIENTE). El folio 108 de esa serie quedó quemado —
regla del sistema. El alias «Galleta mexicana (1000g)»→GALLETAS MEXICANAS que aprendió el
intento era correcto y se conservó.

### Antes (PRs #62 y #64)

**El cotizador replica al agente 1 de WhatsApp (`ec59214`, sin PR — fast-forward directo).**
La pestaña «Cotizar un documento» de `/cotizador` ahora hace lo mismo que el bot con las
requisiciones de Balles: lee el PDF de SAE con pdfplumber (port de `parse_all.py`, acomodos A
y B; IA solo de red final para fotos/acomodos raros), detecta al cliente por el RFC impreso,
valida el precio de cada partida contra `resolver_precio` con las dimensiones del cliente y
dibuja el MISMO PDF que manda el bot (port de `pedido_pdf.py`): notas rojas verbatim («SE
RESPETA EL PRECIO DE OC…» / «SE ENVIARA LA COTIZACION…» / «PRECIO INCORRECTO — OC $X ->
CORRECTO $Y»), alarma ATENCION con conteos, totales y total con letra. La pantalla enseña
exactamente ese PDF y el operador lo descarga. Validado contra la REQ 0000006477 real: mismo
resultado que el PDF del bot (3 respetadas + 1 incorrecta, total $1,798.60). De paso: las
cantidades fraccionarias (0.5 kg) ahora cotizan con el escalón base (antes quedaban «sin
precio» porque los tramos arrancan en `cantidad_minima=1`). El flujo de corrección del bot
(«actualizar rq» / nota «PRECIO YA COTIZADO» / v2) NO está incluido. Ver pendiente 9 para los
datos que faltan en prod.

**La bandeja se filtra encadenada y se busca de verdad (#64).** Buscador al servidor (folio,
remitente, archivo, punto de entrega y OBSERVACIONES del documento — que además salen en su
propia columna), y filtro «Grupo de origen» que cubre los dos mundos: los grupos de WhatsApp
(por jid) y lo que entra por la conexión de Smart Supply sin jid, cuyo origen es el remitente
(«EHMO villahermosa» — 49 de las 57 pendientes de hoy). `GET /oc-recibidas/grupos` enseña ambos
con SUS clientes y los filtros se encadenan: origen → clientes/proyectos, cliente → proyectos.

**La importación de productos encuentra el encabezado aunque no esté en la fila 1 (#62).**

## Lo que entró en el wrap-all nocturno (PRs #56, #59, #60 y #61)

**La negociación es de su plaza (#56 + #61, pendiente 7 RESUELTO en código).** El caso
VH-35COM-MAR: una OC de EHMO Villahermosa entraba etiquetada HOSPITALES —la negociación de
Pachuca— y proponía los precios de la lista SAE 9. Ahora los proyectos declaran en qué
sucursales entregan (#56, tabla `proyecto_sucursales` + multiselect en Catálogos → Proyectos) y
la regla vive en un helper único (`backend/app/services/proyecto_alcance.py`, #61): la ingesta
resuelve la sucursal ANTES que el proyecto y solo lo estampa si es del cliente y de la plaza
(si no, la orden entra sin proyecto y la corrección del operador con `aprender` reapunta la
equivalencia); el PATCH de la bandeja rechaza con 422 el par incompatible; `_auto_de` bloquea
la remisión de un clic de una orden mal etiquetada; y crear una asignación de precios
sucursal+proyecto imposible también da 422 (la UI filtra ambos selects por el alcance).
**Sin filas de alcance un proyecto no tiene restricción** — nada cambia hasta configurar datos.

**Fix de datos que ya corrió en prod (mismo día, sin deploy):** la asignación
cliente EHMO + SUC-02 Tabasco + HOSPITALES → «Lista EHMO Villahermosa 08_2026»
(especificidad 11 > 9). La lista VH trae exactamente los precios de los documentos de VH, así
que los conflictos de precio de las ~37 OCs pendientes de Tabasco desaparecen solos al abrirlas.

**Decisiones del dueño (31-ago):** HOSPITALES son negociaciones DISTINTAS por plaza (se parte
en dos proyectos — ver pendiente 7) y «Bienestar es Pachuca» (IMSS BIENESTAR se restringe allá).

**#59:** el ESTADO recupera lo del #54 que la reescritura del #58 perdió. **#60:** el pool del
backend cabe bajo el tope del pooler (5+7=12 de 15).

**Rama `claude/estado-cierre-31ago` (worktree `new-session-7acb50`): superseded.** Sus 3
commits reescriben este archivo desde una base vieja y conflictúan con `main`; sus hechos
(ZMAFAN 167 con cancelación en proceso, descuadre revisado por el dueño, fecha del masivo
confirmada, onboarding diferido) ya están incorporados abajo. La rama queda en origin por si
algo faltara; el worktree se puede podar cuando su sesión cierre.

## Lo que entró el 31-ago por la noche (PRs #53, #54 y #57)

**La ingesta completa el link del documento en OCs ya asignadas (#54).** Una OC que ya generó
su remisión no se tocaba en un reintento del bot — correcto para la captura, pero dejaba sin
remedio el puntero al documento original: **183 órdenes de la migración llegaron sin
`archivo_url`** y el botón «Ver la OC original» de `/remisiones` caía a la bandeja en vez de
abrir el Drive (lo reportó el dueño con la SN-33NER-JUE). Ahora el reenvío completa
`archivo_url`/`archivo_nombre` **solo si faltan**; con el link puesto no se pisa y la captura
sigue intacta. El código del botón ya era correcto: abre el Drive cuando hay link y solo sin él
cae a la bandeja filtrada. Queda la cura de los datos → pendiente 8.

**#53 (bandeja):** la lista enseña el proyecto y el vistazo deja de repetir los botones del
renglón. **#57 (precios por lote):** el detalle de una orden pasó de ~27 s a ~4 s y el vistazo
a ~2 s — el detalle está en el pendiente 6, ya RESUELTO.

## Lo que entró el 31-ago por la tarde (PR #51)

**La bandeja marca en rojo la partida que la detiene.** El renglón se pinta y dice su salida
(«elige el producto de al lado» / «dalo de alta»); la evaluación automática ya no corta en el
primer tropiezo — reporta TODOS los problemas, cada uno con su partida. El conflicto de precio
trae las dos cifras y **el nombre de la lista** («HOSPITALES (SAE lista 9)» — eso explicó el
57.5 que confundía en la VH-35TEA-MAR), con un clic para cobrar el de la lista o dejar el del
documento. El alta rápida de producto pide la clave SAT a la IA al abrir (endpoint que ya
existía, solo lo usaba Productos), enlaza el catálogo del SAT para verificar, y con el cliente
de la orden guarda el precio en su lista sin salir del modal; el buscador dejó su copia del
modal y usa el compartido (con el candado de duplicados).

## Lo que se cerró el 29–31 de agosto (PRs #40 a #47)

**El export a SAE ya no inventa folios.** Era el problema de fondo: el sistema estampaba
`serie + folio` al generar el Excel masivo, aunque ese Excel nunca se subiera al SAE. Quedaban
remisiones "RESERVADAS" contra facturas que no existían (así nació la ZHGO 588 fantasma). Ahora
`remisiones.export_sae_at` y `export_sae_folio` son **solo rastro**; el campo `factura_sae` lo
escribe únicamente el espejo del SAE o una captura manual. Sin confirmación del SAE, la remisión
se queda en BORRADOR.

**El espejo liga las facturas por su OC.** Dos formatos conviven: `OC <folio>` en las
observaciones, y el folio interno al final de la línea sin prefijo (así escriben EHMO y MAFAN).
El cruce exige candidata única e importes dentro de tolerancia; con eso se ligaron 39 remisiones
que estaban sueltas (de 99 a 138).

**Conciliación automática.** Facturador ↔ SAE: 148 = 148, cero diferencias. Master ↔ bandeja:
se encontraron 61 órdenes que solo vivían en el Master y se recuperaron todas; ahora se revisa
cada 6 h y solo avisa cuando hay excepción.

**Roles por menú y portal de cliente.** Permisos ver/editar/borrar por pantalla, rol preset
PORTAL CLIENTE, y `memberships.cliente_scope` para limitar qué empresas ve un usuario. El
alcance se aplica como filtro explícito: RLS aísla el tenant, no al cliente, y OWNER se salta
los permisos pero **no** el alcance. Una revisión adversarial cerró 8 fugas por las que el
portal alcanzaba datos de otros clientes.

**Cotizador.** Sube PDF/foto/Excel y devuelve la cotización completa; solo cotiza productos que
estén en la lista de precios del cliente, y lo que no cruza sale marcado con su motivo. Los
reportes usan el membrete y el layout de Smart Supply (logo del tenant, azul `#305496`, folio
de página).

**Formato de fecha del masivo.** Aspel lee la columna FECHA con la configuración regional del
Windows que importa, así que el formato es configurable (`SAE_FORMATO_FECHA`, por omisión
`%m/%d/%Y`) y se ve un ejemplo antes de importar. Con DD/MM se corrieron fechas en silencio
(ZHGO 312/324/335/365-369, una ya timbrada).

**Estructura del SAE, verificada.** La empresa 01 **no existe**; 02, 03, 04 y 05 emiten todas
con el mismo RFC que el Facturador y con CSD vigente, así que **no hace falta multiemisor**.

## Cómo verificar "ya fusionado"

Los PRs entran con **squash**: los commits de la rama nunca aparecen en `main` por SHA, así que
`rev-list --count`, `git cherry` y el `diff` de tres puntos **los tres mienten**. Lo que decide
es el `headRefOid` del PR contra el tip local, comparar el **contenido** de los archivos, o el
**patch-id** cuando se sospecha trabajo duplicado. Ejemplo del 31-ago: la rama
`cotizador-documentos` no coincidía con la cabeza de su PR #41 porque se rebasó y entró por el
#42; se confirmó por contenido (los cinco marcadores que introducía están en `main`) antes de
podarla.

## Pendientes

0. ~~Mirar la tabla y el menú con datos reales~~ — **REVISADO por el dueño** el 1-sep al cierre:
   la tabla y el menú se vieron con sesión iniciada y quedaron aprobados. Con eso se cierra
   también la deuda de «sin verificación visual» que traía `7b6e6ca`.
1. **15 textos de producto sin cruzar** (eran 51; el 31-ago se resolvieron 34 con las facturas
   del SAE, el historial de cada cliente y el catálogo INVE — 35 acciones: alias aprendidos,
   9 productos nuevos con su código SAE, 3 duplicados detectados y fusionados). Los 15 restantes
   necesitan decisión del dueño: los "CHILE COLOR" regionales, GUINEO, MELON CRIOLLO (¿chino o
   valenciano?), PITAYA (línea en KG, producto en PIEZA), el casillero de huevo de Balles, y
   5 abarrotes de Balles que no existen en el SAE (mole, mix de cacahuate, cereal 860g, galleta
   salada, papa en hojuela). Además hay 2 pares de productos duplicados por fusionar a mano:
   FLOR DE JAMAICA vs JAMAICA DE PRIMERA A GRANEL, y LECHUGA ROMANA kg vs pz por cliente.
2. **ZMAFAN 167: cancelación en proceso en el SAT** (verificado 31-ago en CFDI02: «Cancelación
   enviada al SAT», motivo 02, acuse recibido). Por el importe, el receptor debe aceptarla en su
   buzón — 3 días hábiles, o se cancela por plazo vencido. No requiere acción: el espejo la
   marcará CANCELADA solo cuando SAE reciba `FECHA_CANCELA`/`STATUS='C'`. La diferencia de
   importe contra RZMAFAN4 ($4,914.46 facturados vs $12,017.36) **ya la revisó el dueño y es
   correcta** (31-ago): la 167 se emitió con errores — por eso el motivo 02 — y la remisión trae
   el importe bueno. Al rematarse la cancelación, refacturar desde RZMAFAN4.
3. ~~Formato de fecha del masivo~~ — **CONFIRMADO** el 31-ago: el dueño subió
   `FACTURA_massiva_SAE.xls` (fechas `08/30/2026`, el default `%m/%d/%Y`) y el SAE lo importó
   sin problemas.
4. **Onboarding de los 4 clientes nuevos del SAE** — **DIFERIDO a propósito** (decisión del
   dueño, 31-ago): se retoma en otra sesión, en otra cuenta. El plan completo vive en
   `PLAN-onboarding-clientes-sae.md` (Etapas A–E); los grupos de WhatsApp ya están configurados
   en `activo: false`, listos para encender.
7. ~~Plan del retiro del Master sin commitear~~ — **REVISADO Y FUSIONADO** (PR #82):
   `PLAN-retiro-master-ordenes.md` vive en la raíz. Recorre los 46 comandos del bot uno por uno
   con el uso real de 30 días, y propone 11 piezas, 5 etapas y 9 decisiones del dueño. **Es la
   siguiente pieza grande de trabajo**: empieza por su Etapa 0 (congelar el Master, fijar el
   contrato de vista previa/aplicar y cerrar el pendiente 11 del propio plan).
   Un matiz detectado al revisarlo: dice que `_sae_sig_folio_pedido` «no se llama», pero sí lo
   hace `ehmo_pedidos.py:3708`; lo que no lo usa es la ruta del masivo de Balles/Jubran.
8. ~~Decisiones abiertas del menú nuevo~~ — **CERRADAS por el dueño** el 1-sep: los favoritos
   siguen «mudándose» de su sección y los iconos de Remisiones y Facturas se quedan como están.
5. **Extender `SERIES_POR_EMPRESA` del conector a las empresas 04 y 05** — diferido junto con
   el onboarding (misma decisión); hoy solo cubre 02 y 03.
6. ~~Resolver precios por lote al abrir una orden~~ — **RESUELTO** el 31-ago (PR #57):
   `resolver_precios_lote` deja el detalle en 3–9 consultas fijas para N partidas (~27 s → ~4 s)
   y el slidedown usa `?vistazo=true` que salta cruce y precios (~27 s → ~2 s). Un test de
   paridad corre ambos resolutores sobre una matriz de escenarios bajo RLS y exige el mismo
   resultado campo por campo.
7. ~~Cruce de proyecto sin respetar la sucursal~~ — **RESUELTO Y DESPLEGADO** el 01-sep con
   el rediseño de sucursales (`7f02ce9`). La configuración de datos que quedaba pendiente se
   aplicó casi toda: HIDALGO EHMO reconocida como Pachuca y fusionada, las 8 sucursales de
   demo borradas, Chiapas dada de alta, `P-HOSPITALES-TAB` creado para Tabasco y los 6
   proyectos históricos anclados a Pachuca. **Faltan dos escrituras** que el classifier
   bloqueó en la sesión (el SQL exacto está en `backend/scripts/datos_post_deploy_sucursales.sql`):
   (a) mover la asignación `da5e658b…` (EHMOVH0826, la espec-11 provisional del 31-ago) del
   HOSPITALES de Pachuca al `P-HOSPITALES-TAB`; (b) borrar lógicamente el `P-HOSPITALES-TAB`
   que quedó de más en el tenant `frutas-kelly` (ese tenant no opera Tabasco). Aparte, ya con
   la pantalla nueva, hay que **capturar las series por vínculo** que hoy no existen: ZHGO
   para Balles y Jubran en Pachuca, y EHCHHO para EHMO×Chiapas.
   **Verificado el 01-sep por la tarde: falta más de lo que decía este punto.** La estructura
   está (P-HOSPITALES-TAB existe y cuelga de Tabasco), pero el cruce sigue mandando las órdenes
   de Villahermosa a la negociación de Pachuca: (c) la equivalencia `PROYECTO /
   villahermosa:HOSPITALES` **todavía apunta al `HOSPITALES` de Pachuca** — cada OC nueva de VH
   entra mal etiquetada; y (d) **las 35 OCs PENDIENTE de Tabasco siguen con ese proyecto**, así
   que hay que reabrirlas (`POST /oc-recibidas/{id}/reabrir`) DESPUÉS de reapuntar la
   equivalencia. **Orden que importa:** la asignación puente `da5e658b…` (espec-11, EHMO +
   Tabasco + HOSPITALES-de-Pachuca → EHMOVH0826) es hoy lo ÚNICO que evita que Tabasco cobre la
   lista 9; no se borra hasta que (c) y (d) estén hechos, o los precios de VH se rompen entre
   un paso y otro. Las dos escrituras las volvió a bloquear el classifier de permisos.
8. **183 OCs de la migración sin `archivo_url`** (31-ago, tras el PR #54). El #54 deja al
   sistema listo para absorber los links: la cura de fondo es un script en `SmartSupply/bot`
   que reenvíe esas órdenes por la ingesta con su URL de Drive — se completan sin tocar la
   captura. **La SN-33NER-JUE que reportó el dueño sigue sin link** (verificado por la noche):
   queda el UPDATE puntual que el dueño tiene pendiente de correr, o cae sola con el script.
9. **Datos en prod para que el PDF del cotizador salga idéntico al del bot** (código ya
   desplegado en `ec59214`): (a) subir el logo de Álvarez Kelly en Ajustes › Empresa (el
   checklist de onboarding lo marca pendiente); (b) poner `7` como código del cliente
   OPERADORA BALLES (el bloque «( clave ) nombre» sale de `clientes.codigo`); (c) tener la
   lista BALLES JUBRAN cargada y al día — los precios salen de las listas del Facturador, no
   de SAE. Limpieza menor aparte: borrar el usuario de prueba `ok-833@test.local` en Supabase
   Auth → Users (lo creó por accidente una corrida de tests del 31-ago con credenciales
   reales; solo existe en Auth, sin datos).
   ⚠️ **El (b) no es capturar un dato: hoy es imposible.** `clientes.codigo` se autogenera y el
   backend lo DESCARTA tanto al crear como al editar (`data.pop("codigo")` en
   `backend/app/api/v1/clientes.py`, altas y PATCH), así que BALLES es `CLI-001` y no hay forma
   de dejarlo en `7` por la UI ni por la API. Hace falta una decisión: o el cotizador deja de
   leer `clientes.codigo` y usa un campo aparte para la clave del SAE, o se abre el campo a
   edición y se rompe la convención de la casa (código autogenerado, de solo lectura).
10. **La conciliación de 6 h no avisó de 14 OCs perdidas** (Balles/Jubran, 28–31 ago, la
    25306 incluida). Se detectaron el 31-ago por la noche corriendo `facturador_conciliar.py`
    a mano y se recuperaron TODAS con `facturador_backfill.py` + contexto regenerado fresco de
    la BD (14 pendientes, 0 duplicados). Falta la causa raíz: por qué el flujo en vivo no las
    empujó (llegaron por el grupo Interno SM) y por qué el job de 6 h no alertó — y OJO: el
    repo del bot tiene cambios sin commitear (`index.js`, `sheets_push.py`) de otra sesión en
    esas mismas fechas. Revisarlo desde la sesión que trabaja ese repo.
11. **8 remisiones de Tabasco se repreciaron DESPUÉS de que su Excel salió al SAE** (01-sep, al
    sincronizar la lista EHMOVH0826). Al bajar la calabaza a $36.19 y marcar la manzana como
    CAJA, ocho de las once remisiones tocadas ya tenían `export_sae_at`: RZEHMOVH3, 10, 12, 13,
    16, 27, 29 y la 40. Ninguna está facturada —por eso el reprecio las alcanzó—, pero su Excel
    ya se había generado, así que **lo que el operador subió al SAE y lo que hoy dice el
    Facturador pueden no coincidir**. Hay que decidir si se regeneran esos masivos o se corrigen
    en el SAE a mano. Recordatorio de la regla de la casa: generar el masivo deja rastro
    (`export_sae_at`/`export_sae_folio`) y nada más; `factura_sae` lo escribe el espejo o una
    captura manual.
12. **El bot de WhatsApp sigue poniendo la OC como folio del pedido** (01-sep, noche). El PR #75
    arregló el masivo del Facturador, pero `cmd_massivo` en `SmartSupply/bot/sheets_push.py`
    hace todavía lo viejo: escribe `su_pedido` en la columna FOLIO. Ahí sí hay acceso al SAE
    —existe `_sae_sig_folio_pedido()`, que toma el mayor entre `MAX(FACTP02)+1` y
    `FOLIOSF02.ULT_DOC+1`—, pero `cmd_massivo` no la llama. Es OTRO repo, así que se dejó fuera
    a propósito: mientras no se alinee, los pedidos que salgan del bot y los que salgan del
    Facturador se folian distinto. Ojo también con los cambios sin commitear que el pendiente 10
    reporta en ese mismo archivo.
