# PLAN — Retiro del Master Órdenes

> Propuesta del 1 de septiembre de 2026, sin código. Verificada contra el código del bot
> (`index.js`, `sheets_push.py`, `ehmo_pedidos.py`), el Facturador en `main e03912c`, el
> `PLAN-migracion-master-facturador.md` del 28-ago y 30 días de bitácora del router del bot
> (2 al 31 de agosto). Versión legible con tablas y diagrama: artifact «Retiro del Master Órdenes».
> Decisiones D9 a D17 y D22 resueltas por el dueño el 2 de septiembre de 2026 (marcadas en el
> texto). REVISADO el 12-sep y de nuevo el 19-sep: empezar por «Revisión del 19 de septiembre».
> Abiertas: D18, D21, D23 y D24 (el giro del catálogo); D19 y D20 quedaron sin sujeto o resueltas.

## Objetivo

El bot de WhatsApp deja de escribir y leer la hoja de Google («Master Órdenes» de Balles/Jubran y
«Master EHMO» por perfil). Cada comando del chat se resuelve contra el Facturador, y todo producto
o precio que nazca en el SAE aparece en el Facturador por un espejo de catálogo. La operación se ve
igual desde el teléfono; cambia dónde vive la verdad. Este plan es independiente del corte de
facturación del SAE, que sigue su propio calendario. El corte de Pachuca (9-sep) se está
revirtiendo por decisión del dueño: el único cliente nativo es Río Libre.

## Cómo retomar esto

Sección de traspaso, escrita el 19 de septiembre de 2026 para quien retome el tema sin haber
estado en las conversaciones anteriores. Todo lo que importa vive en este repositorio; nada
depende de la memoria de una sesión.

**Si lo que vas a hacer es auditar esta propuesta**, empieza por
`BRIEFING-AUDITORIA-RETIRO-MASTER.md`, en esta misma raíz: explica los tres sistemas desde
cero y trae el mandato de auditoría.

### Dónde está cada cosa

| Archivo | Qué es |
|---|---|
| `PLAN-retiro-master-ordenes.md` (este) | La propuesta completa, con sus tres revisiones: 1, 12 y 19 de septiembre. Se lee de arriba abajo, pero las revisiones van primero a propósito: el Resumen y las secciones de más abajo describen el plan original y algunas de sus frases quedaron superadas, marcadas donde corresponde. |
| `backend/scripts/reversa_pachuca_ehmo_mafan.sql` | La reversa del corte de Pachuca. **Arranca en modo diagnóstico** y aborta a propósito para no aplicar nada. |
| `backend/scripts/corte_pachuca_ehmo_mafan.sql` | El corte del 9 de septiembre, que es lo que la reversa deshace. |
| `PLAN-corte-pachuca-ehmo-mafan.md` | El plan de aquel corte, con su checklist. |
| `docs/ESTADO.md` | El estado rodante del proyecto, día a día. Es el punto de entrada general. |

El bot de WhatsApp vive en otro repositorio, `~/Documents/Claude/SmartSupply/bot`, y el agente de
correo en `~/Documents/Claude/SmartSupply/email`.

### Lo inmediato

**La reversa ya se aplicó**, el 19 de septiembre. Las series quedaron ZEHMOHOS y ZMAFAN con el
espejo encendido, la primera con su contador corregido a 912; la fila vacía de FMAFAN se absorbió
y su referencia pasó a ZMAFAN; y las dos series de remisión recuperaron su nombre. Verificado
después: ninguna serie conserva el nombre del corte y no quedó una sola referencia rota. Río Libre
sigue siendo el único cliente nativo.

Lo siguiente es **sacar por el masivo las 137 remisiones de Pachuca que están en borrador sin
factura** para que el SAE las facture y el espejo las traiga de vuelta. Ojo con el detalle
cosmético: las remisiones anteriores al renombre conservan su folio impreso con el prefijo viejo,
así que el histórico se lee partido entre los dos prefijos. Es esperado y no afecta nada.

Para correrlo hace falta la cadena de conexión de producción, que sale de `.env.prod`. La forma que
funciona es asignarla a una variable antes de usarla; extraerla en línea dentro de comillas dobles
sale vacía y psql acaba buscando un servidor local que no existe.

```bash
U=$(grep -E '^ALEMBIC_DB_URL=' .env.prod | head -1 | cut -d= -f2- | tr -d '"' | sed 's/+psycopg2//')
```

### Lo que no está en ningún código y hay que saber

- **El bot tiene miles de renglones sin commitear** y su rama principal está rota a medias: un
  comando tiene el disparador subido y el motor no. Dentro de ese bulto está además el arreglo que
  cierra el pendiente de las órdenes perdidas. Commitearlo es el primer paso de la Fase 0 y no
  debería empezarse nada más sin eso.
- **El agente de correo no está bajo control de versiones** y está en producción. Registra órdenes
  en el Master y también en el Facturador, con identidad vacía.
- **El bot escribe remisiones del Facturador cada hora** con la regla de que gana la más reciente.
  Ya reescribió nueve remisiones firmadas. El Facturador le puso candado, pero solo protege las
  partidas.
- **No creer que un script del repositorio se aplicó.** El del corte desvincula cinco listas de
  precios y en la base siguen vinculadas; esa suposición metió un error en este documento que hubo
  que corregir. Verificar siempre contra la base.
- **Hay dos inquilinos en producción con los mismos clientes.** El vivo es
  `cristian-gerardo-zarate-orozco`. Toda consulta o arreglo manual debe acotarlo.
- **El renombre de una serie solo se puede hacer por base de datos.** La pantalla no expone el
  código de la serie.

### En qué estado están las decisiones

Decididas: D9 a D17 y D22. Por cerrar formalmente, resueltas en la práctica: D19 y D20. Abiertas:
D18, el archivo original de cada orden; D21, las ocho remisiones de Tabasco; D23, los más de veinte
comandos que nacieron en el Master; y D24, el giro que convierte al Facturador en el origen del
catálogo y los precios. Las cuatro están explicadas en la revisión del 19 de septiembre y en la
tabla de decisiones.

