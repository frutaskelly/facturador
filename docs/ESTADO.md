# Estado del proyecto — 31/08/2026 (actualizado tras el wrap-all nocturno: PRs #56, #59, #60, #61 y el #62 posterior)

Lo reescribe `/endworking` al cerrar el día. Punto de entrada para retomar: basta abrir esta
carpeta y leer este archivo.

## Git

| | |
|---|---|
| Rama base | `main` — el commit de este archivo, sobre `6c68639` (#62) — igual que `origin/main` |
| Remoto | `frutaskelly/facturador` |
| Working tree | limpio |
| Worktrees | 2 activos + 1 bloqueado: `listaprecios-a35db8` (embarcado por #61, se poda al cerrar su sesión), `bandeja-filtros` (sesión abierta durante el wrap — no tocar), y `new-session-7acb50` (rama `claude/estado-cierre-31ago`: conflicta con este ESTADO y su contenido ya está incorporado aquí — ver nota abajo). `subida-productos-102286` cerró con el #62 y ya se podó |
| PRs abiertos | ninguno |
| Migración head | `0058_proyecto_sucursales` en `main` **y aplicada a prod** |

`Cristian/smartsupply-v2.0` es un enlace simbólico a esta carpeta, no otro clon.

## Deploy

**En vivo en https://facturador.mx** y al día: `./deploy.sh` corrió el 31-ago en la noche desde
este checkout en `6c68639` (#62) — sin migraciones nuevas (head sigue `0058`), recreó
`backend`/`frontend`/`landing` y los cinco contenedores quedaron arriba y sanos. El deploy
anterior (mismo día, desde `31df063`) fue el que **aplicó la migración
`0057 → 0058_proyecto_sucursales` a Supabase prod**.

⚠️ **La marca de tiempo de la imagen no sirve para saber si el deploy está al día.** Una imagen
construida segundos antes de fusionar un PR "parece" atrasada sin estarlo, y una imagen vieja
cuyo contexto de build no cambió no está atrasada en absoluto. Lo único que decide es mirar el
contenido dentro de la imagen.

**La puerta de `deploy.sh` bloquea si el checkout no coincide con GitHub** — incluidos archivos
sin rastrear. Por eso este `docs/ESTADO.md` va commiteado: suelto, detiene cada deploy. Nunca
usar `FORCE=1` para saltarla: publica el trabajo a medias de otra sesión.

## Lo que entró después del wrap-all (PR #62)

**La importación de productos encuentra el encabezado aunque no esté en la fila 1.** La lista
real «PRECIOS RIO LIBRE CZ» del operador (título y filas vacías antes del encabezado, en la
fila 5) salía con columnas `Unnamed` y el título como si fuera un producto. Ahora
`importar_productos` busca la fila con ≥2 campos conocidos entre las primeras 15, los rótulos
propios mapean por prefijo («PRECIO KELLY» → precio) y MARCA entra como descripción adicional.
Probado con el archivo real: 160 productos, cero filas basura, determinista (sin gastar IA).

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
