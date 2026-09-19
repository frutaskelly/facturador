# Briefing: auditoría de Smart Supply y el Facturador

Este documento es un traspaso. Lo escribe una sesión de Claude que lleva semanas trabajando estos
sistemas, para otra sesión que no los conoce. Al final del documento vas a poder auditarlos sin
haber estado en ninguna conversación previa.

**Regla de evidencia, antes de nada:** toda afirmación que hagas va con su `archivo:línea`, o
con la etiqueta NO VERIFICABLE DESDE EL CÓDIGO. Sin excepción. Si tienes ultracode y la
herramienta Workflow, úsalos: el encargo trae ocho flujos pensados para correrse con varias
lentes en paralelo. Pero la regla de evidencia manda sobre la velocidad.

Hay una propuesta sobre la mesa: **retirar el «Master Órdenes», una hoja de Google Sheets que hoy
es el corazón operativo del negocio**. La propuesta está escrita, revisada tres veces y parcialmente
ejecutada. Tu encargo no es ejecutarla. Es auditarla.

---

## Empieza por aquí: el hallazgo que puede tumbar la propuesta

Al preparar este documento salió algo que conviene que sepas antes de leer nada más, porque cambia
cómo hay que juzgar la propuesta entera.

**La red de seguridad que justifica el apagado se apaga con él.**

El plan cubre su riesgo más grave, «una orden se pierde al apagar el Master», con la frase «el
outbox y la conciliación ya existen». Pero la conciliación **lee el Master**:
`facturador_conciliar.py:12` define una orden perdida como el folio que está en la hoja y no tiene
orden en el Facturador, y `folios_master_ehmo()` y `folios_master_balles()` abren la hoja para
saberlo (líneas 47, 76 y 133). Sin Master no hay contra qué comparar. El detector de órdenes
perdidas deja de funcionar el mismo día que se apaga aquello que debía vigilar.

Lo mismo pasa con dos protecciones más, y estas cubren a los dos canales de entrada, WhatsApp y
correo: la detección de una orden **duplicada** y la de una **versión vieja** de una orden también
leen la hoja. Del lado del Facturador solo queda el ancla de idempotencia, que es más estrecha.

Esto no decide el dictamen por ti. Lo que hace es mover la pregunta central: **ya no es si se puede
escribir directo al Facturador**, porque eso ya se hace todos los días. Es **qué protecciones mueren
con la hoja, cuáles tienen sustituto construido, y qué es irreversible**. Trátalo como hipótesis a
verificar, no como conclusión: confírmalo tú con archivo y línea antes de apoyarte en él. Está
desarrollado en la pregunta P4 del encargo, y el plan ya lleva el aviso en su tabla de riesgos.

---

## Tu encargo

### El estado objetivo, en palabras del dueño (19 de septiembre de 2026)

> «Todo debe funcionar de la misma manera. Únicamente se quita el Master y la sincronización entre
> el Master y el Facturador, y todo se escribe directamente en el Facturador en tiempo real desde
> WhatsApp. WhatsApp siempre llama al Facturador.»

Esto es **más simple que lo que dice hoy el plan**. El plan contempla etapas con escritura al Master
primero y un periodo de sombra en el que los dos conviven. El dueño quiere el Master y su
sincronización fuera, y la escritura directa. Esa diferencia es una de las cosas que debes evaluar:
¿el camino por etapas del plan sigue teniendo sentido, o el objetivo del dueño pide otro diseño?

### Qué tienes que dictaminar

No te toca ejecutar el retiro del Master. Te toca dictaminar si retirarlo es la mejor próxima acción, y con qué forma exacta.

El dueño ya dijo lo que quiere: que todo funcione igual, que se quiten el Master y la sincronización entre el Master y el Facturador, que todo se escriba directo en el Facturador en tiempo real desde WhatsApp, y que WhatsApp siempre llame al Facturador. Esa es la opción A del tablero de abajo. No es la única opción y no se da por aprobada.

Tu dictamen tiene que separar dos cosas distintas, porque pueden no coincidir:

1. Cuál es el mejor destino a un año.
2. Cuál es la mejor próxima acción esta semana.

El veredicto se escribe en una frase y con una de estas tres formas: RETIRAR, RETIRAR CON CONDICIONES, o NO RETIRAR TODAVÍA. Si te apartas de lo que pidió el dueño, dilo en la primera línea, señalando qué parte de su estado objetivo no se cumple y a cambio de qué. No cuela un plan por etapas presentándolo como si fuera lo que él pidió.

**El tablero de opciones.** No contestes sí o no a la única propuesta sobre la mesa. Evalúa estas seis con los mismos criterios, y ninguna se descarta antes del censo:

- **A. Retiro directo.** Lo que pide el dueño. Fuera la hoja y su sincronización, escritura directa ya, sin periodo de sombra.
- **B. Retiro por etapas.** El PLAN vigente, con el Master vivo, periodo de sombra y comparador que exige igualdad.
- **C. Master congelado.** La hoja pasa a solo lectura, nadie captura en ella, y queda de conciliador ciego alimentado desde el Facturador durante un trimestre.
- **D. No retirarlo.** Se arregla la sincronización: invertir el orden de escritura, dedup en el Facturador, vigía que propone en vez de aplicar, candado ampliado. La hoja se queda.
- **E. Reemplazarlo.** Fuera Google Sheets, pero su función la toma otra pieza: cola de eventos más reportes de Excel del Facturador.
- **F. Otra cosa primero.** Cerrar antes la Fase 0 del bot, el agente de correo bajo git, el contrato de propuesta, o el aplicador de catálogo y precios. El Master se retira después.

**Lo que hace que este encargo sea delicado.** El Master no es un almacén. Es también la red: el dedup que corta antes de llegar al Facturador, la conciliación que repara órdenes perdidas, y el respaldo del intake cuando el Facturador no responde. La pregunta central no es si se puede escribir directo al Facturador, porque ya se escribe. Es qué protecciones mueren con la hoja, cuáles tienen sustituto con archivo:línea, y qué es irreversible.

---

### Las preguntas

Contesta todas. Cada respuesta va con archivo:línea o con la etiqueta explícita NO VERIFICABLE DESDE EL CÓDIGO. No hay tercera categoría.

#### A. Las opciones y la superficie

**P1. ¿Qué hace exactamente cada una de las seis opciones?**
Escribe la ficha de diez líneas de cada una ANTES de leer más código: qué se apaga, qué se construye, qué queda igual, cuál es el primer paso, cuál su reversa. Base: `/Users/michelzarate/Documents/Claude/Facturador/PLAN-retiro-master-ordenes.md`, secciones «Cómo retomar esto», «Revisión del 19 de septiembre», «Plan por etapas» y «Piezas nuevas del Facturador» P1 a P12. Sin este paso, el dictamen degenera en «sí, con cuidado».