## Resumen

- **El Master hace tres trabajos**: almacén de órdenes, estado de negocio (SIN CLAVE / SIN PRECIO /
  PRECIO EN CONFLICTO, lote EXTRA / REPOSICIÓN, fecha de bodega y reparto, amarre con la factura,
  quién hizo qué) y reportes (resumen de órdenes, hoja de armado, sin precio, Master Facturas,
  estado de cuenta, cierre de periodo). El Facturador ya hace bien el primero (ingesta directa
  idempotente, sin pantalla de bandeja); la conciliación de cada 6 h existe y la causa raíz de las
  14 órdenes que no vio ya se encontró (falta commit y test); le faltan piezas del segundo y casi
  todo el tercero.
- **46 comandos vivos en dos motores** que no comparten código (`sheets_push.py` para Balles/Jubran,
  `ehmo_pedidos.py` para EHMO/MAFAN). 7,294 comandos en 30 días solo en la tubería de Balles.
- **El bot se vuelve un cliente delgado**: un comando = una llamada con vista previa y aplicar. El bot
  conserva la conversación; el Facturador conserva la verdad. Un solo cruce de productos (el del
  Facturador; los alias del bot ya se migraron el 28-ago) y una sola gramática de comandos para
  todos los clientes: de 46 en dos motores a 20 globales (D22); los poco usados se retiran.
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

## Revisión del 19 de septiembre

Una semana, 26 commits del Facturador y uno solo del bot. La quincena no movió el plan hacia
adelante: lo giró. Tres decisiones tuyas lo reordenan, apareció un segundo agente que registra
órdenes y que este documento no conocía, y el bot empezó a escribir remisiones sin el contrato que
el plan exigía. Hay además un bloqueo operativo abierto en Pachuca.

| Tema | Qué decía el plan | Qué pasó del 12 al 19 | Estado |
|---|---|---|---|
| **Corte del SAE** (Fase 5) | Pachuca cortó el 9-sep; la receta por serie se repite plaza por plaza | Se revierte por decisión tuya: EHMO y MAFAN vuelven al SAE y el único nativo es RÍO LIBRE. **Bloqueo hoy**: mientras las series sigan marcadas como cortadas, el masivo de factura las rechaza y el único camino que el código deja abierto es el nativo, justo al revés de lo que quieres. No hay script de reversa y el renombre solo se puede hacer por SQL | SE REVIERTE |
| **Dirección del catálogo** (D10, regla 4) | Manda el SAE; se invierte al cortar el último cliente | Quieres invertirlo ya y sin cortar: el alta y el cambio de precio nacen en el chat, los guarda el Facturador y un aplicador los escribe en el SAE. No hay una línea de eso. Lo que sirve de molde es la cola de sincronización que ya existe | GIRA (D24) |
| **Alcance** | Tres perfiles: Balles+Jubran, EHMO Pachuca, Villahermosa | Pasa a 10 clientes en cuatro empresas, con CHANEQUES fuera. Medio hecho en datos: las series de la empresa 04 y Campeche entraron al espejo de facturas el 16-sep. Cinco de los nueve clientes activos no tienen grupo vivo. La empresa 05 queda fuera a propósito: tres clientes tienen clave en dos empresas y el masivo no sabría a cuál mandar el documento | CRECE |
| **Canal de correo** | No aparece en el documento | Hay un segundo agente en producción desde el 25-ago que lee órdenes y requisiciones del buzón de pedidos y las registra con el mismo motor del bot: escribe Master y Drive **y las manda a la ingesta del Facturador** como si fueran de WhatsApp, con identidad vacía. Dos clientes con el mismo número de folio se pisan. No está en control de versiones y el interruptor por perfil no lo apagaría | FALTABA |
| **El bot escribe remisiones** (P2) | Toda edición con vista previa y número de propuesta | Nació un vigía que compara el Master contra las remisiones cada hora y empuja en los dos sentidos con la regla «gana la más reciente». Sin propuesta y con la Etapa 0 abierta. Ya cobró: reescribió 9 remisiones de la semana 38 ya impresas y firmadas, ocho de menos y una con $408.36 de más. El Facturador respondió con un candado que congela las partidas de una remisión impresa | AL REVÉS |
| **La clave de SAE** (P6) | El espejo crea el producto con su código para cada cliente de esa empresa | El modelo cambió debajo: una sola clave por producto, la misma en todas las empresas. El backfill corrió en producción y bajó el catálogo de 2,527 códigos a 515. **Regresión silenciosa**: el depósito de precios sigue cruzando por la clave vieja por cliente, justo las filas que el backfill poda, así que P6a deja de estar cerrado | CAMBIÓ EL MODELO |
| **Reportes** (P3) | Resumen de órdenes por día, semana, cliente y punto de entrega | Llegaron reportes, pero de otra cosa y para otro lector: ventas, cartera por antigüedad y saldos por proyecto, todos calculados sobre facturas. Ninguno toca una remisión y todos quedan fuera del alcance del bot | OTRA COSA |
| **Fase 0 del bot** | Congelar el Master, commitear lo que hay, prueba automática real | Volvió a duplicarse: de 2,989 a 6,671 renglones sin commitear, con un commit en la semana. El motor de EHMO estrena más de veinte comandos nuevos, no cinco. Lo bueno: dentro de ese bulto está el arreglo del pendiente 10, el que destraba el interruptor | EMPEORÓ |
| **P1 · P4 · P5 · P9 · P10 · P12** | Por construir | Sin movimiento. La remisión sigue con una sola fecha de entrega y sin tipo de partida; no hay hoja de armado, ni cola de eventos, ni el original guardado. Los permisos de la conexión no cambiaron un byte | IGUAL |

### Qué cambia en el plan

