# Meta 4 — Censo y plan de las lecturas directas al SAE

> «WhatsApp se comunica únicamente con el Facturador» — meta 4 del objetivo de
> referencia (ver `AUDITORIA-retiro-master.md` §0).
>
> Censo del 20-sep-2026, hecho con cuatro lentes en paralelo sobre el árbol de
> trabajo y un verificador que rehízo los conteos. Cada fila lleva su
> `archivo:línea`. **Este documento es el plan de trabajo; la auditoría es el
> dictamen.**
>
> Corrección de partida: son **130** lecturas, no las 131 que citaba la
> auditoría. La 131ª era la llamada recursiva de reintento de `_sae_query`
> (`sheets_push.py:3121`), no una lectura distinta.

---

No hay tercer censo que corregir de fondo, pero sí una cifra: **son 130 lecturas, no 131**.

`grep -c '_sae_query(' sheets_push.py` = 96, menos la definición (`sheets_push.py:3089`) **y menos la llamada recursiva de reintento de la propia función** (`sheets_push.py:3121`, `return _sae_query(sql, timeout=timeout, _reintento=True)`) = **94 sitios reales**. El censo D restó solo la definición y se quedó en 95; ese 95º no es una lectura distinta, es la misma consulta reintentada. `grep -c '_sae_query_ehmo(' ehmo_pedidos.py` = 37 menos la definición (`ehmo_pedidos.py:4641`) = **36**. Universo = **130**. Los censos A (94 filas, una por sitio) y B (36 filas) están exactos; verifiqué las nueve familias de A y las cinco de B contra los números de línea del grep, una por una.

Segunda corrección: las 3 lecturas de emisor que el censo B cuelga de «vía sheets_push» no pasan por `_sae_query`. Están en `pedido_pdf.py:200, 204, 223` y usan un tercer helper propio, `pedido_pdf.py:46 def sae_query(sql, timeout=60)`. Quedan fuera de las 130 y fuera de este lente.