**P2. ¿Cuál es el censo exacto de escrituras y lecturas a la hoja, y cuántas no tienen equivalente en el Facturador?**
El PLAN habla de 86 llamadas a la hoja y de 46 comandos en dos motores. Verificado hoy: `grep -c 'runSheets(' /Users/michelzarate/Documents/Claude/SmartSupply/bot/index.js` da 87, y `gspread` aparece 9 veces en `sheets_push.py` y 10 en `ehmo_pedidos.py`. Si la cifra del plan está mal, el tamaño del trabajo y el riesgo de olvidar una tubería están mal estimados. Nadie apaga lo que no censó.
Cómo: grep sobre `sheets_push.py`, `ehmo_pedidos.py` e `index.js` con `_ws(`, `worksheet`, `append_row`, `update(`, `batch_update`, `values_`, `gspread`, `spreadsheets()`, `_abrir_master`, `runSheets(`. Clasifica cada sitio LECTURA o ESCRITURA y mapéalo a un endpoint real de `/Users/michelzarate/Documents/Claude/Facturador/backend/app/api/v1/*.py`. Aprovecha que el cliente ya existe: `/Users/michelzarate/Documents/Claude/SmartSupply/bot/facturador_client.py` tiene unas 25 funciones públicas. Entrega tabla con archivo:línea, sitio, sustituto, y una columna SIN SUSTITUTO. Fila sin archivo:línea no entra. Si tu censo discrepa de la tabla «Los 20 que se quedan» del PLAN, gana tu censo y repórtalo como errata.

**P3. ¿Qué dato vive SOLO en el Master y desaparece el día que se archive la hoja?**
El Master guarda estado de negocio que el Facturador no modela. `MASTER_HDR` (`sheets_push.py:61-67`) trae «Entregar Bodega», «Reparto», «Nueva Nota», «Nota WhatsApp». `SUMMARY_FACT_HDR` y `MASTER_FACT_HDR` (líneas 78-84) guardan el amarre con la factura del SAE. El tipo de partida EXTRA o REPOSICIÓN vive en la hoja. El propio plan admite que P1, fechas y tipo de partida, sigue por construir desde el 1-sep.
Cómo: compara campo por campo contra los modelos del Facturador (`backend/app/models/`, en especial `oc_recibida`, `remision`, `remision_linea`) y confirma con las migraciones en `backend/migrations/versions`. Cada campo sale MIGRA, MODELAR con la pieza P1 a P12 que le toca, o SE PIERDE. Ojo con el comentario de `sheets_push.py:73-77`: media docena de funciones leen el Summary por índice de columna, así que el orden es contrato.

#### B. Las redes que mueren con la hoja

**P4. Si el Master desaparece, ¿quién repara una orden que no llegó al Facturador?**
Esta pregunta puede tumbar el plan entero. El PLAN lista como cobertura del riesgo «una orden se pierde al apagar el Master» la frase «Outbox más conciliación existen». Pero la conciliación LEE el Master: `facturador_conciliar.py:12` define «Perdidas» como el folio que está en el Master y no tiene orden en la bandeja, y `folios_master_ehmo()` y `folios_master_balles()` abren la hoja (líneas 47, 76, 133). Sin Master no hay contra qué comparar. La red que justifica el apagado se apaga con él.
Cómo: lee `facturador_conciliar.py` completo, incluido el comparador alrededor de las líneas 149 a 200, y el disparador en `index.js:6965-7017`, que corre `conciliar --dias 3 --reparar` cada 6 horas. Luego busca cualquier detector de hueco del lado del Facturador que no dependa de la hoja: `grep -rn 'conciliar\|faltante\|perdida' backend/app`. Veredicto: nombra el sustituto con archivo:línea o declara que no existe.

**P5. Sin Master, ¿quién detecta el duplicado y la versión vieja de una OC?**
`duplicado_exacto` y `version_vieja` leen la hoja y protegen a LOS DOS canales, WhatsApp y correo. Del lado del Facturador solo queda el ancla de idempotencia, y esa ancla colisiona: por correo el jid va vacío, así que `origen_externo = 'WA:sin-jid:<folio>'` y el folio 2518 de un cliente pisa el de otro.
Cómo: localiza ambas funciones en `sheets_push.py` y lee qué compara cada una, marcando en qué orden cortan dentro de `cmd_add` (`sheets_push.py:1113-1275`). Lee `ancla()` en `facturador_client.py:337-343` y la idempotencia del servidor en `backend/app/api/v1/oc_recibidas.py` (grep por `origen_externo`; el POST de ingesta ronda la línea 344). No basta decir que el Facturador ya es idempotente: di si lo es ante el MISMO payload o también ante una SEGUNDA versión del mismo folio. Enumera lo que queda descubierto: versión nueva del mismo folio, folio repetido entre clientes por correo, y `su_pedido` con formato de semana tipo HO-34VIL-MIE, que ya rompió producción al compararse por dígitos.

**P6. Cuando el Facturador no responde, ¿quién guarda la orden? ¿El outbox sobrevive al retiro?**
Hoy el envío al Facturador es un try secundario con la hoja de respaldo. En el estado objetivo pasa a ser el camino único.
Cómo: lee `facturador_client.py:218-323` (`_outbox_append`, `_rechazada_append`, `drenar_outbox`) y quién lo dispara: `index.js:6951-6960`, `global._factOutboxTimer` llama `runFacturador(['drenar'])` cada 5 minutos, es decir lo drena el proceso de WhatsApp y no el del correo. Responde tres cosas: si el bot está caído y el correo vivo, quién drena; qué errores encolan y cuáles descartan, mirando qué códigos HTTP disparan `_rechazada_append`; y si reenviar el mismo PDF recupera una orden encolada o `duplicado_exacto` corta antes.

**P7. Con el orden de escritura invertido, ¿qué le pasa exactamente a una orden en cada modo de falla?**
Hoy `cmd_add` escribe Master y Summary primero y manda al Facturador después. El Master es la red a propósito. El dueño quiere lo contrario y sin red.
Cómo: lee `cmd_add` en `sheets_push.py:1113-1275` y anota el orden real de bloques: parseo, corte por RQ, `duplicado_exacto`, `version_vieja`, Drive, hoja, Facturador. Construye la tabla de ocho escenarios: hoja caída, Facturador caído, bot caído con correo vivo, ambos caídos, red intermitente, respuesta 4xx, respuesta 5xx, timeout. Cada celda con archivo:línea. Incluye el escenario que decide el dictamen: Master ya archivado y una orden que no llegó, quién se entera y en cuánto tiempo.

**P8. ¿Qué flujos nunca llegaron al Facturador y quedarían sin destino?**
Las requisiciones cortan antes del Master (`sheets_push.py:1133-1136`) y por tanto nunca llegan al Facturador. Si la hoja se archiva, hay que decir si ese flujo se pierde, se queda en papel o hay que modelarlo. Lo mismo con el cierre de periodo, el Master Facturas y la hoja de armado.
Cómo: `ls backend/app/services/requisicion_*.py` y `grep -rn 'requisicion' backend/app/models backend/app/api/v1` para ver si hay persistencia o solo generación de PDF. Cada flujo sale con destino o sin destino.

#### C. Escritura, candados y permisos

**P9. ¿Existe el contrato de vista previa y aplicar, o la escritura directa llegaría sin contrato?**
Es la diferencia entre «el bot escribe» y «el bot propone y el Facturador decide». Verificado hoy: `grep -rn 'propuesta' backend/app --include='*.py'` devuelve 3 coincidencias y ninguna es un mecanismo. Son comentarios en `remisiones.py:1908`, `facturas.py:1458` y `sugerir_esquema.py`. Confírmalo y amplía a modelos y migraciones con `propuesta|preview|dry_run|simular`. Si el núcleo no existe, el estado objetivo del dueño no es quitar una pieza, es construir una. Cuenta cuántos de los comandos que ESCRIBEN lo necesitan y cuántos pueden vivir sin él.

