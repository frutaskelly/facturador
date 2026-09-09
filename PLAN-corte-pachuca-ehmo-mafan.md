# Corte de Pachuca: EHMO y MAFAN timbran nativo en el Facturador

Decisiones del dueño (9-sep-2026):

- **Solo EHMO y MAFAN se cortan.** Sus facturas de Pachuca (hoy SAE 10, empresa 02,
  series ZEHMOHOS y ZMAFAN) pasan a timbrarse nativas en el Facturador — igual que ya
  opera Río Libre (serie RIO). Emite **la empresa actual** (ZAOC830517RF9); no hay
  empresa emisora nueva ni CSD nuevo.
- **EHMO Tabasco se queda en SAE empresa 03** (ZEHMOVH) por el momento. Balles/Jubran
  (ZHGO) y todo lo demás siguen en espejo, sin cambio.
- **Series nuevas**: ZEHMOHOS→**FEHMOHOS** y ZMAFAN→**FMAFAN** (facturas, **folio
  arranca en 1**); RZEHMOHOS→**RFEHMOHOS** y RZMAFAN→**RFMAFAN** (remisiones, **la
  numeración continúa**: RFEHMOHOS119, RFMAFAN13).
- **Las listas SAE5–SAE9 se quedan en el Facturador** y desde el corte los precios se
  mantienen aquí (se desvinculan del espejo para que el SAE no los pise).
- **Las vistas de EHMO y MAFAN siguen mostrando lo histórico del SAE junto con lo
  nuevo** para llevar control: por eso el espejo histórico NO se apaga.

## Cómo se resolvió en código (esta rama)

El candado anti doble-CFDI era **por cliente** (`clientes.espejo_sae`), pero el corte
es **por plaza/serie**: EHMO factura nativo en Pachuca y sigue espejado en Tabasco —
el mismo cliente en ambos lados. Apagar el flag rompía el espejo de Tabasco y el de
lo histórico (la cancelación de ZMAFAN 167 sigue en curso); dejarlo prendido
bloqueaba lo nativo.

- **Migración `0070_serie_espejo_sae`**: columna `series.espejo_sae`. Backfill sin
  listas fijas: serie de FACTURA con facturas `origen='ESPEJO_SAE'` de su tenant y
  código → espejo (marca ZHGO, ZEHMOHOS, ZEHMOVH, ZMAFAN; deja RIO apagada).
- **El candado baja del cliente a la serie** (`_rechazar_venta_en_espejo`,
  `backend/app/api/v1/facturas.py`): un cliente espejado factura nativo **solo** si
  la venta resuelve una serie con `espejo_sae` apagado; sin serie, o con una serie
  espejada (aun forzándola con override), sigue el 409. En `desde-remisiones` la
  serie se resuelve ANTES de reservar stock (consulta pura; el folio se consume
  después), así el 409 sale temprano y sin efectos.
- **`clientes.espejo_sae` queda ENCENDIDO en EHMO y MAFAN**: el espejo sigue
  actualizando lo histórico (cancelaciones, saldos) y lo de Tabasco, y el bot los
  sigue viendo como clientes compartidos. Las vistas mezclan histórico + nativo
  porque todo vive en las mismas tablas del mismo cliente.
- **Gate multiemisor en el REP** (`timbrar_recibo`, `cobranza.py`): tenía el hueco de
  timbrar sin el gate de "empresa lista" que sí tenía `timbrar_factura` — y la
  cobranza PPD de EHMO/MAFAN ahora vivirá aquí. El gate quedó como helper compartido
  (`exigir_listo_para_facturar`) y corre ANTES de tomar locks.
