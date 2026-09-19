-- Reversa del corte de Pachuca: EHMO y MAFAN vuelven a facturar EN SAE.
-- (decisión del dueño, 12-sep-2026 — deshace corte_pachuca_ehmo_mafan.sql del 9-sep;
--  el ÚNICO cliente nativo del Facturador queda RÍO LIBRE, serie RIO)
--
-- ============================ CÓMO SE USA ============================
-- 1. Córrelo tal cual: arranca en MODO DIAGNÓSTICO. Imprime el inventario y lo que
--    haría, y al final lanza un error a propósito para que NADA quede aplicado.
-- 2. Lee los NOTICE. Si el diagnóstico pasa limpio, cambia `v_solo_diagnostico`
--    a false y vuelve a correrlo. Es un DO $$ … $$: o se aplica todo, o nada.
--
-- ============================ QUÉ HACE ============================
--   1. Reenciende `series.espejo_sae` en las series de FACTURA (FEHMOHOS, FMAFAN):
--      vuelven a ser espejo del SAE. Esto DESBLOQUEA el masivo, que hoy las rechaza
--      con «su serie ya está cortada del SAE», y vuelve a prohibir emitir nativo.
--   2. Renombra FEHMOHOS→ZEHMOHOS y FMAFAN→ZMAFAN. El renombre NO se puede hacer
--      por la pantalla: `SerieUpdate` no expone `codigo`. Por eso este script.
--   3. Resuelve la colisión del 18-sep: al sembrar el mapa de series de los reportes
--      (PR #173) se dio de alta una serie con el código viejo porque la original
--      estaba renombrada. Si esa fila está SIN USAR, el script repunta sus
--      referencias a la fila original y la borra. Si tiene folios, ABORTA.
--   4. Opcional (`v_renombrar_remisiones`): RFEHMOHOS→RZEHMOHOS y RFMAFAN→RZMAFAN.
--      Estas nunca tuvieron `espejo_sae`; el corte solo las renombró y el contador
--      siguió corriendo, así que volver al nombre viejo no genera folios repetidos.
--   5. Sube `folio_actual` de cada serie de factura al último folio REALMENTE
--      emitido con ese código (incluye los del espejo). Nunca lo baja.
--
-- ============================ QUÉ *NO* HACE ============================
--   · NO cancela CFDI. Las facturas nativas FEHMOHOS/FMAFAN las timbró el
--     Facturador: se cancelan DESDE LA APLICACIÓN, que es la que habla con el PAC,
--     y con motivo distinto de «01» salvo que exista una sustituta timbrada aquí.
--     Cancelar libera las remisiones, que es lo que las devuelve al masivo.
--     ESTE SCRIPT ABORTA si encuentra facturas nativas TIMBRADAS vivas.
--   · NO toca `clientes.espejo_sae` (sigue encendido en EHMO y MAFAN, como debe).
--   · NO revincula las listas SAE5..SAE9. El corte las desvinculó y desde el 9-sep
--     las manda el Facturador; revincularlas haría que la siguiente pasada del
--     espejo PISE con los precios del SAE, y va en contra del giro D24. El script
--     solo REPORTA su estado. Si decides revincular, el mapeo se recupera del
--     propio código de la lista (SAE5..SAE9 = CVE_PRECIO de la empresa 02).
--   · NO toca ZHGO/RZHGO (Balles y Jubran), ZEHMOVH/RZEHMOVH (Tabasco) ni RIO.
--
-- ============================ ORDEN Y POR QUÉ ============================
--   El interruptor del espejo se enciende ANTES del renombre. Si se renombrara
--   primero, con el interruptor apagado, el depósito del espejo empezaría a
--   rechazar las facturas que SAE está re-emitiendo: vería una serie cortada con
--   ese código. Hoy las acepta justo porque no existe ninguna fila con ese nombre.
--   Al ir las dos cosas en la misma transacción, esa ventana nunca se abre.

DO $$
DECLARE
    -- ======================= CONFIGURACIÓN =======================
    v_solo_diagnostico     boolean := true;   -- ← false para APLICAR
    v_renombrar_remisiones boolean := true;   -- RFEHMOHOS→RZEHMOHOS, RFMAFAN→RZMAFAN
    v_tenant_slug          text    := 'cristian-gerardo-zarate-orozco';

    t_id        uuid;
    par         record;
    fila        record;
    v_vieja     record;
    v_choque    record;
    v_n         int;
    v_sobra     uuid;
    v_gana      uuid;
    v_vieja_vacia boolean;
    v_nueva_vacia boolean;
    v_max       int;
    v_cambios   int := 0;
BEGIN
    SELECT id INTO t_id FROM tenants WHERE slug = v_tenant_slug;
    IF t_id IS NULL THEN
        RAISE EXCEPTION 'No existe el tenant %', v_tenant_slug;
    END IF;

    RAISE NOTICE '=================================================================';
    RAISE NOTICE 'REVERSA DEL CORTE DE PACHUCA — tenant %', v_tenant_slug;
    RAISE NOTICE 'Modo: %', CASE WHEN v_solo_diagnostico THEN 'DIAGNÓSTICO (no aplica nada)' ELSE 'APLICAR' END;
    RAISE NOTICE '=================================================================';

    -- ---------- INVENTARIO: series candidatas de Pachuca ----------
    RAISE NOTICE '--- Series de Pachuca hoy:';
    FOR fila IN
        SELECT codigo, tipo_documento, espejo_sae, folio_actual, activa
          FROM series
         WHERE tenant_id = t_id
           AND codigo IN ('ZEHMOHOS','FEHMOHOS','ZMAFAN','FMAFAN',
                          'RZEHMOHOS','RFEHMOHOS','RZMAFAN','RFMAFAN')
         ORDER BY tipo_documento, codigo
    LOOP
        RAISE NOTICE '    % (%) espejo_sae=% folio_actual=% activa=%',
            rpad(fila.codigo::text, 10), fila.tipo_documento, fila.espejo_sae, fila.folio_actual, fila.activa;
    END LOOP;

    -- ---------- INVENTARIO: facturas nativas de esas series ----------
    RAISE NOTICE '--- Facturas NATIVAS emitidas en las series cortadas:';
    FOR fila IN
        SELECT serie, estado, count(*) AS n, min(folio) AS folio_min, max(folio) AS folio_max
          FROM facturas
         WHERE tenant_id = t_id AND origen = 'NATIVA' AND serie IN ('FEHMOHOS','FMAFAN')
         GROUP BY serie, estado ORDER BY serie, estado
    LOOP
        RAISE NOTICE '    % % → % factura(s), folios %..%',
            rpad(fila.serie::text, 10), rpad(fila.estado::text, 10), fila.n, fila.folio_min, fila.folio_max;
    END LOOP;

    -- ---------- INVENTARIO: listas de proyecto (solo informativo) ----------
    RAISE NOTICE '--- Listas de proyecto (el script NO las toca):';
    FOR fila IN
        SELECT codigo, nombre, sae_empresa, sae_lista
          FROM listas_precios
         WHERE tenant_id = t_id AND codigo IN ('SAE5','SAE6','SAE7','SAE8','SAE9')
           AND deleted_at IS NULL
         ORDER BY codigo
    LOOP
        RAISE NOTICE '    % → vínculo SAE %:%  (NULL = la manda el Facturador)',
            rpad(fila.codigo::text, 6), COALESCE(fila.sae_empresa,'—'), COALESCE(fila.sae_lista::text,'—');
    END LOOP;

    -- ---------- CANDADO: nada de renombrar con CFDI nativos vivos ----------
    SELECT count(*) INTO v_n
      FROM facturas
     WHERE tenant_id = t_id AND origen = 'NATIVA'
       AND serie IN ('FEHMOHOS','FMAFAN') AND estado = 'TIMBRADA';
    IF v_n > 0 THEN
        RAISE EXCEPTION
            'Hay % factura(s) nativa(s) TIMBRADA(s) viva(s) en FEHMOHOS/FMAFAN. '
            'Cancélalas primero DESDE LA APLICACIÓN (el PAC no se toca por SQL), con motivo '
            'distinto de 01 salvo que tengan sustituta timbrada aquí: al cancelar se liberan '
            'sus remisiones y podrán salir por el masivo. Después vuelve a correr este script.', v_n;
    END IF;

    -- ======================= APLICACIÓN =======================
    -- Cuatro escenarios posibles por par, porque la reversa puede venir a medias:
    --   A. solo existe la cortada            → se renombra.
    --   B. existen las dos y la NUEVA está sin usar (la sembró el PR #173)
    --                                        → se absorbe la nueva y se renombra.
    --   C. existen las dos y la CORTADA está sin usar (sobrante de una reversa
    --      hecha a mano)                     → se absorbe la cortada; gana la nueva.
    --   D. existen las dos y ambas con folios → ABORTA: lo decide una persona.
    -- Al final, la que sobreviva queda con espejo encendido y el contador sano.
    FOR par IN
        SELECT * FROM (VALUES
            ('FEHMOHOS',  'ZEHMOHOS',  'FACTURA'),
            ('FMAFAN',    'ZMAFAN',    'FACTURA'),
            ('RFEHMOHOS', 'RZEHMOHOS', 'REMISION'),
            ('RFMAFAN',   'RZMAFAN',   'REMISION')
        ) AS t(viejo, nuevo, tipo)
    LOOP
        IF par.tipo = 'REMISION' AND NOT v_renombrar_remisiones THEN
            RAISE NOTICE '[%] omitida por configuración (v_renombrar_remisiones=false)', par.viejo;
            CONTINUE;
        END IF;

        SELECT id, codigo, espejo_sae, folio_actual INTO v_vieja
          FROM series WHERE tenant_id = t_id AND tipo_documento = par.tipo AND codigo = par.viejo;
        SELECT id, codigo, espejo_sae, folio_actual INTO v_choque
          FROM series WHERE tenant_id = t_id AND tipo_documento = par.tipo AND codigo = par.nuevo;

        v_sobra := NULL; v_gana := NULL;

        IF v_vieja.id IS NULL AND v_choque.id IS NULL THEN
            RAISE NOTICE '[%] no existe ni % — revísalo a mano', par.viejo, par.nuevo;
            CONTINUE;

        ELSIF v_vieja.id IS NULL THEN
            v_gana := v_choque.id;
            RAISE NOTICE '[%] ya renombrada: % está en su sitio', par.viejo, par.nuevo;

        ELSIF v_choque.id IS NULL THEN
            v_gana := v_vieja.id;

        ELSE
            -- Las dos existen. ¿Cuál está sin usar? Contador en cero y, en FACTURA,
            -- sin una sola NATIVA emitida por ella. Solo se cuentan las nativas: los
            -- reflejos del espejo guardan el código como texto y llegan con el folio
            -- que les puso SAE, sin pasar por el contador de la fila.
            SELECT count(*) INTO v_n FROM facturas
             WHERE tenant_id = t_id AND serie = par.viejo AND origen = 'NATIVA';
            v_vieja_vacia := (v_vieja.folio_actual = 0)
                         AND (par.tipo <> 'FACTURA' OR v_n = 0);
            SELECT count(*) INTO v_n FROM facturas
             WHERE tenant_id = t_id AND serie = par.nuevo AND origen = 'NATIVA';
            v_nueva_vacia := (v_choque.folio_actual = 0)
                         AND (par.tipo <> 'FACTURA' OR v_n = 0);

            IF v_nueva_vacia THEN
                v_sobra := v_choque.id; v_gana := v_vieja.id;
                RAISE NOTICE '[%] % existe pero está sin usar: la absorbo', par.viejo, par.nuevo;
            ELSIF v_vieja_vacia THEN
                v_sobra := v_vieja.id; v_gana := v_choque.id;
                RAISE NOTICE '[%] sobrante sin usar y % ya en su sitio: la absorbo', par.viejo, par.nuevo;
            ELSE
                RAISE EXCEPTION
                    'Colisión en %: % (folio %) y % (folio %) están las DOS en uso. '
                    'Decide cuál sobrevive antes de seguir.',
                    par.tipo, par.viejo, v_vieja.folio_actual, par.nuevo, v_choque.folio_actual;
            END IF;
        END IF;

        -- Absorber la sobrante: mudar sus referencias a la que gana y borrarla.
        -- Sin este repunte el DELETE se llevaría en cascada el abanico del vínculo
        -- y las asignaciones de precios que apunten a ella (series no tiene borrado lógico).
        IF v_sobra IS NOT NULL THEN
            UPDATE cliente_sucursales SET serie_factura_id  = v_gana
             WHERE tenant_id = t_id AND serie_factura_id  = v_sobra;
            UPDATE cliente_sucursales SET serie_remision_id = v_gana
             WHERE tenant_id = t_id AND serie_remision_id = v_sobra;
            UPDATE remisiones         SET serie_id = v_gana
             WHERE tenant_id = t_id AND serie_id = v_sobra;
            UPDATE lista_asignaciones SET serie_id = v_gana
             WHERE tenant_id = t_id AND serie_id = v_sobra;
            UPDATE cliente_sucursal_series css SET serie_id = v_gana
             WHERE css.tenant_id = t_id AND css.serie_id = v_sobra
               AND NOT EXISTS (SELECT 1 FROM cliente_sucursal_series otro
                                WHERE otro.cliente_sucursal_id = css.cliente_sucursal_id
                                  AND otro.serie_id = v_gana);
            DELETE FROM cliente_sucursal_series WHERE tenant_id = t_id AND serie_id = v_sobra;
            DELETE FROM series WHERE id = v_sobra;
            v_cambios := v_cambios + 1;
            RAISE NOTICE '    referencias mudadas y fila sobrante borrada';
        END IF;

        -- Renombrar, si la que gana todavía carga el nombre cortado.
        IF v_gana = v_vieja.id THEN
            UPDATE series SET codigo = par.nuevo WHERE id = v_gana;
            v_cambios := v_cambios + 1;
            RAISE NOTICE '[%] renombrada → %', par.viejo, par.nuevo;
        END IF;

        -- Interruptor del espejo y contador sano, solo en FACTURA.
        IF par.tipo = 'FACTURA' THEN
            UPDATE series SET espejo_sae = true WHERE id = v_gana AND NOT espejo_sae;
            IF FOUND THEN
                RAISE NOTICE '[%] espejo_sae → true (masivo desbloqueado; nativo prohibido)', par.nuevo;
            END IF;
            SELECT COALESCE(MAX(folio), 0) INTO v_max
              FROM facturas WHERE tenant_id = t_id AND serie = par.nuevo;
            SELECT folio_actual INTO v_n FROM series WHERE id = v_gana;
            IF v_max > v_n THEN
                UPDATE series SET folio_actual = v_max WHERE id = v_gana;
                v_cambios := v_cambios + 1;
                RAISE NOTICE '[%] folio_actual % → % (último emitido con ese código; por debajo, '
                             'apagar el espejo generaría folios repetidos)', par.nuevo, v_n, v_max;
            END IF;
        END IF;
    END LOOP;

    -- ======================= VERIFICACIÓN =======================
    RAISE NOTICE '--- Cómo queda:';
    FOR fila IN
        SELECT codigo, tipo_documento, espejo_sae, folio_actual
          FROM series
         WHERE tenant_id = t_id
           AND codigo IN ('ZEHMOHOS','FEHMOHOS','ZMAFAN','FMAFAN',
                          'RZEHMOHOS','RFEHMOHOS','RZMAFAN','RFMAFAN')
         ORDER BY tipo_documento, codigo
    LOOP
        RAISE NOTICE '    % (%) espejo_sae=% folio_actual=%',
            rpad(fila.codigo::text, 10), fila.tipo_documento, fila.espejo_sae, fila.folio_actual;
    END LOOP;

    SELECT count(*) INTO v_n
      FROM series
     WHERE tenant_id = t_id AND tipo_documento = 'FACTURA'
       AND codigo IN ('ZEHMOHOS','ZMAFAN') AND espejo_sae;
    IF v_n <> 2 THEN
        RAISE EXCEPTION 'Esperaba ZEHMOHOS y ZMAFAN de FACTURA con espejo_sae=true; hay %', v_n;
    END IF;

    SELECT count(*) INTO v_n
      FROM series
     WHERE tenant_id = t_id AND codigo IN ('FEHMOHOS','FMAFAN');
    IF v_n <> 0 THEN
        RAISE EXCEPTION 'Quedaron % serie(s) con el nombre cortado — la reversa no terminó', v_n;
    END IF;

    RAISE NOTICE '=================================================================';
    RAISE NOTICE 'Reversa lista: % serie(s) renombrada(s). Río Libre (RIO) sigue siendo', v_cambios;
    RAISE NOTICE 'el único cliente nativo. Siguiente paso fuera de este script:';
    RAISE NOTICE '  · las remisiones liberadas vuelven a salir por el masivo de factura;';
    RAISE NOTICE '  · decide si revinculas SAE5..SAE9 (hoy las manda el Facturador);';
    RAISE NOTICE '  · el mapa serie→proyecto de los reportes no conoce FEHMOHOS/FMAFAN:';
    RAISE NOTICE '    al desaparecer esas series el hueco se cierra solo.';
    RAISE NOTICE '=================================================================';

    IF v_solo_diagnostico THEN
        RAISE EXCEPTION
            'MODO DIAGNÓSTICO: nada quedó aplicado. Si lo de arriba se ve bien, '
            'cambia v_solo_diagnostico := false y vuelve a correrlo.';
    END IF;
END $$;