**P10. ¿Qué candados protegen lo que ya se imprimió, se exportó o se facturó, y cubren todo lo que la escritura directa va a tocar?**
Verificado hoy: `backend/app/api/v1/remisiones.py:698` dispara el candado solo cuando `lineas_in is not None and rem.impresa_at is not None and ctx.conexion_id is not None`. Protege PARTIDAS y solo frente a una conexión. Y `grep -n 'export_sae_at' backend/app/api/v1/remisiones.py` no devuelve ninguna guarda, así que una remisión ya exportada al masivo se puede reprecear sin aviso.
Cómo: entrega la matriz estado por campo: BORRADOR, CONFIRMADA, IMPRESA, EXPORTADA, FACTURADA contra partidas, fechas, notas, precios, `su_pedido`, sucursal, con una columna ¿bloquea? Para cada candado que afirmes vigente, localiza la prueba que lo cubre en `backend/tests` (grep por `impresa_at`, `conexion_id`, `409`). Candado sin prueba se reporta como vigente pero sin red.

**P11. El vigía que reescribe remisiones cada hora, ¿muere con el Master, cambia de fuente o se invierte?**
El vigía compara Master contra remisiones con la regla «gana la más reciente» y ya reescribió nueve remisiones de la semana 38 ya impresas y firmadas.
Cómo: lee `index.js:10291-10350` (`vigia_remisiones`, `runEhmo(['vigiaremisiones','6 dias','--aplicar'])`) y el comando correspondiente en `ehmo_pedidos.py`. Di qué hace si la hoja no existe. Añade el escenario de concurrencia: el vigía, una persona en pantalla y un mensaje de WhatsApp tocando la misma remisión.

**P12. De los 20 comandos que el plan conserva, ¿cuántos puede ejecutar hoy la clave del bot sin ampliar permisos?**
Verificado hoy: `PERMISOS_CONEXION` son seis permisos fijos en código, en `backend/app/core/rbac.py:57-64`: `menu:oc`, `menu:remisiones`, `remision:gestionar`, `menu:clientes`, `menu:productos`, `factura:espejo`. Fuera quedan CFDI nativo, borrar, usuarios, series y `producto:gestionar`. Hacer de WhatsApp la única puerta obliga a decidir esa frontera, y cada permiso nuevo se le da a un canal sin autenticación humana por mensaje.
Cómo: toma la tabla de los 20 comandos del PLAN, líneas 294 a 324, y para cada uno encuentra el endpoint que necesitaría y el permiso que exige (grep por `require_perm` o `Depends(perm` en los routers). Tabla comando → endpoint → permiso → ¿está en `PERMISOS_CONEXION`? Para los que no, las tres salidas posibles: ampliar el permiso, operar en pantalla, o crear un permiso acotado nuevo. Verifica además si `factura:espejo` está sembrado en el catálogo de permisos o solo vive en código.

**P13. ¿Qué escrituras directas al SAE siguen vivas, y el retiro las reduce o las concentra?**
Si se apaga la hoja pero el bot conserva sus INSERT a INVE02, PRECIO_X_PROD02 y FACTP02, el retiro no quita riesgo: deja al mismo proceso escribiendo en el sistema fiscal con menos testigos. Además hay dos grifos que D9 no contempló: el agente de correo da de alta productos y cambia precios en el SAE con un «sí» en un hilo, sin lista blanca.
Cómo: grep sobre `sheets_push.py` y `ehmo_pedidos.py` con `INSERT INTO INVE`, `PRECIO_X_PROD`, `FACTP0`, `FOLIOSF0`, `TBLCONTROL0`, `_sae_exec_sql`, `cmd_pedido_sae`, `cmd_prefactura_sae`, `_actualizar_pedido_sae_core`. Marca cuáles están tras bandera y cuáles vivas. Cruza con `flujo_confirmar` de `email_watcher.py`, alrededor de 1355 a 1410. Verifica que `_sae_exec_sql` no reintenta, porque un reintento de escritura duplica, y que los consecutivos salen de TBLCONTROL02 y no de MAX más uno. Para cada escritura di a dónde va en el estado objetivo: al Facturador con aplicador, al Facturador sin aplicador y se pierde la escritura en SAE, o se queda en el bot como excepción declarada. Regla dura que no se toca: pedidos y facturas solo entran al SAE por el Excel masivo, y el masivo no estampa folios.

#### D. Los canales y el reloj

**P14. ¿El canal de correo sobrevive al retiro, y con qué identidad llega al Facturador?**
El estado objetivo dice «WhatsApp siempre llama al Facturador» y no menciona un segundo agente que está en producción desde el 25-ago, corre en otro proceso, no está en git, no tiene lista blanca de remitentes y manda identidad vacía. Hoy llega al Facturador como efecto secundario de escribir la hoja. Un interruptor por perfil no lo apaga. Esta pregunta puede descalificar la opción A tal como está enunciada.
Cómo: lee `/Users/michelzarate/Documents/Claude/SmartSupply/email/email_watcher.py`: `resolver_remitente` alrededor de 496 a 517, el contrato de subprocess alrededor de 303 a 321 donde el exit code siempre es 0, las llamadas `run_sheets(['add', DATA, fn])` en 960 y 1016 con tres argumentos, de donde sale el jid vacío, y `flujo_confirmar` en 1355 a 1410. Confirma que no hay una sola llamada HTTP al Facturador con `grep -n 'requests\|http'`. Confirma que no está bajo control de versiones. Del lado del Facturador, busca si existe equivalencia por correo o dominio. Reporta las tres piezas faltantes: control de versiones, marca de canal propia y tabla de equivalencias por remitente. No abras ni imprimas `email_config.json`: cita nombres de campo, nunca valores.

**P15. ¿Qué sincronización se quita exactamente, y cuál se queda?**
Hay al menos seis pasadas periódicas vivas y la frase del dueño solo nombra una. Si no separas cuál muere y cuál sigue, contestarás una pregunta distinta a la que se hizo.
Cómo: enumera todas con archivo:línea y periodo, leído del código y nunca del comentario: espejo del SAE (`index.js:6930`, `15 * 60 * 1000`, aunque el comentario de 6953 a 6957 y los textos digan 30 minutos), poller del botón Sincronizar (`index.js` cerca de 6938, 60 segundos), drenado del outbox (`index.js:6951`, 5 minutos), vigía de remisiones (`index.js:10291-10350`), conciliación Facturador contra SAE cada 6 horas (`facturador_conciliar.py:245`) y la conciliación Master contra bandeja. Etiqueta cada una MUERE, SOBREVIVE o CAMBIA DE FUENTE. Deja escrito en el dictamen, en una frase, que el espejo del SAE no es la sincronización que el dueño quiere quitar.

**P16. ¿Qué comandos exigen respuesta síncrona de verdad y cuáles son periódicos por diseño? ¿«Tiempo real» rompe algo?**
La escritura directa que pide el dueño ya existe en la entrada: `backend/app/core/config.py:93` tiene `OC_INGESTA_DIRECTA: bool = True` y el POST de `oc_recibidas.py` crea la remisión en el mismo request, alrededor de las líneas 396 y 432 (`_intentar_remision_auto`). Si el tiempo real ya está construido, la propuesta cambia de naturaleza: no es una tubería nueva, es paridad de comandos y reportes, y el riesgo se desplaza de la ingesta al estado de negocio.
Cómo: clasifica los 20 comandos en síncrono ya, síncrono posible con endpoint que falta, y dependiente de una pasada, con su latencia peor caso. Enumera además todas las condiciones que hacen que una orden caiga a REVISAR en vez de convertirse, con el motivo que escribe cada una, y contrasta esa lista con lo que hoy el Master resuelve a mano. La tasa real de REVISAR se declara no verificable: no consultes producción.

