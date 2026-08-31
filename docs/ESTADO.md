# Estado del proyecto — 31/08/2026 (actualizado por la noche tras el deploy del PR #54)

Lo reescribe `/endworking` al cerrar el día. Punto de entrada para retomar: basta abrir esta
carpeta y leer este archivo.

## Git

| | |
|---|---|
| Rama base | `main` @ `a54762a` (PR #54) — igual que `origin/main` |
| Remoto | `frutaskelly/facturador` |
| Working tree | limpio |
| Worktrees | 3 (`bandeja` con trabajo en curso sin commitear; `new-session-7acb50` y `zealous-torvalds-918745` podables) |
| PRs abiertos | ninguno (esta actualización entra por el suyo) |
| Migración head | `0057_export_rastro_portal_rbac` (ni #53 ni #54 traen migraciones) |

`Cristian/smartsupply-v2.0` es un enlace simbólico a esta carpeta, no otro clon.

## Deploy

**En vivo en https://facturador.mx** y al día con `a54762a` (PR #54): se comprobó **dentro del
contenedor** que `backend/app/api/v1/oc_recibidas.py` trae el backfill de `archivo_url` que
introdujo el #54. Los cinco contenedores (`frontend`, `backend`, `landing`, `tunnel`, `redis`)
están arriba y sanos (deploy del 31-ago por la noche; facturador.mx y api.facturador.mx
responden 200), y los cuatro propios se construyen desde **este** checkout, no desde un worktree.

⚠️ **La marca de tiempo de la imagen no sirve para saber si el deploy está al día.** Una imagen
construida segundos antes de fusionar un PR "parece" atrasada sin estarlo, y una imagen vieja
cuyo contexto de build no cambió no está atrasada en absoluto. Lo único que decide es mirar el
contenido dentro de la imagen.

**La puerta de `deploy.sh` bloquea si el checkout no coincide con GitHub** — incluidos archivos
sin rastrear. Por eso este `docs/ESTADO.md` va commiteado: suelto, detiene cada deploy. Nunca
usar `FORCE=1` para saltarla: publica el trabajo a medias de otra sesión.

## Lo que entró el 31-ago por la noche (PRs #53 y #54)

**La ingesta completa el link del documento en OCs ya asignadas (#54).** Una OC que ya generó su
remisión no se tocaba en un reintento del bot — correcto para la captura, pero dejaba sin remedio
el puntero al documento original: **183 órdenes de la migración llegaron sin `archivo_url`** y el
botón «Ver la OC original» de `/remisiones` caía a la bandeja en vez de abrir el Drive (lo
reportó el dueño con la SN-33NER-JUE). Ahora el reenvío completa `archivo_url`/`archivo_nombre`
**solo si faltan**; con el link puesto no se pisa, y la captura sigue intacta. El código del
botón ya era correcto: abre el Drive cuando hay link y solo sin él cae a la bandeja filtrada.

**#53 (sesión de la bandeja):** la lista enseña el proyecto y el vistazo deja de repetir los
botones del renglón; el detalle quedó en el propio PR.

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
6. **Resolver precios por lote al abrir una orden** (nuevo, 31-ago, tras el PR #51). El detalle
   de una OC evalúa el precio de CADA partida contra la BD remota (~6 consultas por partida):
   una orden de 25 partidas tarda ~27 s en abrir. Ese costo ya existía para las órdenes donde
   todo cruza bien; el #51 lo extendió a las órdenes con problemas al dejar de cortar en el
   primer tropiezo. El arreglo es de fondo: `resolver_precio` por lote (una consulta de
   overrides y una por lista para todos los productos, en vez de la cascada por partida).
7. **183 OCs de la migración sin `archivo_url`** (nuevo, 31-ago, tras el PR #54). El #54 deja
   al sistema listo para absorber los links, pero alguien tiene que mandarlos: la cura de fondo
   es un script en `SmartSupply/bot` que reenvíe esas órdenes por la ingesta con su URL de
   Drive (con el #54 en prod, se completan sin tocar la captura). **La SN-33NER-JUE sigue sin
   link**: el dueño tiene el UPDATE puntual pendiente de correr (Claude no puede escribir a la
   BD de prod), o cae sola cuando corra el script del bot.
