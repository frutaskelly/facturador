# Onboarding de los clientes que quedan en SAE

**Fecha:** 29-ago-2026 · **Alcance temporal:** solo de **junio 2026 a hoy** (decisión del dueño).
**Excluido:** PROCESADORA Y DISTRIBUIDORA LOS CHANEQUES (`02:1`, 5 facturas por $734).

Este plan se apoya en lo que ya funciona con Balles y Jubran: no inventa un
proceso nuevo, replica el que ya está probado en producción.

---

## Lo que se encontró en SAE (verificado, no supuesto)

**La empresa 01 no existe.** Hay cuatro: `02`, `03`, `04` y `05`.

**Las cuatro facturan con el MISMO RFC: `ZAOC830517RF9`** — el mismo del Facturador,
con CSD vigente hasta julio-2029 y ambiente en producción. Son cuatro empresas
*administrativas* de Aspel, no cuatro emisores fiscales: **el corte no necesita
multiemisor ni trámites nuevos ante el SAT**.

### Clientes con facturación desde junio-2026

| Empresa | Cliente | RFC | Facturas | Importe | Series | Estado |
|---|---|---|---|---|---|---|
| 02 | GRUPO OPERADOR DE ALIMENTOS EHMO | GOA180712SF5 | 567 | $7.40 M | ZEHMOHOS, ZEHMOFAC | ✅ migrado |
| 02 | OPERADORA BALLES VEGA DE HIDALGO | OBV191007BS1 | 284 | $1.96 M | ZHGO | ✅ migrado |
| 02 | DISTRIBUIDORA … JUBRAN | DAP250922PY2 | 88 | $0.33 M | ZHGO | ✅ migrado |
| 02 | MEDIOS DE ALIMENTACIÓN MAFAN | MCM170118UJ6 | 79 | $5.97 M | ZMAFAN | ✅ migrado |
| 02 | ~~LOS CHANEQUES~~ | PDC110704EI1 | 5 | $734 | — | ⛔ excluido |
| 03 | GRUPO OPERADOR DE ALIMENTOS EHMO | GOA180712SF5 | 718 | $5.86 M | ZEHMOVH | ✅ migrado |
| **04** | **GRUPO OPERADOR DE ALIMENTOS EHMO** | GOA180712SF5 | **714** | **$9.46 M** | **ZEHMOTG, ZDIF** | ⚠️ falta esta operación |
| **04** | **GRUPO SUREÑA** | GSU110118GL0 | **474** | **$1.72 M** | **ZSUR** | ❌ nuevo |
| **04** | **BODEGA DE DON PEDRO** | BDP250605HH6 | **124** | **$0.73 M** | **ZBPT** | ❌ nuevo |
| **04** | **COORDINACIÓN DE DISTRIBUCIONES Y SERVICIOS** | CDS001113K23 | **68** | **$1.09 M** | **ZCH5C, MIN5C** | ❌ nuevo |
| **04** | **CASA DE SOCTONES** | CSO150706NX6 | **12** | $47 K | **ZCS** | ❌ nuevo |
| **05** | COORDINACIÓN DE DISTRIBUCIONES Y SERVICIOS | CDS001113K23 | 13 | $0.25 M | CH5C, *(una sin serie)* | ❌ nuevo |

**A onboardear: 4 razones sociales nuevas + una operación más de EHMO.**
Suman 1,405 facturas y ~$13.3 M desde junio.

### Datos fiscales y condiciones (de SAE, listos para sembrar)

| Cliente | Domicilio fiscal | Crédito | Método | Uso CFDI | Forma |
|---|---|---|---|---|---|
| EHMO (04) | San Agustín de las Juntas, **Oaxaca** · CP 71260 | 30 d | PPD | G01 | 99 |
| GRUPO SUREÑA | Berriozábal, **Chiapas** · CP 29130 | 15 d | PPD | G01 | 99 |
| BODEGA DE DON PEDRO | Centro, **Tabasco** · CP 86108 | 15 d | PPD | G01 | 99 |
| COORDINACIÓN (CODISEL) | Tuxtla Gutiérrez, **Chiapas** · CP 29059 | 15/30 d | PPD | G01 | 99 / 03 |
| CASA DE SOCTONES | Tuxtla Gutiérrez, **Chiapas** · CP 29030 | 15 d | **PUE** | **G03** | **03** |

⚠️ CASA DE SOCTONES es la única de contado (PUE) y con uso G03: no entra al flujo de
crédito ni genera complementos de pago.
⚠️ COORDINACIÓN aparece en **dos empresas** (04 y 05) con condiciones distintas —
igual que EHMO, necesita mapeo *empresa por sucursal*.
⚠️ Ninguno tiene lista de precios en SAE (`LISTA_PREC` vacío): **sus precios viven en
cada factura**, así que su lista del Facturador se construye del histórico.

### Catálogo: mucho más ligero de lo esperado

189 claves distintas entre los cinco. **135 (72%) ya las conoce el Facturador** por
Balles/Jubran/EHMO/MAFAN — solo **54 son nuevas**.

| Cliente | Productos distintos | Partidas (jun→hoy) |
|---|---|---|
| EHMO (04) | 131 | 16,167 |
| GRUPO SUREÑA | 48 | 2,655 |
| BODEGA DE DON PEDRO | 67 | 1,771 |
| COORDINACIÓN (04) | 76 | 1,403 |
| COORDINACIÓN (05) | 57 | 334 |
| CASA DE SOCTONES | 71 | 237 |

### El intake ya existe: está apagado

Los grupos de WhatsApp de estos clientes **ya están configurados en el bot**, con
`activo: false`:

- `Verdura Don Pedro Chiapas` → BODEGA DE DON PEDRO
- `FyV Cristian Codisel Chiapas` + `Fac codisel Interno Chiapas` → COORDINACIÓN
- `Frutas y verduras Chiapas Ehmo` → EHMO Chiapas
- `Cd Carmen verdura Ehmo Campeche` → EHMO Campeche
- `FAC REST ABRE CAMPO CHIAPAS INTERNO` → *(por confirmar si es GRUPO SUREÑA)*

**No hay que construir canales: hay que encenderlos y darles perfil.** Eso convierte
la Etapa 1 del onboarding en configuración, no en desarrollo.

---

## El molde: cómo quedó Balles (lo que hay que replicar)

Un cliente "onboardeado" es exactamente esto:

| Pieza | Balles | Para qué sirve |
|---|---|---|
| `clientes` | RFC, régimen 601, uso G01, PPD/99, 90 días, `espejo_sae=true` | El receptor del CFDI y sus defaults |
| `series` factura + remisión | ZHGO / RZHGO | Foliado propio y del espejo |
| `sucursales` | 1 (Pachuca) | Resuelve serie, almacén y **empresa SAE** |
| `cliente_externos` · SAE | `02:7` | El número de cliente en el masivo |
| `cliente_externos` · RFC | OBV191007BS1 | Cruza la OC con el cliente |
| `cliente_externos` · NOMBRE | 2 variantes | Cruce por razón social impresa |
| `cliente_externos` · WHATSAPP | 3 grupos | De qué grupo llega su pedido |
| `cliente_externos` · UBICACION | 5 puntos | Punto de entrega → sucursal |
| `producto_clientes` | 470 claves | La CVE_ART que SAE exige en el masivo |
| `lista_asignaciones` | 1 lista | Los precios que se cobran |

**El orden importa**: sin la clave SAE no hay masivo; sin `producto_clientes` el masivo
se detiene; sin sucursal no se resuelve la empresa; sin lista, las remisiones salen sin
precio.

---

## Plan por etapas

### Etapa A — Sembrar (sin tocar la operación)

Por cada cliente nuevo, en este orden:

1. **Cliente** con sus datos fiscales de SAE y `espejo_sae = true` (el candado que
   impide timbrarle desde aquí mientras SAE lo factura).
2. **Series** de SAE como series del Facturador: ZSUR, ZBPT, ZCH5C, MIN5C, ZCS,
   ZEHMOTG, ZDIF, CH5C — cada una con su `folio_actual` en el último usado, y su
   pareja de remisión (RZSUR, RZBPT…).
3. **Sucursal** por operación, y `cliente_externos` SAE `<empresa>:<clave>` **colgado
   de esa sucursal** — es lo que hace que el masivo salga a la empresa correcta.
   Obligatorio para EHMO (02/03/04) y COORDINACIÓN (04/05).
4. **Equivalencias** RFC y NOMBRE (del PDF real de su OC, si existe).
5. **Catálogo**: `producto_clientes` con las claves de SAE. 135 ya existen como
   producto — se vinculan; las 54 nuevas se dan de alta con el wizard de importación.
6. **Lista de precios** construida del histórico facturado (último precio por producto
   desde junio), una por cliente.

**Verificación de la etapa:** el preview del export a SAE de una remisión de prueba
sale sin errores para cada cliente.

### Etapa B — Espejo del histórico (junio → hoy)

Extender el conector a las empresas **04 y 05** (hoy solo cubre 02 y 03: es una línea
en `SERIES_POR_EMPRESA` de `facturador_espejo.py`) y correr el backfill desde
`2026-06-01`. Resultado esperado: ~1,400 facturas más reflejadas, con sus saldos.

**Verificación:** `facturador_conciliar.py sae` en verde para las 8 series nuevas.

### Etapa C — Encender el intake

Prender los grupos de WhatsApp con su perfil correcto y sembrar sus equivalencias
`WHATSAPP` y `UBICACION`. A partir de aquí sus pedidos llegan solos a la bandeja.

**Verificación:** la conciliación diaria Master ↔ bandeja en verde para los perfiles
nuevos (ya corre sola cada 6 h).

### Etapa D — Operar en paralelo

Igual que Balles hoy: las remisiones nacen en la bandeja, el masivo se genera desde el
Facturador, SAE sigue facturando. Con el comparador de masivos como puerta antes de
importar.

### Etapa E — Corte, cliente por cliente

Cuando un cliente cumpla su semana estable: fijar `folio_actual`, cuadrar saldos,
apagar `espejo_sae` desde la pantalla del cliente. Ya no requiere trabajo de código.

---

## Riesgos concretos

1. **COORDINACIÓN en dos empresas con condiciones distintas** (15 vs 30 días, forma 99
   vs 03): si no se mapea empresa-por-sucursal, sus masivos salen a la empresa
   equivocada. Es el mismo error que ya bloqueó a EHMO y que ya tiene solución.
2. **La factura sin serie en la empresa 05** (2 documentos): el espejo necesita serie
   para su identidad. Hay que decidir cómo se reflejan o excluirlas.
3. **Precios que viven en la factura, no en una lista**: la lista reconstruida del
   histórico es una foto del último precio. Hay que revisarla con el dueño antes de
   que gobierne remisiones nuevas.
4. **EHMO ya tiene tres operaciones** (Pachuca, Villahermosa, Oaxaca/Tuxtla). Cada una
   con su sucursal, su serie y su empresa SAE; el modelo lo soporta pero hay que
   sembrarlo completo o el export se detiene por ambigüedad.