**P17. ¿Existe el interruptor `master.activo` por perfil del que cuelga todo el apagado?**
Verificado hoy: `grep -rn 'master\.activo\|MASTER_ACTIVO\|master_activo'` sobre el bot devuelve CERO coincidencias. Confírmalo. El plan apaga el Master por perfil con un interruptor y esa es su única palanca de retroceso rápido. Si no existe, el apagado es todo o nada para los cuatro motores a la vez, y eso cambia por completo el perfil de riesgo.
Cómo: verifica también cómo se declara el perfil hoy (grep por `perfil` alrededor del router de `index.js` y en `_t()` de `ehmo_pedidos.py`) y qué grupos viajan sin perfil declarado. Revisa los nombres de campo del bloque de grupos de `sheets_config.json` sin imprimir valores. Concluye si el apagado puede ser gradual y reversible sin redesplegar, o si es atómico.

#### E. Costo, reversa y evidencia

**P18. ¿Cuáles afirmaciones del PLAN son falsas hoy, y cuáles de esas cambian la conclusión?**
Auditar la propuesta incluye auditar el documento que la sostiene. Una recomendación construida sobre cifras viejas no vale.
Cómo: haz la lista de aserciones comprobables y verifica cada una contra el código. Siembra la lista con estas: el espejo dice 30 minutos en los comentarios y el código pone 15; las «86 llamadas a la hoja» contra los 87 `runSheets`; los 46 comandos y los 20 que se quedan; «2,989 renglones sin commitear» contra el estado real del árbol; «P6a precios hecho» contra el depósito de precios que cruza por la clave vieja por cliente; las 7 listas espejadas, que solo se verifican contra la base y por tanto se marcan NO VERIFICABLE. Cada aserción sale CONFIRMADA, REFUTADA o NO VERIFICABLE, con nota de si mueve el dictamen.

**P19. ¿Qué código estás auditando realmente del lado del bot?**
Verificado hoy en `/Users/michelzarate/Documents/Claude/SmartSupply/bot`: `git diff --shortstat` da 9 archivos y 6,648 inserciones, con `index.js`, `sheets_push.py`, `ehmo_pedidos.py`, `facturador_client.py`, `facturador_espejo.py`, `probar.py` y `pruebas/esperado.json` modificados. Producción se construye de disco. Auditar HEAD y dictaminar sobre producción son dos cosas distintas. Dentro de ese bulto está el arreglo de las órdenes perdidas, que es precondición del apagado. Lee siempre el árbol de trabajo, dilo en el dictamen, y no cambies de rama ni commitees nada.

**P20. ¿Cuánto trabajo real es, contado y no estimado a ojo, y en qué orden obligado?**
El plan ofrece 21 a 29 días de Facturador y 27 a 34 de bot, escritos el 1-sep. Entre el 12 y el 19-sep cambió el modelo de la clave SAE, apareció el correo, el bot duplicó su código sin commitear y el corte de Pachuca se revirtió. Recuenta contra el código de hoy y publica el método: endpoints FALTA por tamaño, sitios de llamada a redirigir, estados en memoria a convertir (grep por `pending`), migraciones nuevas, motores afectados, que son cuatro y uno no está en git. Entrega dos columnas enfrentadas, camino del dueño y camino por etapas, con la diferencia explicada renglón por renglón. Si dos recuentos distintos difieren mucho, reporta el rango y no el promedio.

**P21. ¿Hay camino de vuelta en cada etapa, y qué exactamente sería irreversible?**
El precedente está a la vista: el corte de Pachuca del 9-sep se revirtió el 19-sep, no había script de reversa, y el renombre de una serie solo se pudo hacer por SQL porque la pantalla no expone el campo.
Cómo: lee `backend/scripts/reversa_pachuca_ehmo_mafan.sql`, que arranca en modo diagnóstico y aborta a propósito, y `backend/scripts/corte_pachuca_ehmo_mafan.sql` como molde. Enumera lo irreversible leyendo la Etapa 5 del PLAN (líneas 445 a 454) y la sección «Se borra al final» (399 a 418): hoja archivada, `alias_aprendidos.json`, `remisiones.json`, `facturas_oc.json`, PROYECTO_LISTA_SAE, el código de Sheets. Para cada uno di si hay copia y dónde. Redacta la reversa exigible por etapa, escrita y no ejecutada.

**P22. Si el dictamen recomienda avanzar, ¿la red de pruebas aguanta?**
Cuando el Master deja de ser la red, lo único que queda es la suite. Pero una corrida verde puede estar saltada, porque el fixture hace `pytest.skip` si la base no responde; la base de pruebas del 5434 corre como superusuario con la RLS apagada y arrastra un inquilino residual; y el bot no tiene prueba automática real.
Cómo: lee `backend/tests/conftest.py` y localiza el `pytest.skip`. Cuenta la cobertura de la ingesta (`ls backend/tests | grep -i 'oc\|ingesta\|idempot'`). Revisa el guion `test` de `package.json` y `pruebas/esperado.json` en el bot. Si corres pytest en local, mira el conteo de skipped y decláralo. Nunca contra producción, y nunca con `./dev.sh`, que apunta el backend local a la base de producción.

**P23. ¿Qué bloquea el apagado que no es código sino datos o decisión del dueño?**
Varios pendientes no se arreglan programando: originales sin link, equivalencias faltantes, órdenes perdidas, clientes sin canal vivo. Cruza P11 del PLAN y la lista «Lo que hay que decidir» con `docs/ESTADO.md`, advirtiendo que ESTADO.md tiene un hueco declarado entre el 02 y el 19-sep. Para cada hueco: quién lo cierra, qué lo bloquea, y si es verificable sin producción.

**P24. ¿Cuáles opciones se pueden ejecutar SIN cerrar antes D18, D21, D23 y D24?**
Una opción que exige cuatro decisiones del dueño no es ejecutable esta semana aunque sea la correcta a un año. Marca por opción dependencia dura, blanda o nula, y cierra con la lista de decisiones que el dueño tendría que tomar antes del primer paso de la opción recomendada, redactadas como pregunta de sí o no.

**P25. ¿Qué control concreto se pierde al saltarse el periodo de sombra, y con qué se sustituye cada uno?**
Es la diferencia exacta entre lo que pide el dueño y lo que dice el plan. Recorre las etapas 0 a 5 del PLAN (419 a 454) y extrae cada Comprobación. Para cada una identifica qué la implementa hoy: `facturador_comparar.py`, `facturador_conciliar.py:245` con su disparador, el flock, la caché de huellas, el filtro de clientes compartidos. Entrega la tabla control perdido → sustituto propuesto → qué falta construir → qué queda descubierto. Di cuáles de esos sustitutos NO dependen de la hoja: esos son los únicos que sobreviven al estado objetivo.

---

### Cómo auditarlo

**Reglas de la casa para esta auditoría.** Solo lectura. No tocas producción, no consultas la base de producción, no cambias de rama, no commiteas, no ejecutas scripts de corte ni de reversa, no imprimes secretos ni valores de archivos de configuración. Auditas el árbol de trabajo, no HEAD. Si una afirmación exige correr el sistema o mirar producción, se marca NO VERIFICABLE DESDE EL CÓDIGO y se convierte en pregunta para el dueño.