- **La Fase 5 se reescribe al revés y la reversa se convierte en trabajo con pasos.** Ya no es
  «cortar plaza por plaza» sino «volver todos al SAE menos uno». El orden importa y hay una trampa:
  el 18-sep se sembró de nuevo una serie ZMAFAN en producción, así que antes de tocar nada hay que
  mirar esa fila y decidir si se reusa o se elimina. Después: reencender el interruptor de espejo en
  las dos series de factura, decidir el renombre (solo por SQL, la pantalla no cambia el código de
  una serie), cancelar con devolución para que las remisiones vuelvan a borrador, y revincular las
  Las listas de proyecto no hay que tocarlas: el diagnóstico del 19-sep confirmó que siguen
  vinculadas al SAE, así que la desvinculación del corte no está vigente.
- **Se registra D24, tu giro.** El Facturador pasa a ser el origen del catálogo y los precios, y un
  aplicador junto al SAE los escribe allá. Esto obliga a reescribir la regla de diseño 4: el
  Facturador no escribe pedidos ni facturas en el SAE, eso sigue siendo Excel masivo; catálogo y
  precios sí, por cola, con confirmación y bitácora. El conector sigue siendo de solo lectura; lo que
  cambia de lugar es el escritor, que hoy vive en el bot. La cola que ya mueve el espejo sirve de
  molde, así que P10 deja de ser opcional y se vuelve el camino de ida.
- **Antes de apagar el Master hay que invertir el orden de escritura.** Hoy el bot escribe la hoja
  primero y el Facturador después, y si la hoja falla de forma no pasajera la orden muere en los dos
  lados. Mientras eso siga así, dejar el Master en solo lectura no es «apagarlo»: es perder órdenes.
- **Entra el canal de correo como pieza propia.** Ponerlo en control de versiones, darle su propia
  marca de identidad y declararlo como canal de correo en la ingesta. Y falta algo que el Facturador
  no sabe hacer: resolver un cliente por su dirección de correo. Necesita su tabla de equivalencias
  por remitente o dominio, con su interruptor de apagado, igual que hoy la tienen los grupos.
- **P2 deja de estar «por construir» y pasa a «construido al revés».** La escritura llegó sin el
  contrato y el precio fue un incidente. El candado de la remisión impresa sube al plan como regla
  general: lo que el cliente firmó no lo reescribe una sincronización. Hoy ese candado solo cubre las
  partidas; las fechas y las notas siguen pasando.
- **P6 se reescribe con el modelo nuevo y P6a se reabre.** La frase «código para cada cliente de esa
  empresa» ya no describe nada, y el depósito de precios tiene que aceptar la clave nueva del
  producto antes de que el backfill siga podando la vieja. Son pocas líneas y hoy es una regresión
  que nadie ve.
- **La puerta de la Etapa 3 son siete listas, no dos.** Verificado contra producción el 19-sep:
  siguen espejadas las 5 de proyecto, la de Balles y Jubran y la de Tabasco. La empresa 04 entró al espejo de facturas, no al de precios: sus listas no
  cuentan para la puerta mientras no se les capture su vínculo, y esa alta es trabajo aparte.
- **D23 se recuenta y cambia de forma.** No son cinco comandos nacidos en el Master, son más de
  veinte, y varios no caben en el resumen ni en la hoja de armado: inventario de bodega por foto,
  pronóstico de compra, conversiones de presentación y el propio vigía. Son dominios de negocio que
  el Facturador no modela. D23 se reformula como lista cerrada: qué se conserva, qué se declara
  legítimamente del bot y a qué pieza va cada cosa.
- **Lo primero sigue siendo la Fase 0 del bot**, hoy con el doble de código en el aire que hace una
  semana, con un segundo agente que ni siquiera está en git, y con el arreglo del pendiente 10
  atrapado dentro del bulto.

### Lo que hay que decidir

- **D24**, el giro: aprobarlo, decidir dónde vive el aplicador y qué gana cuando el precio también
  cambió del lado del SAE.
- **Notas de crédito**: el Facturador no puede emitir un egreso y la empresa 04 sí las usa. O se
  quedan en el SAE hasta el corte, y entonces el espejo tiene que leerlas para no inflar la cartera,
  o entran al Facturador. Sin eso, los reportes nuevos y el estado de cuenta de la 04 mienten.
- **La empresa 05**: hace falta una regla que diga a qué empresa pertenece cada clave cuando un
  cliente tiene dos.
- **Los clientes sin canal**: Sureña, Soctones y Vida Sana no tienen grupo, y CDS y Don Pedro lo
  tienen apagado. Decidir si se les abre chat o entran solo por pantalla.
- **El vocabulario por punto de entrega**: el bot empezó a aprender claves por hospital, un nivel que
  el Facturador no modela. Antes de borrar el archivo de alias hay que decidir si eso es una columna
  nueva o si el hospital se modela como plaza.
- **D18** sigue abierta y ahora tiene dos productores de originales, no uno. **D21**, las ocho
  remisiones de Tabasco, sin tocar. **D20** se queda sin sujeto con la reversa: cerrarla como «no
  aplica hoy».
- **Dos grifos abiertos que D9 no contempló**: el agente de correo da de alta productos y precios en
  el SAE con el «sí» de un cliente en un hilo, sin lista blanca; y el espejo del catálogo tiene un
  agendador que existe pero falla en todas sus corridas, así que el aviso que detiene el masivo vale
  lo que valga su última pasada a mano.

## Revisión del 12 de septiembre

Diez días y unos 150 commits después; verificado contra el código del Facturador en main, el repo
del bot y el git. Tres cosas se adelantaron fuera del orden del plan, seis piezas no se movieron y
el bot acumuló más riesgo.

