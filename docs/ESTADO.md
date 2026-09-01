# Estado del proyecto — 01/09/2026 (cierre wrap-all: PRs #62–#71 + `ec59214` + `7a9c689`)

Lo reescribe `/endworking` al cerrar el día. Punto de entrada para retomar: basta abrir esta
carpeta y leer este archivo.

## Git

| | |
|---|---|
| Rama base | `main` — el commit de este archivo, sobre `7a9c689` (sugerir-sat-batch) — igual que `origin/main` salvo este commit de docs |
| Remoto | `frutaskelly/facturador` |
| Working tree | limpio |
| Worktrees | TODAS las sesiones cerradas por el wrap-all del 01-sep; solo queda `amazing-gould-c9fa17` (la sesión del cotizador, vacía — se poda sola al cerrarla). Ramas viejas en origin por borrar cuando se quiera: `claude/cotizador-fcad7e` y `claude/focused-roentgen-b61e17` (ambas ya contenidas en `main`); `claude/estado-cierre-31ago` se conserva a propósito como respaldo |
| PRs abiertos | ninguno |
| Migración head | `0058_proyecto_sucursales` en `main` **y aplicada a prod** |

`Cristian/smartsupply-v2.0` es un enlace simbólico a esta carpeta, no otro clon.

## Deploy

**En vivo en https://facturador.mx y al día con `7a9c689`**: el wrap-all del 01-sep corrió
`./deploy.sh` desde este checkout en `7a9c689` — reconstruyó las tres imágenes, sin
migraciones nuevas (head sigue `0058`), y los cinco contenedores quedaron arriba y sanos.
Deploys previos del 31-ago: `ec59214` (cotizador; endpoint nuevo verificado con 401) y
`1a0fd06` (PRs #67–#71, por la sesión que unificó este ESTADO).

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
5. **Extender `SERIES_POR_EMPRESA` del conector a las empresas 04 y 05** — diferido junto con
   el onboarding (misma decisión); hoy solo cubre 02 y 03.
6. ~~Resolver precios por lote al abrir una orden~~ — **RESUELTO** el 31-ago (PR #57):
   `resolver_precios_lote` deja el detalle en 3–9 consultas fijas para N partidas (~27 s → ~4 s)
   y el slidedown usa `?vistazo=true` que salta cruce y precios (~27 s → ~2 s). Un test de
   paridad corre ambos resolutores sobre una matriz de escenarios bajo RLS y exige el mismo
   resultado campo por campo.
7. ~~Cruce de proyecto sin respetar la sucursal~~ — **RESUELTO en código y desplegado** el
   31-ago (PRs #56 + #61, ver arriba). **Queda la configuración de DATOS en prod** (decisión
   del dueño: negociación por plaza), en este orden desde la UI ya desplegada:
   (a) crear proyecto **HOSPITALES VILLAHERMOSA** (dueño EHMO, alcance SUC-02 Tabasco) y
   restringir HOSPITALES e IMSS BIENESTAR a SUC-01 Pachuca en Catálogos → Proyectos;
   (b) reapuntar la equivalencia `villahermosa:HOSPITALES` corrigiendo una OC de VH desde la
   bandeja con «Guardar asignación»; (c) crear la asignación cliente EHMO + HOSPITALES
   VILLAHERMOSA → «Lista EHMO Villahermosa 08_2026» y **borrar la especificidad-11 provisional**
   (EHMO+Tabasco+HOSPITALES, id `da5e658b…`, creada el 31-ago como puente); (d) `POST
   /oc-recibidas/{id}/reabrir` en las PENDIENTE de Tabasco con `resuelto_via != 'MANUAL'`.
   Diferido a propósito: la defensa dentro del resolutor de precios (tras #61 el par inválido ya
   no se puede crear; la alternativa barata sería validar con el helper en `create_remision` y
   el cotizador). Limpieza aparte detectada: SUC-03/SUC-04 «HIDALGO EHMO» duplicadas en prod.
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
10. **La conciliación de 6 h no avisó de 14 OCs perdidas** (Balles/Jubran, 28–31 ago, la
    25306 incluida). Se detectaron el 31-ago por la noche corriendo `facturador_conciliar.py`
    a mano y se recuperaron TODAS con `facturador_backfill.py` + contexto regenerado fresco de
    la BD (14 pendientes, 0 duplicados). Falta la causa raíz: por qué el flujo en vivo no las
    empujó (llegaron por el grupo Interno SM) y por qué el job de 6 h no alertó — y OJO: el
    repo del bot tiene cambios sin commitear (`index.js`, `sheets_push.py`) de otra sesión en
    esas mismas fechas. Revisarlo desde la sesión que trabaja ese repo.