Corre estos ocho workflows. Cada uno lanza varias lentes en paralelo sobre territorios distintos, y cada uno termina con un paso de verificación que puede tumbar lo que produjo.

**W1. Censo de tuberías.** Levanta el mapa completo de por dónde entra y sale un dato hoy: cada escritura y lectura a la hoja, cada llamada al Facturador, cada escritura directa al SAE, cada proceso de launchd.
Cinco lentes en paralelo: `sheets_push.py` (motor Balles y Jubran), `ehmo_pedidos.py` (motor EHMO y MAFAN), `index.js` con foco en los timers 6897 a 7017 y el vigía 10291 a 10350, `email_watcher.py`, y los routers del Facturador en `backend/app/api/v1` más `config.py`.
Verificación: una sexta lente rehace el censo con greps independientes y solo se conserva lo que coincide. Lo que vio una sola lente se marca DUDOSO y se resuelve leyendo el archivo entero. Ninguna fila existe sin archivo:línea en los dos lados. La columna SIN SUSTITUTO es la lista de trabajo real del retiro.

**W2. Qué vive solo en el Master.** Inventario de pérdida, campo por campo y reporte por reporte.
Tres lentes: esquema de la hoja, que además rastrea qué funciones leen el Summary por índice de columna; modelo del Facturador sobre `backend/app/models` y las migraciones; reportes y consumidores, que busca para cada reporte del Master su equivalente en `backend/app/services` (`reporte_pdf.py`, `estado_cuenta_xlsx.py`, `lista_export.py`).
Verificación: una cuarta lente toma diez campos al azar del veredicto MIGRA e intenta refutarlos buscando el destino real. Si no lo encuentra, el campo baja a MODELAR.

**W3. Simulacro de apagón, de escritorio y de solo lectura.** Los ocho escenarios de falla de P7 contra el código, con el orden de escritura invertido, más el escenario decisivo: Master archivado y una orden que no llegó.
Un agente por escenario, todos obligados a citar archivo:línea.
Verificación: un árbitro rechaza toda conclusión sin cita y obliga a reescribirla. Cada modo de falla trae un incidente precedente documentado (las nueve remisiones de la semana 38, las 14 órdenes perdidas por fechas, la colisión de folio del correo) o la marca de hipotético.

**W4. El reloj del sistema.** Inventario de todas las pasadas periódicas con archivo:línea y periodo, cada una etiquetada MUERE, SOBREVIVE o CAMBIA DE FUENTE, más la clasificación de los 20 comandos en síncronos hoy, síncronos posibles y dependientes de pasada.
Tres lentes: espejo del SAE y su cola de solicitudes; vigía y conciliaciones; alertas y temporizadores del chat.
Verificación: cada periodo se cita del código y nunca del comentario. Precedente conocido: el código dice 15 minutos donde el texto dice 30. Una lente de cierre revisa que ningún timer quede sin etiqueta.

**W5. Contrato de escritura, candados y frontera de permisos.** Existencia del núcleo de propuesta, cobertura real de `impresa_at`, ausencia de guarda sobre `export_sae_at`, mapeo comando a permiso, y el inventario de escrituras al SAE que sobreviven a cada opción.
Cuatro lentes: contrato; candados, con la matriz estado por campo; permisos, sobre `rbac.py:57-64` y los decoradores de cada router; y concurrencia.
Verificación: la lente de permisos intenta romper la tabla de comandos buscando uno que exija un permiso no listado. Todo candado que se afirme vigente debe traer su prueba en `backend/tests`, o se reporta como vigente pero sin red. Conteo explícito de permisos nuevos por opción, donde cero es el mejor resultado.

**W6. Auditoría de las aserciones del plan.** Somete `PLAN-retiro-master-ordenes.md` a verificación.
Dos lentes adversarias sobre la MISMA lista, sin verse: una busca confirmar cada aserción, la otra busca un contraejemplo. Una tercera concilia y solo acepta el veredicto que trae evidencia. Empate sin evidencia significa NO VERIFICABLE.

**W7. Camino de vuelta, puerta de pruebas y riesgo de despliegue.**
Tres lentes: reversa, con los scripts del corte de Pachuca como molde; pruebas, sobre `conftest.py`, la cobertura de ingesta e idempotencia y el guion `test` del bot; despliegue e higiene, sobre `deploy.sh`, `CLAUDE.md`, el estado de git del bot y el hecho de que el agente de correo no está en git.
Verificación: la lente de pruebas declara el número de skipped, o dice que no pudo, en vez de suponer verde.

**W8. Duelo de las seis opciones.** Evita el sesgo de contestar sí o no a la única propuesta sobre la mesa.
Seis lentes abogadas en paralelo, una por opción, obligadas a dos cosas: defender su opción con citas de W1 a W7, y escribir «qué me mata», el hallazgo que la descalificaría. Redactan a ciegas y con el mismo insumo. Un juez único las puntúa contra los criterios y declara empates.
Verificación: una lente falsadora toma la opción ganadora y se dedica exclusivamente a tumbarla con el código en la mano. Si lo logra, la matriz se recalcula. El auditor final tacha toda afirmación sin ancla en W1 a W7 y comprueba que el veredicto no contradiga ninguna fila de la tabla de modos de falla de W3.

---

### Qué entregar

Un documento, `/Users/michelzarate/Documents/Claude/Facturador/AUDITORIA-retiro-master.md`, con estas piezas en este orden.