| Tema | Qué decía el plan | Qué pasó del 2 al 12 | Estado |
|---|---|---|---|
| **Corte del SAE** (Fase 5) | Después de catálogo, por cliente, con checklist y comandos por chat | Pachuca se cortó el 9-sep por serie, no por cliente: EHMO y MAFAN nativos con FEHMOHOS/FMAFAN desde folio 1 (FEHMOHOS 1 timbrada el 10-sep); el candado bajó del cliente a la serie (migr 0070, seis candados con tests); se opera en pantalla (confirmar→facturar→enviar en /remisiones). Balles/Jubran y Tabasco siguen en SAE con espejo | ADELANTADO |
| **La bandeja de órdenes** | Pantalla propia donde el operador procesa | Desapareció el 9-sep: la ingesta crea remisión directa (`OC_INGESTA_DIRECTA`), la orden con duda es fila REVISAR en /remisiones con «Procesar órdenes»; backlog drenado el 10-sep (quedaron ~43 órdenes con motivo: las filas REVISAR). La API de ingesta del bot quedó intacta, mismos estados | CAMBIÓ |
| **P6 espejo de catálogo** | Precios + productos + espejo puntual | La mitad de precios existe: listas vinculadas (migr 0065), endpoints de depósito, botón «Sincronizar SAE» (migr 0064) con expiración (PR #109) y caché en el bot (~116→~2 escrituras). productos no: renglón sin cruce se reporta, no se crea; sin espejo puntual ni reevaluación | PARCIAL |
| **Listas y D10** | El SAE manda; puerta de Etapa 3 = 7 listas iguales | El corte desvinculó las 5 listas de proyecto: esas ya las manda el Facturador. Quedan espejadas 2 (Balles/Jubran y Tabasco); la puerta baja de 7 a 2. *Corregido el 19-sep contra prod: las 7 siguen vinculadas (02:3, 02:5-9, 03:4); esa desvinculación no está vigente y D10 NO se invirtió* | SIN CAMBIO |
| **Estado de cuenta** | El comando se serviría del Facturador (P8, D12) | Excel formato SAE + semana automática + filtro por serie + crédito (11-sep); mezcla nativo y espejo. Al bot solo le falta el permiso de cobranza (`menu:facturas` no está en la clave) | CASI |
| **OC que cambia tras remisionar** | P2 avisaría | Detector con diff, insignia «OC CAMBIÓ» y cierre con nota (migr 0072); se cierra solo si el documento vuelve a coincidir | HECHO |
| **P1 · P2 · P3 · P4 · P10 · P9** | Por construir | Sin movimiento: ni fechas persistidas, ni propuestas, ni resumen, ni armado, ni eventos; `PERMISOS_CONEXION` idéntico byte por byte y `factura:espejo` sigue sin sembrar | IGUAL |
| **Fase 0 del bot** | Congelar el Master y pisar firme | No arrancó y empeoró: 317 → 2,989 renglones sin commitear en los mismos archivos de las órdenes perdidas; el main del bot quedó roto («une remisiones»: disparador commiteado, motor no); 5 comandos nuevos nacieron en el Master; secretos, `npm test` y perfil por grupo iguales. La causa raíz del pendiente 10 se encontró (fechas DD/MM contra campo `date`, 4xx sin rastro) y hay log de rechazadas, pero sin commit ni test | EMPEORÓ |

### Qué cambia en el plan

- **El corte es por plaza y serie, no por cliente.** La Fase 5 se reescribe con lo demostrado en
  Pachuca: serie nueva desde folio 1, candado por serie, operación en pantalla. Remates: verificar
  el primer timbrado de MAFAN, cuidar folios de series renombradas, resolver D21.
- **D20 resuelta de facto**: el primer corte llegó sin comandos de chat; se factura en pantalla y
  la clave del bot sigue sin poder timbrar. Cerrarla así; reevaluar el chat con el Master apagado.
- **D19 en la práctica**: el grupo se configura completo desde Equivalencias del Facturador
  (11-sep). Manda el Facturador; falta el cierre formal.
- **P6 se parte**: P6a precios (hecho) y P6b productos/espejo puntual/reevaluación (pendiente; hoy
  importa para la lista 3, Tabasco y las altas por chat).
- **Etapa 3 se acorta**: puerta = las 2 listas espejadas. *Superado el 19-sep: con la reversa vuelven
  a ser 7.* Los retiros de D9 siguen enteros por
  hacer: el bot conserva todas sus escrituras directas al SAE y sus masivos.
- **Decisión nueva D23**: 5 comandos nacieron en el Master el 11-12 sep, contra la Etapa 0 y fuera
  de D22 — `lista de compras`, `pronóstico de compra`, `nota de remisión`, `nota de armado`,
  `calendario de entregas`; «lista de compras» dejó de ser sinónimo de hoja de armado.
  Propuesta: entran a la gramática global mapeados a P3/P4, y el Master se congela de verdad,
  empezando por commitear lo que hay.
- **Lo primero sigue siendo la Fase 0 del bot**, hoy con 9× más código en el aire.

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
4. **El catálogo y los precios tienen un solo dueño** (reescrita el 19-sep por D24: el Facturador es
   el origen y un aplicador junto al SAE los escribe allá, por cola y con bitácora). Nada se da de
   baja por iniciativa del sistema; el Facturador nunca escribe PEDIDOS ni FACTURAS en SAE, que entran a Aspel
   únicamente por el Excel masivo (D9). La única escritura directa que queda es el alta de producto y
   precio desde el chat, con confirmación, como hoy.
5. **Las reglas de la casa se conservan**: folios del sistema sin ceros ni espacios; el masivo deja
   rastro y no estampa; escrituras cruzadas con confirmación; lo facturado no se toca.
6. **Quién hizo qué**: la «Nota WhatsApp» del Master se vuelve actor externo en la bitácora.

## Los comandos: una sola gramática

**Decisión del 2-sep (D22): los comandos son GLOBALES.** La misma frase funciona para Balles,
Jubran, EHMO y MAFAN, sin importar el motor ni el grupo; lo que cambia es el cliente al que apunta.
De los 46 comandos de hoy quedan **20**: los repetidos entre motores se funden en uno y los que
casi no se usan se retiran. Uso en 30 días: exacto para Balles/Jubran (bitácora del router); ≈ es
aproximado de EHMO (sin bitácora) y «≈ pocos», uso sin conteo fiable; 0 es cero medido; raya, sin
registro; «nuevo», comando que aún no existe; «bajo», unidades sueltas. La cifra suma las
variantes absorbidas cuando hay conteo. El 11-12 sep nacieron 5 comandos más, directo en el
Master: ver D23 en la revisión (propuestos para entrar por P3 y P4).

### Los 20 que se quedan

| Comando global | Absorbe | Uso 30 d | Con el Facturador | Estado |
|---|---|---|---|---|
| PDF/foto/Excel al grupo (la entrada, no es comando) | Las cuatro tuberías de hoy, incluida la del correo; acuse, alarmas y preguntas de destino no cambian | 605 + fotos | La bandeja cruza y cotiza; el original viaja con la orden (P12, con D18) | EXISTE |
| `nueva orden <cliente o ubicación> <día>` + partidas | El alta manual de EHMO, para cualquier cliente | ≈ pocos | Ingesta canal MANUAL (el modelo ya lo admite) | PARCIAL |
| `actualiza OC <folio>` + líneas | Editar/borrar/agregar renglones; agregar y quitar partidas; kilos; partida a extra o reposición; corregir el producto (recruce y `reemplaza`, aprende el alias); cambio de semana. Siete comandos en una gramática | ≈235 | Edición con vista previa y aplicar (P2 sobre P1) | FALTA |
| `bodega <folio> <fecha>` / `reparto` | La fecha de entrega para cualquier cliente | 44 | Fechas en orden y remisión (P1) + endpoint por lote (P2) | FALTA |
| `cancela la OC <folio>` | — | 12 | Descartar; el aviso de pedido/factura ya hecha sale del espejo | EXISTE |
| `une remisiones R-34-01 con R-34-02` | El código sin commitear del bot; nace global | nuevo | Operación de remisiones con vista previa (P2) | FALTA |
| `pedido por ubicación <folio>` | Parte una orden de CONALEP en una entrega por plantel, con hoja por destino y fecha de bodega; hoy solo Balles, queda global | 10 | Reparto por punto de entrega sobre la orden (P2) y hojas por destino (P4) | FALTA |
| `hoja de armado` por OC, fecha, semana, rango o entrega | Las dos variantes; FRUVE/SECOS; extras y reposiciones marcadas | ≈107 | Pivote desde remisiones y órdenes (P4), eje = fecha de bodega (P1) | FALTA |
| `precio de <producto>` (ficha) | El comando más usado | 1,296 | Catálogo y listas espejadas (P6), sin sqlcmd ni túnel; la fuente no cambia hasta que las listas espejadas sean iguales, recalculadas contra el censo (con la reversa vuelven a ser 7) | PARCIAL |
| `crea producto <nombre> en la lista de <cliente o proyecto> a <precio>` | El alta de Balles y la de EHMO, una sintaxis; absorbe `busca SAT` (la clave SAT se sugiere sola) | ≈336 | SAE con «sí» como hoy + espejo puntual (P6) + reevaluación de órdenes que lo esperaban | FALTA |
| `actualiza <precio, categoría o ficha> de <producto>` | Precio por clave, precios de una OC (regla de primera vez), categoría, campos de la ficha | 292 | Igual en SAE + espejo puntual: la lista nunca se desalinea | FALTA |
| `lista de precios de <cliente o proyecto>` | Las listas por proyecto y la de Balles/Jubran | ≈18 | PDF/Excel existen; falta el permiso (P9) | PARCIAL |
| `masivo de pedido` / `masivo de factura` | Los tres generadores de hoy; un archivo por empresa, cualquier cliente | ≈209 | Export del Facturador con folio real (P7); único camino al SAE (D9) | PARCIAL |
| `factura de la OC <folio>` | «¿ya está timbrada?» y el ejemplo de factura (si no está timbrada, contesta con el ejemplo) | ≈80 | Desde el espejo (liga factura-orden-remisión); falta lectura al alcance de la clave (P8) | PARCIAL |
| `factura <serie> <folio>` (PDF) | Los totales de la factura y el PDF del pedido | 35 | PDF del SAE mientras el SAE facture; totales del espejo | SE QUEDA |
| `resumen de órdenes` por día, semana o cliente | Las dos variantes por motor; falta facturar antes de facturado con subtotal | en 613 · ≈8 | Resumen del Facturador (P3): texto y Excel | FALTA |
| `pendientes de la OC <folio>` / `sin facturar` | El detalle de la orden: en EHMO el folio a secas contesta qué trae | 448 | Bandeja + espejo (P5, P8) | PARCIAL |
| `sin precio` / `sin clave` | Alimentan la alerta de las 7/10/13/16/18 (D14) | ≈243 | Consulta agregada de la bandeja (P5) con importe en juego | PARCIAL |
| `estado de cuenta <cliente>` | La hoja aparte de Google desaparece | 30 | Cobranza del Facturador con PDF; pagos de Balles/Jubran capturados ahí (D12) | PARCIAL |
| `impuestos de <OC o factura>` | Las 6 variantes de hoy en una forma | 67 | Sin cambio de fondo | SE QUEDA |
| `ayuda` / `manual` | Se reescriben el día que su perfil se apaga | 5 + ≈6 | Sin cambio | SE QUEDA |

Los comandos de operación interna (`grupos`, `prueba alerta`, `conectar facturador`) siguen igual
y no cuentan en los 20. Los de facturación nativa (timbrar desde el chat, registrar pago)
quedaron fuera por ahora: el corte opera en pantalla (D20 resuelta de facto) y se reevalúan
cuando el Master esté apagado.

### Los que se retiran

| Comando | Uso 30 d | Por qué se retira | Qué lo sustituye |
|---|---|---|---|
| `crear pedido SAE OC <folio>` | 37 | D9: nada se inyecta directo al SAE | El masivo de pedido con folio real; el mismo comando contesta con el archivo |
| `actualiza pedido <n>` (edita FACTP02) | bajo | D9: escritura directa a pedidos | Editar la remisión y regenerar el masivo |
| Prefactura (comando quitado el 3-ago; código vivo) | 0 | D9: insertaba facturas directas | Se borra en la Etapa 3 con sus disparadores |
| `actualiza/borra/agrega renglón N OC F` | <10 | Repite `actualiza OC` | La gramática global de edición |
| `actualizar rq <folio>` + precios | 0 | Sin un solo uso en la ventana | Corrección de requisiciones desde la pantalla del cotizador |
| `cotiza · CLAVE · Desc` | 8 (todas 4-5 ago) | Casi sin uso | Pantalla del cotizador; si se extraña, se reactiva contra el Facturador |
| `amarra OC <x> con la factura <y>` | ≈ pocos | El espejo liga solo (OC y folio interno) | Automático; el caso raro se corrige en `su_pedido` de la remisión |
| `master facturas` | — | Reconstruía una hoja que ya no existirá | Espejo cada 30 min + resumen del espejo |
| `revisión` y `conciliación` a mano | ≈2 | Ya corren solas (6 h y viernes 17) | Avisan al grupo interno cuando hay algo que ver |
| `busca SAT <qué es>` | — | El alta sugiere la clave SAT sola | Dentro de `crea producto` |
| `ignora productos <claves>` | 25 | Sí se usa, pero es configuración, no conversación | La pantalla del Facturador, que debe existir antes del retiro |
| `cierra el periodo con carpeta X` | 3 | D13: el periodo es un filtro | Excel del periodo a Drive, a petición |

Los tres primeros retiros, los de D9, van en la Etapa 3; los demás, cuando su función global
quede en vivo. Ninguno se borra antes de que su reemplazo funcione: el comando viejo contesta durante dos
semanas diciendo cuál es el nuevo.

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
  nuevo/cambiado → lista correspondiente, hoy las 2 aún espejadas (3 → Balles/Jubran, Tabasco →
  EHMO VH); las 5 de proyecto se desvincularon con el corte del 9-sep.
  Nunca desactiva ni borra; duplicados se desempatan por partidas facturadas; si la lista SAE no
  existe o difiere de lo facturado, toma el precio de la última factura no cancelada y avisa. Espejo puntual por
  clave tras cada comando que escribe el SAE, y reevaluación de órdenes pendientes. Sentido único
  hasta el corte: el SAE manda y el Facturador lo sigue (D10). **Estado 12-sep**: la mitad de
  precios ya está en prod (listas vinculadas migr 0065 + botón Sincronizar SAE migr 0064); falta
  productos, espejo puntual y reevaluación.
- **P7 Masivos con el folio real del SAE**: el bot deja su .xls; usa el export del Facturador y le
  pasa el siguiente folio real del SAE (`_sae_sig_folio_pedido` existe; hoy solo la llama el motor
  EHMO, y solo contra la empresa 02; `cmd_massivo` de Balles no la usa). Cierra el
  pendiente 12 y D1. Con D9 es el ÚNICO camino de pedidos y facturas hacia el SAE: `cmd_pedido_sae`
  (INSERT directo en FACTP02) se retira y el bot deja de escribir FACTP02/FOLIOSF02.
- **P8 Lectura del espejo para el bot**: factura de la OC, estado SAT, totales, pendientes. El PDF
  sigue saliendo del SAE. **Estado 12-sep**: la clave ya puede pedir y reportar el sync del espejo;
  para estado de cuenta le falta el permiso de cobranza (`menu:facturas`).
- **P9 Permisos de conexión acotados**: leer espejo, leer listas (y cotizar, por si `cotiza` se
  reactiva), editar partidas con
  propuesta, espejar catálogo, folio sugerido. Nunca `producto:gestionar` ni `cliente:gestionar`.
  Detalle: `factura:espejo` no está sembrado en el catálogo de permisos (solo en código).
- **P10 Bitácora de eventos para el bot** (cola que el bot consulta cada minuto): orden convertida
  en remisión desde la pantalla, partida sin cruzar, factura llegada por el espejo. Opcional para
  apagar el Master; necesario para que los grupos se enteren de lo hecho en pantalla.
- **P11 Datos previos**: pendiente 7 (equivalencia VH + reabrir 35 órdenes), 8 (183 links Drive), 10
  (causa raíz de las 14 órdenes perdidas), 9b (código del cliente en el cotizador). El interruptor solo
  se mueve con los cuatro en cero. **Estado 12-sep**: la causa del 10 ya se encontró (fechas DD/MM
  contra campo `date`, 4xx sin rastro) + log de rechazadas en el bot; falta commit y test.
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
   *Comprobación*: las listas espejadas iguales renglón por renglón, SAE = Facturador. La puerta se
   verifica contra el censo: hoy son 7 (02:3, 02:5-9, 03:4), confirmado contra prod el 19-sep, y las de
   la empresa 04 entran cuando se les capture su vínculo. Producto creado por el bot aparece antes
   del siguiente pedido; 2 masivos de pedido y 2 de factura importados sin error.
4. **Apagar el Master por perfil** (3 semanas de calendario). Balles+Jubran → EHMO Pachuca →
   Villahermosa; hoja de solo lectura; conciliación Master↔bandeja apagada con cada perfil; Drive
   sigue recibiendo los originales como respaldo (con D18 aprobada, el Facturador ya guarda su copia).
   *Comprobación*: una semana por perfil sin abrir la hoja para resolver nada.
5. **Limpieza**. Borrar código de Sheets y archivos locales; exportar cada Master a Drive como
   archivo muerto; actualizar manuales. El corte del SAE sigue su plan por plaza (Fase 5 del agente).
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
- **28-ago**: espejo SAE, el corte del SAE (hoy por plaza y serie, ver la revisión del 12-sep),
  outbox, y «el bot escribe el Master primero» para la ENTRADA de órdenes mientras el perfil siga
  encendido.
- **De esta propuesta**: las seis reglas, P1-P12, D9-D18 y la gramática global de D22.

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
los cuatro motores (incluido el del correo), `probar.py` con casos dorados, `eval_router.py` y pruebas de contrato de
`facturador_client.py` contra el tenant demo de la BD local (:5434), nunca contra los dos tenants
de producción. Menos de cinco minutos.

| Fase | Va con | Criterio de salida | Bot | Facturador |
|---|---|---|---|---|
| 0 · Piso firme | Etapa 0 | git limpio; 0 secretos en `sheets_config.json`; `npm test` verde; una propuesta de prueba sobrevive al reinicio del bot y del backend; cada grupo activo con perfil declarado | 5–6 d | 2–3 d |
| 1 · Paridad de lectura | Etapa 1 | 5 días hábiles con los reportes idénticos en los 3 perfiles | 5–6 d | 6–8 d |
| 2 · Paridad de escritura | Etapa 2 · D18 solo para P12 | conciliación en cero 5 días con el Master como copia; 0 cruces del bot; toda orden nueva con su original (archivo o link); abandonados 236 → <20/mes | 6–8 d | 7–9 d |
| 3 · Catálogo, precios y masivos | Etapa 3 | las listas espejadas iguales (con la reversa, 7); 0 INSERT/UPDATE del bot en FACTP02, PAR_FACTP02, FACTF02, PAR_FACTF02, CUEN_M02, FOLIOSF02, TBLCONTROL02; motores sin lecturas SQL del SAE | 5–6 d | 5–7 d |
| 4 · Apagar el Master y borrar | Etapas 4 y 5 | una semana por perfil sin abrir la hoja; cero referencias a gspread/Sheets en los cuatro motores (incluido el del correo) | 6–8 d + 3 sem | 1–2 d |
| 5 · Corte del SAE por plaza | Migración 28-ago · Fase 3 | por plaza: 2 periodos a factura timbrada sin intervención; 0 CFDI duplicados. Pachuca ya está dentro (9-sep) | 3–4 d | 2–3 d |
| 6 · Plataforma | sin calendario | alta de tenant/cliente solo con filas; conciliación de dinero en cero 2 semanas | 5–7 d | 8–12 d |

**Fase 0 · Piso firme** (semana 1, con la Etapa 0)
- Causa raíz del pendiente 10 con test que lo reproduzca; después commit o rama del trabajo sin
  commitear: 317 renglones el 1-sep, 2,989 el 12 y 6,671 el 19, en los mismos archivos de las
  órdenes perdidas. El arreglo del pendiente 10 ya está escrito ahí dentro: falta commitearlo con su prueba.
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
- El comando `cotiza` se retira (D22). Cada comando llama `registrarAccion` (existe, nadie la
  llama): es la medida de las fases siguientes.
- Arranca D12 y con ella el fix de fechas del Facturador (el «hoy» de cobranza sigue en UTC).
- La lista blanca del router híbrido solo crece con lecturas que ya sirve el Facturador.

**Fase 2 · Paridad de escritura** (semanas 3–5, con la Etapa 2; solo P12 depende de D18)
- Toda edición va por P2 sobre P1 (la gramática global de D22: actualiza OC, bodega/reparto,
  cancelar, kilos, agrega, quita, extra o reposición, corregir producto, semana, une remisiones).
  El cruce del bot deja de correr. El
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

**Fase 3 · Catálogo, precios y masivos** (semanas 5–6, con la Etapa 3; se cierra antes del corte de
las plazas que quedan; Pachuca ya cortó)
- P6 con la regla del dueño: entre dos claves vivas manda lo último facturado; si la lista SAE no
  existe o difiere de lo facturado, el espejo toma el precio de la última factura no cancelada
  (PAR_FACTF) y avisa. `PROYECTO_LISTA_SAE` deja el bot y se vuelve equivalencia del Facturador.
- **Se retiran las escrituras directas de pedidos y facturas (D9)**: `cmd_pedido_sae` (:8990),
  `_actualizar_pedido_sae_core` (UPDATE FACTP02/PAR_FACTP02), `cmd_prefactura_sae` (:8463, sigue en
  el código aunque el comando se quitó el 03-ago) y el generador propio de masivos (`_xls_massivo`,
  `cmd_massivo*`). El comando devuelve el export del Facturador con el folio sugerido de `_sae_sig_folio_pedido`.
- Las lecturas SQL del SAE de los dos motores pasan a `conector_sae/` (solo lectura, clave de
  conexión tipo SAE separada de la del bot). Las escrituras que quedan (producto, precio, categoría,
  cambios de ficha) viven en un módulo pequeño del bot, con «sí» y espejo puntual.
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
- Líneas de los cuatro motores (incluido el del correo) medidas antes y después, publicadas en ESTADO.

**Fase 5 · Corte del SAE por plaza** (el corte de Pachuca del 9-sep SE ESTÁ REVIRTIENDO — ver la
revisión del 19-sep; queda Río Libre como único nativo;
para las plazas que quedan depende de la Fase 3 de su perfil, NO del apagado del Master)
- Solo si D20 se reabre con el Master apagado: «factura la OC», «registra pago» y «estado de
  cuenta» como vista previa, «sí» y aplicar, con las guardas del 21-ago (candado $0, anti-duplicado
  por OC, método de pago explícito, verificar factura existente antes de crear). Mientras, el corte
  opera en pantalla.
- Antes del corte de la siguiente plaza, y como deuda de Pachuca (cortó sin ellos): runbook de
  recuperación (Baileys sin sesión, Facturador caído a medio timbrado, clave revocada), REP F3 y
  zona horaria confirmados en prod, temas E–K del manual como casos dorados, respaldo de la Mac Mini.
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
  `grupos_whatsapp` del Facturador). Manda el Facturador; en la práctica ya ocurrió (Equivalencias,
  11-sep). Falta el cierre formal.
