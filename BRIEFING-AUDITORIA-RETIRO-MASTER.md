# Briefing: auditoría de Smart Supply y el Facturador

Este documento es un traspaso. Lo escribe una sesión de Claude que lleva semanas trabajando estos
sistemas, para otra sesión que no los conoce. Al final del documento vas a poder auditarlos sin
haber estado en ninguna conversación previa.

Hay una propuesta sobre la mesa: **retirar el «Master Órdenes», una hoja de Google Sheets que hoy
es el corazón operativo del negocio**. La propuesta está escrita, revisada tres veces y parcialmente
ejecutada. Tu encargo no es ejecutarla. Es auditarla.

---

## Tu encargo

**Dictamina si retirar el Master Órdenes es la mejor acción, y si el estado objetivo que pide el
dueño es alcanzable con lo que hoy existe.** No aceptes la propuesta como premisa. Puede ser que la
respuesta correcta sea hacerla tal cual, hacerla distinto, hacer otra cosa primero, o no hacerla.

### El estado objetivo, en palabras del dueño (19 de septiembre de 2026)

> «Todo debe funcionar de la misma manera. Únicamente se quita el Master y la sincronización entre
> el Master y el Facturador, y todo se escribe directamente en el Facturador en tiempo real desde
> WhatsApp. WhatsApp siempre llama al Facturador.»

Esto es **más simple que lo que dice hoy el plan**. El plan contempla etapas con escritura al Master
primero y un periodo de sombra en el que los dos conviven. El dueño quiere el Master y su
sincronización fuera, y la escritura directa. Esa diferencia es una de las cosas que debes evaluar:
¿el camino por etapas del plan sigue teniendo sentido, o el objetivo del dueño pide otro diseño?

### Las preguntas que tienes que responder

1. **¿Qué hace hoy el Master que el Facturador no hace?** El plan afirma que son tres papeles:
   almacén de órdenes, estado de negocio y reportes. Verifícalo tú contra el código y contra la
   base. Enumera lo que se perdería el día que se apague, pieza por pieza, y di cuál tiene sustituto
   construido, cuál tiene sustituto diseñado y cuál no tiene nada.
2. **¿La escritura directa y en tiempo real es alcanzable hoy?** Hoy el bot escribe el Master
   primero y el Facturador después. Si el Facturador no responde, ¿qué pasa con esa orden? Audita
   el camino completo de una orden desde que llega el PDF hasta que existe la remisión, y di qué
   falta para que el Facturador pueda ser el único destino sin perder órdenes.
3. **¿Cuántos canales de entrada hay realmente?** El plan dice que son cuatro. Verifícalo. Cada
   canal que no conozcas es un canal que el apagado rompe en silencio.
4. **¿La propuesta es la mejor acción?** Enumera y compara al menos estas alternativas, con
   evidencia: retirarlo como propone el plan; dejarlo de solo lectura indefinidamente; no retirarlo
   y arreglar la sincronización; retirarlo pero después de otra pieza; o un diseño distinto al del
   plan. Di cuál recomiendas y por qué, con lo que costaría y lo que arriesgaría cada una.
5. **¿Qué afirmaciones del plan están mal fundadas?** El plan ya tuvo dos errores de ese tipo, los
   dos por dar por cierto lo que decía un script en vez de mirar la base. Busca más.
6. **¿Qué debería pasar primero?** Si hay trabajo que ordena el resto, nómbralo.

### Cómo auditarlo

Usa la herramienta Workflow. Estas auditorías no caben en una sola pasada y el trabajo se paraleliza
bien. Un armado que funciona, y que puedes cambiar:

- **Un flujo de mapeo.** Un agente por subsistema, en paralelo: el Facturador, el bot de WhatsApp,
  el agente de correo, la integración con el SAE, y el camino de datos de una orden de punta a
  punta. Cada uno devuelve hechos con ruta y línea.
- **Un flujo de comparación de alternativas.** Un agente por alternativa, cada uno obligado a
  defenderla con evidencia, y jueces independientes que las puntúen contra criterios fijos.
- **Un flujo de verificación adversarial.** Por cada hallazgo o afirmación que sostengas, dos o tres
  agentes que intenten refutarla. Lo que no sobreviva, fuera. Este paso no es opcional: las dos
  correcciones que ya sufrió el plan habrían salido aquí.
- **Un crítico de completitud al final.** Qué quedó sin mirar, qué afirmación quedó sin verificar.

Verifica **contra el código y contra la base de datos**, nunca contra lo que diga el plan o un
script del repositorio. Un script que existe no es un script que se aplicó.

### Qué entregar

1. **El dictamen**, en una página: si la propuesta es la mejor acción, cuál es tu recomendación y
   qué la sostiene.
2. **La comparación de alternativas**, con criterios explícitos.
3. **El inventario de lo que el Master hace y quién lo sustituiría**, con su estado real.
4. **Los errores del plan** que hayas encontrado, con evidencia.
5. **El orden de trabajo** que recomiendas, si recomiendas seguir.
6. **Lo que no pudiste verificar** y qué haría falta para verificarlo.

Publícalo como documento y déjalo también en el repositorio del Facturador. Es lo único que viaja.

### Criterios

Juzga cada alternativa con estos, en este orden: que no se pierda una sola orden; que nadie tenga
que capturar dos veces; que lo facturado no se toque nunca; que el equipo pueda seguir operando
desde el teléfono igual que hoy; que el trabajo sea proporcional al problema que resuelve; y que
cada paso sea reversible o tenga su vuelta atrás escrita.

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