1. **DICTAMEN, una página.** RETIRAR, RETIRAR CON CONDICIONES o NO RETIRAR TODAVÍA, en una frase. La opción recomendada. Las tres razones que la hacen ganar. El hallazgo que la tumbaría. La primera acción concreta con su reversa. Si el mejor destino y la mejor próxima acción no coinciden, se dicen las dos, por separado. Se lee solo, sin el resto.
2. **MAPA DE TUBERÍAS (W1).** Toda escritura y lectura hoy viva: archivo:línea, destino, sustituto en el Facturador, y la columna SIN SUSTITUTO. Las filas huérfanas, solo bot o solo hoja, van listadas aparte.
3. **INVENTARIO DE PÉRDIDA (W2).** Campo por campo y reporte por reporte, con veredicto MIGRA, MODELAR con su pieza P1 a P12, o SE PIERDE A PROPÓSITO. Es el documento que el dueño firma antes de archivar la hoja.
4. **TABLA DE MODOS DE FALLA (W3).** Ocho escenarios por dos órdenes de escritura, el de hoy y el invertido, por las cuatro tuberías de entrada: WhatsApp Balles y Jubran, WhatsApp EHMO, correo, y captura en pantalla. Qué se pierde, dónde queda rastro, quién se entera y en cuánto tiempo.
5. **RELOJ DEL SISTEMA (W4).** Cada pasada periódica con archivo:línea, periodo leído del código y etiqueta MUERE, SOBREVIVE o CAMBIA DE FUENTE. Con la frase explícita de qué sincronización se quita y cuál se queda.
6. **FICHA DE CONTRATO, CANDADOS Y PERMISOS (W5).** La matriz estado por campo, la lista de comandos que hoy una conexión no podría ejecutar con sus tres salidas, y el conteo de permisos nuevos por opción.
7. **HOJA DE ASERCIONES DEL PLAN (W6).** Cada afirmación comprobable marcada CONFIRMADA, REFUTADA o NO VERIFICABLE, con nota de si mueve el dictamen, y un parche de texto propuesto para `PLAN-retiro-master-ordenes.md`, redactado y NO aplicado. La auditoría es de solo lectura.
8. **TABLERO DE LAS SEIS OPCIONES (W8).** Matriz opción por criterio, cada celda con veredicto y cita, más la fila «qué me mata» de cada una. Si el camino recomendado no es el del dueño, la frase exacta de su estado objetivo que no se cumple y por qué, en una línea.
9. **COSTEO.** Las dos columnas enfrentadas, con el método de conteo publicado y el rango cuando dos recuentos difieren.
10. **PUERTA DE ARRANQUE.** Las precondiciones no negociables antes del primer cambio, cada una con cómo se comprueba. Candidatas ya detectadas: el bot commiteado con prueba que corra de verdad, el agente de correo bajo git con marca de canal propia, el ancla de idempotencia sin colisión entre clientes, y un detector de huecos que no lea la hoja.
11. **SECUENCIA Y REVERSA POR ETAPA (W7).** El orden obligado con puertas verificables sin tocar producción: qué se verifica con pytest contra la base local del 5434 mirando el conteo de skipped, qué con `npx tsc --noEmit`, qué con lectura de código, y qué no se puede verificar sin el dueño. Para cada etapa, qué se deshace y cómo, escrito y no ejecutado, más la lista de lo irreversible y dónde está su copia. Marca qué pasos los tiene que correr el dueño porque el clasificador de permisos los bloquea.
12. **PREGUNTAS PARA EL DUEÑO.** Máximo siete, redactadas como sí o no, separadas en las que bloquean el primer paso y las que pueden esperar, cada una con sus opciones y el coste de cada opción. Salen de lo que la auditoría no puede decidir: D18, D21, D23, D24, notas de crédito de la empresa 04, la regla de la empresa 05, los clientes sin canal vivo, y si el canal de correo entra al alcance o se apaga.
13. **REGISTRO DE NO VERIFICABLE.** Toda afirmación heredada que no se pudo confirmar, con lo que haría falta para confirmarla. Ninguna conclusión del dictamen puede apoyarse en una línea de este registro sin decirlo en el mismo párrafo.

---

### Criterios

Con estos se puntúan las seis opciones, y con estos se sostiene o se cae el dictamen.

1. **COBERTURA.** Cada fila del censo de W1 tiene sustituto con archivo:línea, o queda escrita como pérdida aceptada en el inventario de W2. Una sola tubería sin sustituto y sin firma del dueño basta para que el dictamen no sea RETIRAR.
2. **RED SIN LA HOJA.** Tiene que existir un detector de órdenes faltantes que no lea el Master, y un drenado de la cola de reintentos que no dependa exclusivamente del proceso de WhatsApp. Este es el criterio duro. Si la conciliación de 6 horas muere con el Master y nada la sustituye, el retiro pierde su única cobertura documentada.
3. **NINGUNA ORDEN MUERE EN SILENCIO.** Cada camino de entrada, en los dos canales, con acuse al cliente, reintento que alguien drene, y conciliación contra una contraparte que siga viva después del cambio.
4. **IDENTIDAD ÚNICA.** Toda orden que entre trae canal, remitente e identidad propios, y el ancla de idempotencia no colisiona entre clientes de distinto origen. Mientras el correo mande `sin-jid`, dos clientes con el mismo folio se pisan y el segundo se descarta en silencio.
5. **NADA ESCRIBE SIN CONTRATO SOBRE LO YA FIRMADO.** Si el núcleo de vista previa y aplicar no existe, «escritura directa en tiempo real» es exactamente el patrón que reescribió nueve remisiones impresas. El dictamen dice si eso se construye antes, o si el alcance de escritura se recorta.
6. **REVERSIBILIDAD.** Cada etapa tiene su reversa escrita antes de empezarla, y nada irreversible ocurre antes del periodo en que la hoja queda de solo lectura. Opción sin reversa escrita queda descalificada para ser la próxima acción, por buena que sea a un año. El precedente manda: el corte de Pachuca se revirtió sin script y costó diez días.
7. **ESTADO INTERMEDIO SEGURO.** Si el trabajo se detiene a la mitad, y aquí se detiene a la mitad con frecuencia, el sistema queda operable y no peor que antes. Es el desempate cuando dos opciones empatan en todo lo demás.
8. **FRONTERA DE PERMISOS.** Se mide cuántos permisos nuevos exige cada opción sobre los seis de `PERMISOS_CONEXION`. Cero es el mejor resultado. Cualquier ampliación se nombra, se dice qué abre y quién la aprueba, y se reporta como costo. Nunca `producto:gestionar` ni nada de CFDI nativo para la clave del bot en esta ronda.
9. **INDEPENDENCIA DE DECISIONES ABIERTAS.** La opción recomendada como próxima acción no puede depender de D18, D21, D23 ni D24. Si depende, el dictamen dice dos cosas distintas: cuál es el mejor destino y cuál es la mejor próxima acción.
10. **HIGIENE PREVIA, COMO PUERTA Y NO COMO CONSEJO.** El bot commiteado con prueba automática que corra de verdad, y el agente de correo bajo control de versiones. Con miles de renglones en el aire y un agente en producción fuera de git, cualquier medición del antes y el después es ficción.
11. **EVIDENCIA.** Ninguna afirmación del dictamen sin archivo:línea o sin la etiqueta explícita NO VERIFICABLE DESDE EL CÓDIGO. Nada se da por aplicado porque esté en el repositorio, y nada se consulta contra producción.
12. **SE AUDITA EL CÓDIGO QUE CORRE.** Producción se construye de disco. Un hallazgo basado en HEAD que contradiga el árbol de trabajo es un hallazgo inválido.
13. **COSTO MEDIDO, NO OPINADO.** Piezas exigidas, sitios a redirigir, estados a convertir, migraciones y endpoints nuevos, con el método publicado. Los días del PLAN solo valen si se recuentan contra el código de hoy. Si no se recuentan, no se citan.
14. **LAS REGLAS DE LA CASA SIGUEN EN PIE.** El camino recomendado no toca: los folios los pone siempre el sistema, sin ceros a la izquierda ni espacios; el masivo deja rastro y no estampa; pedidos y facturas solo entran al SAE por Excel; lo facturado no se toca; las escrituras cruzadas piden confirmación explícita; y lo que el cliente firmó no lo reescribe una sincronización. Una opción que roce cualquiera de estas se rechaza sin puntuar, o se marca como cambio de regla explícito para que lo apruebe el dueño.
15. **GANA EN EL MISMO TABLERO.** El camino recomendado supera a los otros cinco en riesgo de pérdida de órdenes, reversibilidad y tiempo hasta el estado objetivo, no solo en elegancia. Un empate se resuelve por el de menor pérdida de órdenes.
16. **UMBRAL EXPLÍCITO PARA LA OPCIÓN DEL DUEÑO.** La opción A se recomienda solo si se cumplen las tres a la vez: las guardas que hoy viven en la hoja, duplicado exacto y versión vieja, existen o se pueden mover al Facturador en el mismo paso; los campos del estado de negocio existen o su ausencia está aceptada por escrito por el dueño; y el canal de correo tiene camino propio declarado. Si falla cualquiera, el dictamen recomienda la opción con red que más se le parezca y dice exactamente qué habría que cerrar para llegar a A. Sin adornos: el dueño pidió lo simple, y si lo simple no se puede hoy, merece saber por cuál línea de código exactamente.
17. **ALINEACIÓN DECLARADA.** Si el dictamen se aparta de lo que pidió el dueño, lo dice en una frase al principio, señalando qué parte de su estado objetivo no se cumple y a cambio de qué.

