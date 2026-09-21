# Las 23 claves de SAE en disputa

> Dos o más productos del Facturador reclaman el mismo artículo de SAE, así que
> el backfill no puede asignar `productos.clave_sae` y los deja sin clave. Es el
> bloqueo real de la **meta 5** («el producto se crea en el Facturador y también
> en el SAE»): automatizar altas sobre este catálogo propagaría los duplicados al
> sistema fiscal.
>
> Datos de producción, 21-sep-2026. «Vendido» = suma de `lineas_remision.importe`
> en remisiones no canceladas. La regla del dueño era **gana el viejo**; donde el
> dinero la contradice se dice, porque ahí la regla no debería decidir sola.
>
> **Nada de esto se ha aplicado.** Es una tabla para decidir.

---

## Grupo A — Obvias: el gemelo del 15-sep no ha vendido nada

Doce claves donde el original vende y su gemelo —creado el 15-sep por la
importación masiva— tiene **$0**. Aquí «gana el viejo» y el dinero dicen lo mismo.
Se pueden resolver de corrido.

| Clave SAE | Se queda (vendido) | Se apaga (vendido) |
|---|---|---|
| `FRIJOLNEGROKG` | FRIJOL NEGRO 25 KG · **$41,777** | FRIJOL NEGRO KG · $0 |
| `BROCOLIPZA` | BROCOLI · **$20,124** | BROCOLI PZA · $0 |
| `CANELAENTERAKG` | CANELA ENTERA · **$7,449** | CANELA ENTERA KG · $0 · CANELA ENTERA (CANELAENTERAKG) · $0 |
| `PIMIENTANEGRAMKG` | PIMIENTA NEGRA MOLIDA 460 GR · **$6,218** | PIMIENTA NEGRA MOLIDA · $0 |
| `CILANTROMJ` | CILANTRO MANOJO DE 1 KG · **$4,889** | CILANTRO MANOJO · $0 |
| `MARIASGALLETASPZ` | GALLETA MARIA CAJA CON 720 GRS · **$3,515** | GALLETAS MARIAS CAJA CON 720 GRS · $95 · GALLETAS MARIAS · $0 |
| `COLBCAPZA` | COL · **$2,746** | COL BLANCA PZA · $0 |
| `ELOTEPZA` | ELOTE · **$2,568** | ELOTE PZA · $0 |
| `MERMELADAFRESAPZ` | MERMELADA DE FRESA 450 G · **$1,102** | …FRASCO 450 GRS MC CORMICK · $276 · MERMELADA DE FRESA · $0 |
| `GERMENALFALFAKG` | GERMEN DE ALFALFA PAQ.500GR · **$956** | GERMEN DE ALFALFA · $0 |
| `CHILECASCABELKG` | CHILE SECO CASCABEL · **$285** | CHILE CASCABEL · $0 · CHILE SECO CASCABEL KG · $0 |
| `CALABAZACASTILKG` | CALABAZA DE CASTILLA · **$49,642** | CALABAZA CRIOLLA · $657 · CALABAZA DE CASTILLA KG · $152 |

---

## Grupo B — El dinero contradice «gana el viejo»

Cinco claves donde el producto **más nuevo es el que factura**. Aplicar la regla
tal cual apagaría el que la operación está usando. Estas necesitan tu decisión
una por una.

| Clave SAE | El viejo | El nuevo | Cuál vende |
|---|---|---|---|
| `AZUC-AZUC-3260` | AZUCAR ZULKA 1 KG (27-ago) · $85 | AZÚCAR BOLSA DE UN KILO SIMILAR ZULKA (1-sep) · **$6,272** | **el nuevo, 74×** |
| `PAN-CERE-314` | PAN MOLIDO CLASICO BIMBO (27-ago) · $223 | PAN MOLIDO CLÁSICO, PAQUETE 210 GRS (1-sep) · **$6,270** | **el nuevo, 28×** |
| `ATUNENAGUAPZ` | ATÚN EN LATA (28-ago) · **$12,612** | LATAS DE ATU DE AGUA (11-sep) · $300 | el viejo — pero el nuevo es una **errata** de un PDF |
| `PITAHAYAKG` | PITAHAYAKG (2-sep) · $1,254 | PITAHAYA KG (15-sep) · **$2,195** | el nuevo |
| `CHIAKG` | CHIA A GRANEL (27-ago) · $437 | CHIA KG (28-ago) · **$780** | el nuevo, por poco |

**Observación:** en los dos primeros el nombre largo —el que viene de la orden del
cliente— es el que factura. Sugiere que la operación cruza por el texto del
documento, no por el nombre corto del catálogo.

---

## Grupo C — Probablemente NO son duplicados

Seis claves donde los dos productos parecen **artículos distintos**, no el mismo
escrito de dos formas. Aquí la decisión no es cuál gana, sino **declarar que son
diferentes** y darle a cada uno su propia clave de SAE (o dejar uno sin ella).

| Clave SAE | Producto 1 | Producto 2 | Por qué parecen distintos |
|---|---|---|---|
| `ESPINACAPZA` | ESPINACA BABY ORG PAQ 454GR · $23,036 | ESPINACA PZA · $37 | Baby orgánica embolsada contra espinaca suelta |
| `HIERBABUENAKG` | TE DE YERBABUENA NATURAL (1000 G) · $3,995 | HIERBABUENA · $583 · HIERBABUENA MANOJO · $562 | **Té** contra **hierba fresca**: no son lo mismo |
| `OREGANOENTEROKG` | ORÉGANO EN HOJA (60 G) · $2,497 | OREGANO · $227 | Empaquetado de 60 g contra granel |
| `FRIJOLNEGROKG` | FRIJOL NEGRO 25 KG [PIEZA] | FRIJOL NEGRO KG [KILO] | **Costal** contra **kilo**: unidad distinta |
| `CHAYOTESINESPIKG` | CHAYOTE · $237,410 | CHAYOTE SIN ESPINAS · $10,864 | Con y sin espinas son dos productos en la práctica |
| `CILANTROKG` | CILANTRO CRIOLLO · $39,624 | CILANTRO MANOJO DE 1 KG · $4,889 | Criollo contra manojo |

> **Ojo con `CILANTRO MANOJO DE 1 KG`**: aparece disputado bajo **dos** claves a la
> vez, `CILANTROKG` y `CILANTROMJ`. Resolver una sin mirar la otra deja el
> problema a medias.

---

## Cómo se aplica una vez decidido

Existe la herramienta: `backend/scripts/resolver_duplicados_clave.py`, que ya hace
las tres escrituras por caso —el ganador recibe la clave, el gemelo se desactiva,
y las líneas en BORRADOR se repuntan— y **respeta lo facturado**. Hoy resuelve
sola una de las 23 (`CALABAZACASTIKG`); su regla está acotada a la tanda del
15-sep, así que los grupos B y C quedan fuera por diseño, no por descuido.

Para el grupo A, la vía corta es ampliar su criterio: «gana el que vendió, si el
otro tiene $0». Para el B y el C no hay atajo: son criterio de negocio.

**Lo que NO hay que hacer todavía:** construir el aplicador de altas al SAE. Con
24 claves en disputa, automatizar altas mete los duplicados en el sistema fiscal,
y la regla de la casa pide confirmación explícita para toda escritura cruzada.