- **D20** Para una plaza ya cortada, ¿el bot aplica con «sí» timbrado, pago y alta de producto, o
  solo prepara la propuesta y se aplica en pantalla? Resuelta de facto el 9-sep: el primer corte
  llegó sin comandos de chat y se factura en pantalla. Se reevalúa con el Master apagado.
- **D21** Las 8 remisiones de Tabasco que divergen del Excel subido al SAE (pendiente 11):
  regenerar los masivos o corregir en el SAE a mano.

### Riesgos propios del agente

- El trabajo sin commitear del bot (317 renglones el 1-sep, 6,671 el 19) vive en los mismos archivos
  que las 14 órdenes perdidas y contiene el arreglo del pendiente 10; la Fase 0 lo commitea con su
  prueba antes de tocar nada.
- Refactor sin red sobre >27,000 líneas en tres archivos: nunca reescribir, solo redirigir detrás de
  `master.activo` y borrar al final; `npm test` como puerta.
- Precio equivocado antes de la paridad del espejo (ZMAFAN 168): la ficha no cambia de fuente hasta
  que las listas espejadas sean iguales, recalculadas contra el censo del momento.
- Si el Facturador cae tras el cambio de fuente, se callan las alertas: outbox y aviso al grupo
  interno cubren también las lecturas programadas.