---

## El negocio en diez líneas

La empresa distribuye frutas, verduras y abarrotes a comedores institucionales: hospitales,
planteles, centros de reinserción y comedores de gobierno, en varias plazas de México.

El ciclo es siempre el mismo. Un cliente manda su pedido por WhatsApp, casi siempre como un PDF de
orden de compra o como una foto de una hoja escrita a mano. Alguien lo captura, se cruza cada
renglón contra el catálogo y su lista de precios, se arma la mercancía en bodega, se entrega, y al
final se factura. Entre el pedido y la factura hay un documento intermedio, la **remisión**, que es
el comprobante de lo que realmente se entregó.

Hoy ese ciclo vive repartido en tres sistemas que no se hablan del todo, y el pegamento entre ellos
es una hoja de cálculo.

---

## Los tres sistemas

### El Facturador

Es el sistema propio, el que la empresa quiere que sea el centro. Vive en `~/Documents/Claude/Facturador`.

**Qué es.** Un sistema de facturación electrónica mexicana (CFDI) con todo el ciclo comercial
alrededor: clientes, catálogo de productos, listas de precios, remisiones, facturas, cobranza,
inventario y punto de venta. Emite CFDI reales a través de un proveedor autorizado (Facturama).

**Cómo está hecho.** Backend en Python con FastAPI y SQLAlchemy, con migraciones de Alembic en
`backend/migrations/versions`. Frontend en Next.js con App Router. Base en Supabase (Postgres). Se
despliega con `./deploy.sh`, que construye imágenes y levanta contenedores; vive en `facturador.mx`
con subdominios para la API, la aplicación y el administrador.

**Es multi-inquilino.** Cada empresa cliente del producto es un *tenant*, aislado por seguridad a
nivel de fila en la base. Los permisos viven en `backend/app/core/rbac.py`. Hay además un alcance
por cliente: un usuario puede ver solo ciertos clientes.

**El modelo de datos que tienes que entender.** Estos nombres aparecen en todo el resto del documento:

| Tabla | Qué es en el negocio |
|---|---|
| `clientes` | La razón social a la que se factura. |
| `sucursales` | La **plaza**, no una sucursal del cliente: la operación regional (Pachuca, Tabasco, Tuxtla). De ahí sale la mercancía. |
| `cliente_sucursales` | El vínculo cliente por plaza. **La serie de folios es de esta relación**, no del cliente ni de la plaza. |
| `cliente_sucursal_series` | El abanico de series disponibles en ese vínculo. |
| `proyectos` | La negociación concreta dentro de una plaza (HOSPITALES, DIF, CEREZOS). Decide la lista de precios. |
| `series` | Los folios. Tiene `espejo_sae`: si está encendido, esa serie la factura el SAE y aquí solo se refleja. |
| `productos` | El catálogo canónico. Desde septiembre tiene `clave_sae`: una sola clave por producto. |
| `producto_clientes` | El código y el nombre que ese cliente usa para ese producto. Va al CFDI. |
| `producto_alias` | El vocabulario: cómo escribe cada cliente el nombre de un producto. Es lo que permite cruzar «jitomate saladet» con el producto real. |
| `listas_precios`, `precios`, `lista_asignaciones` | Los precios. La asignación se resuelve por especificidad: proyecto pesa 8, serie 4, plaza 2, cliente 1. |
| `oc_recibidas` | La orden como llegó, con su documento original y su payload crudo. Estados: PENDIENTE, ASIGNADA, DESCARTADA. |
| `remisiones` | Lo que se entrega. Estados: BORRADOR, RESERVADO, CONFIRMADA, FACTURADA, CANCELADA. |
| `facturas` | El CFDI. Tiene `origen`: NATIVA si la emitió el Facturador, ESPEJO_SAE si es el reflejo de una que emitió el SAE. |

**Cómo entra una orden hoy.** Por API, `POST /oc-recibidas`, idempotente por una marca de origen.
Desde el 9 de septiembre la ingesta **crea la remisión directamente** en el mismo request cuando
puede resolver cliente y destino; si queda duda, la orden aparece como una fila marcada REVISAR
dentro de la tabla de remisiones. La pantalla de bandeja que existía se retiró.

**Reglas de la casa, escritas en `CLAUDE.md` y en el código.** Los folios los pone siempre el
sistema, sin ceros a la izquierda. El Excel masivo para el SAE deja rastro pero **nunca estampa** un
folio que el SAE no haya confirmado. Una escritura que toca una entidad ajena, como una remisión que
cambia un precio de lista, exige confirmación explícita. Nadie trabaja sobre `main`.

### Smart Supply

Es el agente que atiende a los clientes. Son dos programas distintos.

**El bot de WhatsApp.** Vive en `~/Documents/Claude/SmartSupply/bot`. Corre en una Mac Mini bajo
launchd. Es Node (`index.js`, unas 9,300 líneas) más scripts de Python (`sheets_push.py`, unas
10,800; `ehmo_pedidos.py`, unas 7,200).

Tiene **dos motores que no comparten código**: uno atiende a los clientes que mandan PDF de orden de
compra, y otro a los que mandan fotos de hojas de pedido. Cada grupo de WhatsApp tiene un rol
(interno o cliente) y un perfil que decide qué motor lo procesa.

El bot **parsea el documento, cruza cada renglón contra su catálogo local, calcula precios y
escribe el Master**. Después, y solo después, manda la orden al Facturador. Tiene un patrón
conversacional de confirmación: toda operación que cambia algo muestra el antes y el después y
espera un «sí» del mismo participante. Esa memoria vive en variables del proceso, once en total, y
**se pierde al reiniciar**.

Corre varios temporizadores: el espejo de facturas cada 30 minutos, el drenador de su cola de envío
cada 5, la conciliación entre el Master y el Facturador cada 6 horas, y alertas a horas fijas.

**El agente de correo.** Vive en `~/Documents/Claude/SmartSupply/email`. Atiende el buzón
`pedidos@frutaskelly.com`. Lee órdenes y requisiciones en PDF y las procesa **llamando al mismo
motor del bot**, así que escribe el Master igual que WhatsApp y también llega al Facturador. **No
está bajo control de versiones** y está en producción.

### El SAE

Es Aspel SAE, el sistema administrativo comercial que la empresa usa desde antes. Corre en Windows
con SQL Server, y se consulta desde la Mac por línea de comandos a través de un túnel.

**Hoy el SAE es quien factura** a casi todos los clientes, y es la fuente de verdad del catálogo y
de los precios. Tiene cuatro empresas activas, numeradas 02 a 05; la 01 no existe. Las cuatro emiten
con el mismo RFC.

El Facturador **nunca escribe en la base del SAE**. La comunicación es en dos sentidos y por dos
caminos distintos:

- **Hacia el Facturador, el espejo.** Un conector que corre junto al SAE lee sus facturas y las
  deposita en el Facturador como reflejos, ligándolas con su remisión. También espeja precios de las
  listas vinculadas y claves de producto.
