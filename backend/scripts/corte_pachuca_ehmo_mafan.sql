-- Corte de Pachuca: EHMO y MAFAN pasan a timbrar NATIVO en el Facturador.
-- (decisión del dueño, 9-sep-2026 — solo EHMO y MAFAN; el resto sigue en espejo)
--
-- Qué hace, en el tenant vivo (cristian-gerardo-zarate-orozco):
--   1. Renombra las series de factura ZEHMOHOS→FEHMOHOS y ZMAFAN→FMAFAN y les
--      apaga espejo_sae: quedan cortadas del SAE y el candado de facturas
--      nativas las deja emitir. Exige folio_actual = 0 (decisión del dueño:
--      la primera nativa sale con folio 1) y ABORTA si no lo está.
--   2. Renombra las series de remisión RZEHMOHOS→RFEHMOHOS y RZMAFAN→RFMAFAN.
--      El contador NO se toca: la numeración continúa (RFEHMOHOS119, RFMAFAN13).
--      Las remisiones históricas conservan su folio_interno RZ* tal cual.
--   3. Desvincula las listas SAE5..SAE9 del espejo de precios (sae_empresa y
--      sae_lista a NULL): desde el corte los precios de proyecto se mantienen
--      EN el Facturador y el SAE ya no los pisa cada 30 min. La lista 3
--      (Balles/Jubran) y la EHMOVH0826 (Tabasco, empresa 03) siguen espejadas.
--
-- Qué NO toca: clientes.espejo_sae (queda ENCENDIDO en EHMO y MAFAN para que
-- el espejo siga actualizando lo histórico del SAE — cancelaciones y saldos — y
-- lo de Tabasco), ZHGO/RZHGO, ZEHMOVH/RZEHMOVH, vínculos y asignaciones (van
-- por id de serie, el renombre no los rompe).
--
-- Correr DESPUÉS de desplegar la migración 0070 (backfill de series.espejo_sae).
-- Es un DO $$ … $$: cualquier verificación que falle hace RAISE y NADA queda
-- aplicado. Al final imprime las filas resultantes con RAISE NOTICE.

DO $$
DECLARE
    t_id uuid;
    n int;
    fila record;
BEGIN
    SELECT id INTO t_id FROM tenants WHERE slug = 'cristian-gerardo-zarate-orozco';
    IF t_id IS NULL THEN
        RAISE EXCEPTION 'No existe el tenant cristian-gerardo-zarate-orozco';
    END IF;

    -- Premisa del dueño: las facturas nuevas arrancan en folio 1.
    SELECT count(*) INTO n FROM series
     WHERE tenant_id = t_id AND tipo_documento = 'FACTURA'
       AND codigo IN ('ZEHMOHOS', 'ZMAFAN') AND folio_actual = 0 AND espejo_sae;
    IF n <> 2 THEN
        RAISE EXCEPTION 'Se esperaban ZEHMOHOS y ZMAFAN (FACTURA) con folio_actual=0 y espejo_sae=true; hay % — revisa antes de cortar', n;
    END IF;

    UPDATE series s
       SET codigo = v.nuevo,
           espejo_sae = false
      FROM (VALUES ('ZEHMOHOS', 'FEHMOHOS'), ('ZMAFAN', 'FMAFAN')) AS v(viejo, nuevo)
     WHERE s.tenant_id = t_id AND s.tipo_documento = 'FACTURA' AND s.codigo = v.viejo;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 2 THEN
        RAISE EXCEPTION 'Renombre de series de FACTURA tocó % filas (se esperaban 2)', n;
    END IF;

    UPDATE series s
       SET codigo = v.nuevo
      FROM (VALUES ('RZEHMOHOS', 'RFEHMOHOS'), ('RZMAFAN', 'RFMAFAN')) AS v(viejo, nuevo)
     WHERE s.tenant_id = t_id AND s.tipo_documento = 'REMISION' AND s.codigo = v.viejo;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 2 THEN
        RAISE EXCEPTION 'Renombre de series de REMISION tocó % filas (se esperaban 2)', n;
    END IF;

    UPDATE listas_precios
       SET sae_empresa = NULL, sae_lista = NULL
     WHERE tenant_id = t_id AND codigo IN ('SAE5', 'SAE6', 'SAE7', 'SAE8', 'SAE9')
       AND deleted_at IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 5 THEN
        RAISE EXCEPTION 'Desvinculación de listas tocó % filas (se esperaban 5: SAE5..SAE9)', n;
    END IF;

    FOR fila IN
        SELECT codigo, tipo_documento, espejo_sae, folio_actual
          FROM series
         WHERE tenant_id = t_id
           AND codigo IN ('FEHMOHOS', 'FMAFAN', 'RFEHMOHOS', 'RFMAFAN')
         ORDER BY tipo_documento, codigo
    LOOP
        RAISE NOTICE 'serie % (%) espejo_sae=% folio_actual=%',
            fila.codigo, fila.tipo_documento, fila.espejo_sae, fila.folio_actual;
    END LOOP;
    RAISE NOTICE 'Corte aplicado: 2 series de factura, 2 de remisión, 5 listas desvinculadas';
END $$;