- Apagar un perfil sin conciliación deja pérdidas silenciosas (pendiente 10): la conciliación
  sustituta corre antes de mover el interruptor.
- La visión de fotos sigue en el bot (D17) y sigue frágil (60 s por foto vs 25 s de SIGTERM): libro,
  reintentos y watchdog se conservan hasta el corte.
- La Mac Mini es un solo punto físico de falla: respaldo y runbook antes del corte de la siguiente
  plaza; Pachuca ya corre sin ellos.

## Decisiones del dueño

El dueño resolvió D9 a D17 y D22 el 2-sep-2026. Abiertas: D18 (OneDrive), D21 (Tabasco) y D23 (comandos nacidos en el Master); D19 y D20 resueltas en la práctica, falta el cierre formal.

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
| D22 | ¿Comandos por motor o unificados? | **Decidido**: una sola gramática global para todos los clientes; quedan 20 comandos y los poco usados o repetidos se retiran (ver «Los comandos: una sola gramática»). | DECIDIDO |
| D18 | La orden original vive en Drive (y en la carpeta OneDrive de la Mac); el Facturador solo guarda el link. ¿Se sigue así? | **Propuesta**: el Facturador guarda el archivo (P12); OneDrive/Drive de respaldo durante la transición, opcionales después. | ABIERTA |
| D19 | Tres registros del mismo grupo (config del bot, tabla `grupos` del agente, `grupos_whatsapp` del Facturador). | **Propuesta**: manda el Facturador. En la práctica ya ocurrió: desde el 11-sep el grupo se configura completo desde Equivalencias. Falta el cierre formal. | POR CERRAR |
| D20 | Cliente ya cortado: ¿el bot aplica con «sí» timbrado, pago y alta de producto, o solo prepara la propuesta? P9 excluye CFDI nativo. | **Resuelta de facto el 9-sep**: el primer corte llegó sin comandos de chat; se factura en pantalla y la clave del bot sigue sin poder timbrar. Cerrarla así; reevaluar con el Master apagado. | POR CERRAR |
| D21 | 8 remisiones de Tabasco divergen del Excel subido al SAE (pendiente 11). | **Propuesta**: regenerar esos masivos desde el Facturador antes de la Fase 3. | ABIERTA |
| D24 | ¿El Facturador pasa a ser el origen del catálogo y los precios, y un aplicador los escribe en el SAE? | **Giro del dueño (12-sep)**: sí. Deroga la regla 4 en su promesa de «nunca escribe en SAE» (pedidos y facturas siguen solo por masivo, D9) y convierte P10 en camino de ida. Falta aprobarlo formalmente y decidir dónde vive el aplicador. | ABIERTA |
| D23 | 5 comandos nacieron en el Master el 11-12 sep, fuera de D22: `lista de compras`, `pronóstico de compra`, `nota de remisión`, `nota de armado`, `calendario de entregas`. | **Propuesta**: entran a la gramática global por P3/P4, y el Master se congela de verdad: lo primero es commitear los 2,989 renglones en el aire. | ABIERTA |