- **Hacia el SAE, el Excel masivo.** El Facturador genera un archivo que un operador importa a mano
  en Aspel. Es el único camino por el que entran pedidos y facturas.

Las únicas escrituras directas al SAE las hace el bot, desde el chat y con confirmación: dar de alta
un producto y cambiar un precio.

**Los clientes y su actividad** en los 30 días previos al 12 de septiembre:

| Cliente | Empresas | Facturas 30 d | Canal de WhatsApp |
|---|---|---|---|
| EHMO | 02, 03, 04, 05 | 572 | Sí, Pachuca y Tabasco |
| BALLES | 02 | 270 | Sí, comparte grupo con Jubran |
| GRUPO SUREÑA | 04 | 142 | **No tiene** |
| JUBRAN | 02 | 76 | Sí |
| CDS | 04, 05 | 48 | Configurado pero apagado |
| BODEGA DE DON PEDRO | 04 | 30 | Configurado pero apagado |
| MAFAN | 02 | 29 | Sí |
| CASA DE SOCTONES | 04, 05 | 16 | **No tiene** |
| CENTRO DE VIDA SANA | 04 | 4 | **No tiene** |
| RÍO LIBRE | 02 | 0 en SAE | Factura nativo en el Facturador |
| CHANEQUES | 02 | 0 | Fuera del alcance por decisión del dueño |

Son doce series de factura vivas. La empresa 04 factura casi tanto como la 02 y **el conector solo
empezó a espejarla el 16 de septiembre**. La empresa 05 está muerta.

---

## La propuesta que tienes que auditar

Vive en `PLAN-retiro-master-ordenes.md`, en la raíz de este repositorio. Ábrelo: empieza con una
sección «Cómo retomar esto» que te pone al día en dos minutos.

**Su objetivo.** Que el bot deje de escribir y leer la hoja de Google, y que cada comando del chat
se resuelva contra el Facturador. El bot se vuelve un cliente delgado: conserva la conversación, el
Facturador conserva la verdad.

**Su forma.** Doce piezas por construir en el Facturador, numeradas P1 a P12, y seis etapas con una
comprobación cada una. Más un plan aparte para el bot, de siete fases, cuya fase 0 es «congelar el
Master y pisar firme».

**Sus decisiones.** Están numeradas D9 a D24 y las tomó el dueño. Las que más pesan: los pedidos y
facturas entran al SAE solo por Excel masivo; el SAE manda en catálogo y precios hasta el corte,
aunque hay un giro pendiente de aprobar que lo invertiría; los comandos se unifican en una sola
gramática global; y el corte de facturación es por plaza, no por cliente.

**Su estado real.** Tres cosas se adelantaron fuera del orden previsto: la bandeja de órdenes
desapareció y se fusionó con remisiones, el espejo de precios llegó a producción, y se cortó y se
revirtió la facturación de una plaza. Cinco piezas no se han movido: las fechas de entrega, la
edición con vista previa, el resumen de órdenes, la hoja de armado y la cola de eventos. Y la fase 0
del bot **empeoró**: tiene miles de renglones sin commitear.

---

## Cómo trabajar aquí

**Los repositorios.** El principal es el del Facturador, que es donde vive el plan y donde debe
quedar tu dictamen. El bot y el agente de correo están en `~/Documents/Claude/SmartSupply`.

**Las reglas del repositorio del Facturador**, en su `CLAUDE.md`: nadie trabaja sobre `main`; se
trabaja en una rama y se integra por PR; `main` solo se actualiza con `git pull`; y el despliegue es
únicamente `./deploy.sh`. Varias sesiones comparten el mismo checkout, así que un archivo suelto
puede publicar el trabajo a medias de otra sesión.

**Las pruebas.** El backend con pytest desde su entorno virtual; el frontend con `npx tsc --noEmit`.
Hay una base de pruebas local en un contenedor Docker, en el puerto 5434. Ojo: suele estar una
migración atrasada, y corre como superusuario, así que la seguridad por fila está apagada y los
filtros por inquilino hay que ponerlos explícitos o las pruebas salen intermitentes.

**Producción.** Se llega con `psql` usando una variable de `.env.prod`. La que funciona es la del
pooler; la de conexión directa tiene la contraseña vieja. Extrae el valor a una variable antes de
usarlo: hacerlo en línea dentro de comillas dobles sale vacío y acabas conectado a un servidor local
que no existe. **Nunca imprimas el valor.**

### Trampas que ya costaron caro

- **Hay dos inquilinos en producción con los mismos clientes.** El vivo es
  `cristian-gerardo-zarate-orozco`. Toda consulta o arreglo manual debe acotarlo, o los números
  salen inflados.
- **Un script que existe en el repositorio no es un script que se aplicó.** El del corte desvincula
  cinco listas de precios y en la base siguen vinculadas. Esa suposición metió un error en el plan
  que hubo que corregir. Verifica contra la base.
- **El bot escribe remisiones del Facturador cada hora** con la regla de que gana la más reciente.
  Ya reescribió nueve remisiones impresas y firmadas, ocho cobrando de menos y una de más. Hay un
  candado, pero solo protege las partidas: las fechas y las notas siguen pasando.
- **El agente de correo llega al Facturador con identidad vacía.** Dos clientes con el mismo número
  de folio se pisan.
- **El bot tiene miles de renglones sin commitear** y su rama principal no arranca limpia: un
  comando tiene el disparador subido y el motor no. Dentro de ese bulto está además el arreglo que
  cierra un pendiente viejo de órdenes perdidas.
- **El renombre de una serie solo se puede hacer por base de datos.** La pantalla no expone ese campo.
- **El Facturador no puede emitir notas de crédito** y la empresa 04 sí las usa.

---

## Lo que ya se hizo y lo que está pendiente

**Hecho.** La bandeja de órdenes se fusionó con remisiones y la ingesta crea la remisión directa. El
espejo de precios está en producción. El detector de órdenes que cambian después de remisionarse
está en producción. El estado de cuenta se puede sacar en Excel con el formato del SAE. El corte de
facturación de una plaza se hizo, se decidió revertirlo, y **la reversa se aplicó y se verificó el
19 de septiembre**: hoy el único cliente que factura nativo es Río Libre.

**Pendiente inmediato.** Sacar por el Excel masivo las 137 remisiones de esa plaza que quedaron en
borrador sin factura.

**Pendiente de fondo.** Las cinco piezas que no se han movido, la fase 0 del bot, y cuatro
decisiones abiertas: dónde se guarda el documento original de cada orden; ocho remisiones de una
plaza que divergen de lo que se subió al SAE; qué hacer con los más de veinte comandos que nacieron
en el Master fuera de la gramática acordada; y el giro que convertiría al Facturador en el origen
del catálogo y los precios.

---

## Qué NO debes hacer

- **No escribas en producción** sin que el dueño lo pida explícitamente. Leer está bien; cambiar, no.
  Esta es una auditoría.
- **No toques el SAE.** Ni una escritura. Sus contadores ya rompieron la facturación dos veces.
- **No modifiques los repositorios del bot ni del correo.** Tienen trabajo sin commitear de otras
  sesiones y lo perderías.
- **No imprimas secretos.** Los archivos de configuración del bot y del correo, y los `.env`, tienen
  contraseñas y llaves en claro. Reporta nombres de campo, nunca valores.
- **No des por buena una afirmación del plan** sin verificarla. Ya falló dos veces.
- **No trabajes sobre `main`.** Rama y PR.