| familia | lecturas | REDIRIGIBLE HOY (endpoint existe + el bot tiene el permiso) | FALTA ENDPOINT | FALTA PERMISO | SE VA CON EL RETIRO | sin destino |
|---|---|---|---|---|---|---|
| **catálogo de producto** | 32 (A 20 + B 12) | **28** — `productos.py:194` GET /productos, `:219` /similares, `:282` POST /match, `:1815` /{id} con `menu:productos` (`productos.py:102 _READ = "menu:productos"`); SAT por `sat.py:44/57/68`, mismo permiso. La llave de cruce ya existe: `clave_sae` en `schemas/producto.py:34`. Wrappers YA cableados: `facturador_client.py:543 productos_match`, `:564 ficha_producto`, `:741 producto_id_por_clave` | ninguno duro. Dos comodidades ausentes, derivables paginando GET /productos: «unidades en uso y cuántos productos las usan» (`sheets_push.py:8429`) | **1** — `sheets_push.py:5462` `_catalogo_lineas` (CLIN02). Equivalente: `categorias.py:30`, que pide `menu:productos.categorias` (`categorias.py:25`). `AuthContext.has` (`core/rbac.py:114`) compara cadena exacta: `menu:productos` NO lo cubre | **3** — `sheets_push.py:9110, 9117, 9118` (`_actualizar_pedido_sae_core`) | 0 |
| **facturas** | 31 (A 21 + B 10) | **6** — `sheets_push.py:3352, 3390, 9826` y `ehmo_pedidos.py:5298, 10025, 10031`: solo quieren folio + status por OC. Se contestan con `oc_recibidas.py:437/:936` (`menu:oc`, `oc_recibidas.py:86`) + `remisiones.py:271/:611` (`menu:remisiones`, `remisiones.py:93`) + `facturas.py:830` /espejo/resumen (`factura:espejo`), ya envuelto en `facturador_client.py:471 espejo_resumen` | ninguno | **17** — todo lo que pide UUID, fechas de timbre/cancelación, partidas, cliente o domicilio: `sheets_push.py:3319, 3660, 4113, 4208, 4792, 6362, 8634, 8676, 10790, 10805, 10850` y `ehmo_pedidos.py:5765, 5783, 6348, 6391, 10154, 10165`. Todo cuelga de `menu:facturas` (`facturas.py:95 _READ`), que no está en `PERMISOS_CONEXION` (`core/rbac.py:66-76`). `/espejo/resumen` solo devuelve folio, total, estado y saldo (`facturas.py:849-862`) | **8** — prefactura `sheets_push.py:8814, 8843, 8844, 8981, 8989, 8990`; `cmd_master_facturas` `:4280`; bloque `amarra` `ehmo_pedidos.py:11621` | 0 |
| **impuestos** | 16 (A 14 + B 2) | **14** — cero endpoints nuevos: `iva_tasa`, `ieps_tasa`, `objeto_imp`, `clave_sat`, `unidad_sat` ya viajan dentro de `ProductoOut` (`schemas/producto.py:31-38`), que llega con el mismo `menu:productos` del paso de catálogo | ninguno | **2** — `sheets_push.py:8439` `_esquemas_catalogo` (IMPU02 completo) y `ehmo_pedidos.py:11343` (IMPU del esquema elegido). Equivalente: `esquemas_impuesto.py:47/:95`, que pide `menu:esquemas_impuesto` (`esquemas_impuesto.py:27`). `esquema_impuesto_id` llega como UUID opaco | 0 | 0 |
| **pedidos** | 13 (A 12 + B 1) | 0 | ninguno | ninguno | **8** — `sheets_push.py:9019, 9026` (`_diff_pedido_existente`), `:9080, 9093, 9237` (`_actualizar_pedido_sae_core`), `:9449, 9462, 9464` (`cmd_pedido_sae`) | **5** — `sheets_push.py:3420, 3498, 4123, 4768` y `ehmo_pedidos.py:10041` preguntan por el PEDIDO DE SAE (`FACTP02`), documento que el Facturador no tiene. Se pueden apuntar a `remisiones.py:271` (`menu:remisiones` ✓), pero eso **cambia la pregunta**, no la fuente |
| **precios** | 10 (A 3 + B 7) | **3** — `sheets_push.py:5389, 5514` y `ehmo_pedidos.py:6231` vía `precios.py:381` /cotizar + `:227` /contexto con `menu:cotizador` (`precios.py:40 _READ_COTIZAR`), ya cableado en `facturador_client.py:622`. Con fan-out: /cotizar es por `producto_id`, no por lote | ninguno duro. No hay endpoint de una llamada para «las 7 listas de UNA clave» (`sheets_push.py:8223`, `ehmo_pedidos.py:11066`); habría que pedir lista por lista | **7** — `sheets_push.py:8223` y `ehmo_pedidos.py:2037, 4488, 4768, 8458, 11066, 11478` piden la LISTA entera. Equivalente: `listas_precios.py:374` /{id}/precios, con `menu:listas_precios` (`listas_precios.py:67`). La descarga (`listas_precios.py:628/:649`) además exige `cliente_scope` en `_ctx_descarga` (`listas_precios.py:604-618`): el bot trae `menu:cotizador` pero no `cliente_scope`, así que hoy recibe 403 | 0 | 0 |
| **folios y consecutivos** | 7 | 0 | ninguno | **5** — `sheets_push.py:7344, 7366, 7372, 7390, 7395`. Equivalente: `series.py:74/:95/:111/:120`, con `menu:series` (`series.py:30`) | **2** — `sheets_push.py:8828` (prefactura), `:9376` (`cmd_pedido_sae`) | 0 |
| **clientes** | 4 | **3** — `sheets_push.py:2016, 3238, 11023` vía `clientes.py:55` GET /clientes (q sobre legal_name/rfc/codigo) y `:388` /{id}, con `menu:clientes` (`clientes.py:50`). `ClienteOut` trae `rfc`, `legal_name`, `codigo`, `domicilio_fiscal`, `regimen_fiscal`, `uso_cfdi_default`, `forma_pago_default`, `metodo_pago_default`, `dias_credito` (`schemas/cliente.py:15-30, 69`) | ninguno | ninguno | **1** — `sheets_push.py:8824` (prefactura) | 0 |
| **cobranza** | 2 | 0 (resquicio parcial: `ClienteOut.saldo_actual`, `schemas/cliente.py:73`, llega con `menu:clientes` — da el saldo, no el detalle por factura) | ninguno | **1** — `sheets_push.py:6765` `cmd_estado_cuenta`. Equivalente: `cobranza.py:386` /estado-cuenta/{cliente_id}, con `menu:facturas` (`cobranza.py:41`) | **1** — `sheets_push.py:8991` (CUEN_M02, verificación de prefactura) | 0 |
| **ping / sonda de vida** | 15 (A 11 + B 4) | **12** — `conexiones.py:204` /conexiones/probar, con `menu:remisiones` ✓, YA envuelto en `facturador_client.py:139`. Sitios: `sheets_push.py:7824, 8209, 8292, 8389, 8522, 8593, 8632, 8673` y `ehmo_pedidos.py:5688, 10143, 11239, 11326` | ninguno | ninguno | **3** — `sheets_push.py:8797, 9072, 9311` | 0 |
| **TOTAL** | **130** | **66** | **0** | **33** | **26** | **5** |