- **Cierres de una revisión adversarial de 6 agentes** (todos con test o candado):
  - Una serie espejada no emite folios nativos para NADIE (ni con override, ni vía
    `sustituir`): sus folios los numera SAE.
  - Para un cliente espejado, la venta debe declarar su plaza: `desde-remisiones`
    exige que el lote comparta UNA sucursal (o serie explícita) y la `directa` exige
    elegir la serie explícitamente — sin eso, la cascada caería a la serie del
    cliente (la nativa) aunque la venta fuera de la plaza que sigue en SAE.
  - El **export masivo de FACTURA rechaza** remisiones cuya serie ya está cortada
    (antes era "disciplina del operador"; ya es candado).
  - El **cruce del espejo no ampara** remisiones cuya serie resuelta ya es nativa
    (una espejo huérfana ya no puede estamparles `factura_sae` y dejarlas
    infacturables).
  - El **espejo no siembra reflejos NUEVOS** en una serie cortada (las históricas
    renombradas siguen actualizándose: cancelaciones y saldos).
  - `series.espejo_sae` es visible y editable por API (`SerieOut`/`SerieUpdate`):
    una serie creada a futuro para una plaza espejada se marca desde la app, sin SQL.

## El corte (día D — falta ejecutarlo)

1. Fusionar esta rama a `main` (PR), `git pull` en el checkout principal parado en
   `main`, y `./deploy.sh` (aplica la 0070). Nunca desplegar desde la rama.
2. Correr `backend/scripts/corte_pachuca_ehmo_mafan.sql` contra prod (transaccional,
   con SELECTs de verificación): renombra las 4 series, apaga `espejo_sae` en las de
   factura y desvincula las listas SAE5–SAE9 del espejo de precios.
3. Verificar: facturar una remisión RFEHMOHOS → sale FEHMOHOS folio 1 y timbra; el
   botón «Sincronizar SAE» sigue reportando (ZHGO/ZEHMOVH siguen llegando).

Nada del bot cambia para el corte: la emisión nativa es invisible para él, el espejo
de precios se apaga solo para las listas desvinculadas (el Facturador declara cuáles
espejar vía `/listas-precios/espejo/vinculadas`), y las series Z que quedan en
`SERIES_POR_EMPRESA` siguen espejando lo histórico y Tabasco.

## Operación post-corte (disciplina, no código)

- **No correr más los masivos de Pachuca** (pedidos/facturas de EHMO/MAFAN hacia SAE
  empresa 02): la factura ya nace aquí. El export de FACTURA del Facturador ya lo
  rechaza solo (serie cortada) y el espejo rechaza reflejos nuevos en esas series; el
  masivo de PEDIDOS y las capturas directas EN SAE siguen siendo disciplina. El
  masivo de Balles/Jubran y el de Tabasco siguen igual. Riesgo residual: dos capturas
  paralelas de la misma venta (una aquí y una en SAE bajo otra serie) siguen siendo
  posibles — eso no lo ve ningún candado.
- **No quitar la serie del vínculo EHMO×Tabasco** (ZEHMOVH): es lo que mantiene a
  Tabasco del lado espejado del candado.
- La cobranza de lo nativo (PPD) se lleva en `/cobranza` con complementos de pago del
  Facturador; la de lo histórico sigue llegando por el espejo de saldos del SAE.

## Pendientes que deja este corte

1. **Ejecutar el script del corte** (día D, con el dueño).
2. **Bot (repo SmartSupply, otra sesión)**: retirar los comandos de masivo de Pachuca
   cuando el corte sea definitivo; más adelante, sacar ZEHMOHOS/ZMAFAN de
   `SERIES_POR_EMPRESA` cuando lo histórico deje de moverse (hoy deben seguir:
   cancelaciones y saldos vivos).
3. Cuando Tabasco corte también, repetir la receta: renombrar ZEHMOVH/RZEHMOVH y
   apagar su `espejo_sae` — el código ya lo soporta (y `espejo_sae` ya se puede
   apagar por PATCH /series sin SQL).
4. **No bajar `folio_actual` de las series renombradas**: el piso anti-duplicados
   (`_max_folio_emitido`) busca por el código ACTUAL, y justo después del renombre
   lo histórico (RZEHMOHOS…) no cuenta para RFEHMOHOS. No es un camino que la
   operación normal toque.