## Riesgos

| Riesgo | Cobertura |
|---|---|
| Una orden se pierde al apagar el Master | **OJO, la cobertura es circular** (hallazgo del 19-sep): `facturador_conciliar.py` define «perdida» leyendo EL MASTER, así que la red que justifica el apagado se apaga con él. Hace falta un detector que no dependa de la hoja. Outbox + conciliación existen; interruptor solo con pendiente 10 cerrado y 5 días en cero; Master solo lectura 2 semanas |
| El cruce del Facturador difiere del bot sin que nadie lo note | Etapa 1 compara reportes 5 días; diferencias se corrigen en datos |
| Precio corregido en SAE y el Facturador cobra el viejo | Espejo puntual tras cada comando + pasada de 30 min; lo facturado no se reprecia |
| La clave del bot gana permisos amplios | Permisos acotados (P9); catálogo se escribe por el espejo, no por endpoints de gestión |
| El equipo sigue capturando en la hoja | Solo lectura desde el apagado; ayuda y manuales cambian ese día |
| Doble captura tras el corte (nativa aquí + a mano en SAE): el masivo de pedidos de Pachuca no está bloqueado por código | Candado del export de pedidos para series cortadas + conciliación del espejo; mientras, disciplina declarada en el plan del corte |
| El main del bot no arranca limpio («une remisiones» a medias) | Primer paso de la Fase 0: commitear o mover a rama los 2,989 renglones, hoy |
| docs/ESTADO.md congelado en el 2-sep (no sabe del corte ni de la bandeja fusionada) | Reescribirlo en el próximo cierre; esta revisión sirve de puente |
| El pedido del SAE deja de crearse al instante desde el chat (37/mes) | El mismo comando devuelve el masivo de pedido con folio real y avisa que se importa en Aspel; el rastro queda en la remisión |
| Se mezclan los dos tenants de prod | Clave y espejo apuntan al tenant que opera; el de pruebas no recibe nada |

## Lo que no cambia

Grupos y roles (el bot enruta por grupo y rol; los perfiles se declaran una vez en la Fase 0 —
Balles/Jubran → `balles`, EHMO Pachuca → `ehmo` — sin cambiar lo que cada grupo puede pedir);
los comandos por chat con la gramática global de D22 y la confirmación sí/no; acuses, alarmas y PDFs; el SAE factura e importa los
masivos de pedido y de factura hasta el corte de cada plaza (Pachuca ya cortó; sigue para Balles,
Jubran y Tabasco); los originales siguen llegando a Drive
durante la transición, con su link en cada orden (y, con D18 aprobada, el archivo en el
Facturador); alertas y horarios.