---

## Orden de trabajo por relación valor/esfuerzo

El orden sale del cociente lecturas-que-caen ÷ endpoints-que-hay-que-cablear, con el wrapper existente como divisor real: `facturador_client.py` (1088 líneas) ya tiene `productos_match` (:543), `ficha_producto` (:564), `producto_id_por_clave` (:741), `espejo_resumen` (:471), `buscar_oc` (:411), `listar_ocs` (:440), `listar_remisiones` (:951), `remision_detalle` (:529). Lo que NO tiene: ni una sola llamada a `/clientes`, `/sat` ni `/listas-precios/{id}/precios`.

1. **Pings — 12 lecturas, 0 endpoints nuevos, 0 wrappers nuevos.** Es sustituir `if _sae_query("SELECT 1") is None:` por la llamada que ya existe en `facturador_client.py:139`. Doce sitios, una línea cada uno. Ratio insuperable. **Salvedad**: mientras el bot siga escribiendo en SAE por `_sae_exec_sql` y `_sae_exec_ehmo` (`ehmo_pedidos.py:10958`), el ping a SAE sigue midiendo lo que de verdad va a fallar; cámbialo cuando caigan las escrituras, no antes.
2. **Catálogo — 28 lecturas, 3 endpoints, los 3 ya cableados.** 28 de 130 (22 %) contra código que ya corre en producción para `cmd_ver_producto`. Es extender un patrón probado, no inventarlo.
3. **Impuestos — 14 lecturas, 0 endpoints adicionales.** Van de regalo con el paso 2: `iva_tasa` e `ieps_tasa` viajan dentro del mismo `ProductoOut` que el paso 2 ya trae a memoria. Hacer 2 sin cobrar 3 sería tirar 14 lecturas.
4. **Clientes — 3 lecturas, 1 endpoint, wrapper nuevo.** Poco volumen, pero `ClienteOut` trae de un golpe todo lo que `cmd_prefactura_sae` iba a buscar a `FACTF02` y `CLIE02` por separado (`sheets_push.py:8814` y `:8824`). Barato y desbloquea el PDF (`sheets_push.py:11023`).
5. **Precios — 3 lecturas, endpoint ya cableado.** Poco volumen porque las 7 grandes están tras un permiso, no tras un endpoint.
6. **Facturas OC↔folio — 6 lecturas, 3 endpoints combinados.** Último de los redirigibles: más piezas por lectura y semántica delicada (ver riesgos).

**Pasos 1 a 3 = 54 de las 66 redirigibles (82 %) tocando cuatro endpoints, tres de ellos ya cableados.** Los pasos 4 a 6 aportan las 12 restantes y cuestan más código que los tres primeros juntos.

## Cuántas se redirigen HOY sin construir nada

**66 de 130** (51 %). Sumadas a las **26 que se van solas con los retiros** (verificadas: 10 de `cmd_prefactura_sae`, 7 de `cmd_pedido_sae` + `_diff_pedido_existente`, 7 de `_actualizar_pedido_sae_core`, 1 de `cmd_master_facturas`, 1 del bloque `amarra`), son **92 de 130 — 71 % — sin un solo endpoint nuevo en el Facturador**.

Quedan **33 bloqueadas por permiso** y **5 sin destino**.

**Las 3 piezas que más desbloquean**, todas de permiso, ninguna de backend:

1. **Acceso de lectura a facturas — 18 lecturas** (17 de facturas + 1 de cobranza), el bloque más grande que queda. Añadir `menu:facturas` a `PERMISOS_CONEXION` (`core/rbac.py:66-76`) es el camino corto, pero es ancho: abre el CFDI nativo completo, PDF, XML y los 10 endpoints de cobranza. La alternativa quirúrgica es engordar el espejo bajo el permiso que el bot YA tiene: `facturas.py:830` hoy devuelve solo `folio, total, estado, saldo` (`facturas.py:849-862`); agregarle UUID, fechas de timbre y cancelación, observación y cliente cubre las mismas 18 sin regalar el CFDI nativo.
2. **`menu:listas_precios` — 7 lecturas de precios**, y es la incoherencia más visible del árbol: las 7 listas se declaran desvinculadas de SAE, pero `ehmo_pedidos.py:2037, 4488, 4768, 8458, 11066, 11478` y `sheets_push.py:8223` siguen leyendo `PRECIO_X_PROD{emp}` en el disco. De paso, `listas_precios.py:180` /espejo/vinculadas filtra por `sae_empresa`/`sae_lista` no nulos: con las listas ya desvinculadas es un endpoint vivo sin datos que servir. Y el 403 de las descargas (`listas_precios.py:604-618`) no es de permiso sino del candado `cliente_scope`: son tres líneas.
3. **`menu:series` — 5 lecturas de folios**, y es el prerrequisito estructural del resto. Mientras SAE folie, el bot tiene que preguntarle a SAE.

## Riesgos: qué lecturas tocan la respuesta al cliente

**Cambian lo que el cliente lee en el chat — cambiar de fuente aquí puede cobrar mal:**

- **Precios (10 lecturas).** El precio que el chat dice es el que se cobra. El propio código documenta el incidente: `facturador_client.py:568-574` explica que el masivo fija el precio del DOCUMENTO y la lista de SAE se queda con el viejo, «el incidente ZMAFAN 168». Sitios: `sheets_push.py:5389, 5514, 8223` y `ehmo_pedidos.py:2037, 4488, 4768, 6231, 8458, 11066, 11478`.
- **Impuestos que entran al precio con IVA (6).** No son reportes: alteran el número. `sheets_push.py:892` (IVA de partidas resueltas), `:6347` (subtotal/IVA/total del massivo), `:8330` (PRECIOCIMP de `cmd_precio_sae`), `:8564` (recálculo de PRECIOCIMP al cambiar precio), `:11009` (IEPS/IVA del PDF de factura) y `ehmo_pedidos.py:11343` (precio con IVA del alta).
- **Folios (7).** El riesgo más caro de los cuatro censos. `series.py:120` se llama `folio-sugerido`: **sugiere**, no reserva. Hoy `sheets_push.py:7366/7372` y `:7390/7395` toman el MÁXIMO entre `MAX(CVE_DOC)+1` y `ULT_DOC+1` de `FOLIOSF02` precisamente porque quien folía es SAE. Mover esta lectura antes de que el Facturador sea el que folia produce documentos duplicados o huecos en la numeración fiscal. Esta familia va al final, no por esfuerzo sino por consecuencia.
- **Folio y UUID que se le contestan al cliente (5).** `sheets_push.py:3660, 8634, 8676` y `ehmo_pedidos.py:10154, 10165` devuelven UUID y fechas de cancelación. `/espejo/resumen` no trae UUID: contestar desde ahí hoy sería contestar de menos, no de más.
- **Existencias: CERO.** No hay una sola lectura de existencias en las 130. `inventario.py:81/:142/:193/:214` está construido pero bajo `menu:inventario`, fuera de los 8 permisos. No es un riesgo de la meta 4; es un vacío que ningún comando del bot pisa hoy.

**Solo diagnóstico interno — cambiar de fuente no llega al cliente (52 lecturas):** los 15 pings y sondas (`ehmo_pedidos.py:5688` es literalmente una sonda de vida para no reportar «claves inexistentes» cuando lo caído es el túnel); los reportes al operador `sheets_push.py:3183, 3207, 8429, 8439, 4421, 4567, 4597, 4699, 4771, 4798, 4826, 4902`; los cruces internos `sheets_push.py:3498, 4113, 4123, 5493` y `ehmo_pedidos.py:5765, 5783, 6348, 6391, 10288, 10305`; y los «¿esta clave ya existe?» previos a un alta, `ehmo_pedidos.py:11139, 11143, 11194, 11198, 11245, 11340`. Aquí una discrepancia entre SAE y Facturador se ve en un reporte, no en una factura: es el terreno seguro para estrenar cada redirección antes de llevarla a lo que el cliente lee.