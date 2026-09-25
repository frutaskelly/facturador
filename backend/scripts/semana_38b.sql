-- Corte de semana 38-B (decisión del dueño, 25-sep-2026).
--
-- El 13-sep el bot pasó a numerar las semanas con el calendario ISO y el lunes
-- 14-sep arrancó como SEM 38 (con la cuenta anterior era la 37). Desde entonces
-- el equipo va un número ADELANTADO: la semana del 21 al 27 de septiembre salió
-- como 39 y debía ser 38. Se corrige así:
--
--   · Pachuca (HO, CE, …): del 14 al 19 la numeró 37, así que la 38 está libre.
--     La semana del 21-27 pasa de 39 a 38, sin marca.
--   · Villahermosa (VH): la 38 YA se usó del 14 al 20 (48 remisiones facturadas,
--     intocables). La del 21-27 pasa a «38-B»: folio VH-38ROV-LUN-B, observación
--     «SEM 38-B …». Una entrega aparte queda VH-38PAL-MIE-B-2.
--   · La semana del 28-sep al 4-oct pasa de 40 a 39 para TODOS: desde ahí la
--     numeración ya queda alineada y no lleva marca.
--
-- Qué toca: remisiones EN BORRADOR (su_pedido, notas, origen_externo) y las
-- órdenes recibidas de esas semanas (folio_externo, origen_externo y las mismas
-- llaves dentro del payload). Una orden cuya remisión ya no está en borrador no
-- se toca.
--
-- Qué NO toca: nada facturado ni timbrado, la numeración del bot (va aparte, y
-- TIENE QUE cambiar al mismo tiempo o el bot reenvía las entregas con el número
-- viejo como si fueran nuevas), el Master de Sheets ni el SAE.
--
-- ============================ CÓMO SE USA ============================
-- 1. Córrelo tal cual: arranca en MODO DIAGNÓSTICO. Imprime cada cambio y al
--    final lanza un error a propósito para que NADA quede aplicado.
-- 2. Si el diagnóstico sale limpio, cambia `v_solo_diagnostico` a false y
--    vuélvelo a correr. Es un DO $$ … $$: o se aplica todo, o nada. Los índices
--    únicos de origen_externo frenan cualquier choque con la semana anterior.

CREATE OR REPLACE FUNCTION pg_temp.sem_corte(t text, sem int, es_vh boolean)
RETURNS text LANGUAGE sql IMMUTABLE AS $f$
  SELECT CASE
    WHEN t IS NULL THEN NULL
    WHEN sem = 39 AND es_vh THEN
      regexp_replace(
        regexp_replace(t, '\m(VH-)39([A-Z]{2,4}-[A-Z]{3})(-\d{1,2})?\M', '\138\2-B\3', 'g'),
        '\m(SEM(ANA)?\.?\s*)39\M', '\138-B', 'gi')
    WHEN sem = 39 THEN
      regexp_replace(
        regexp_replace(t, '\m([A-Z]{2,3}-)39([A-Z]{2,4}-[A-Z]{3})', '\138\2', 'g'),
        '\m(SEM(ANA)?\.?\s*)39\M', '\138', 'gi')
    WHEN sem = 40 THEN
      regexp_replace(
        regexp_replace(t, '\m([A-Z]{2,3}-)40([A-Z]{2,4}-[A-Z]{3})', '\139\2', 'g'),
        '\m(SEM(ANA)?\.?\s*)40\M', '\139', 'gi')
    ELSE t END
$f$;

DO $$
DECLARE
    v_solo_diagnostico boolean := true;   -- ← false para APLICAR
    v_tenant uuid := '0114d0d2-1e9b-47d1-b5de-5c6062ae94d8';  -- cristian-gerardo-zarate-orozco
    r record;
    n_rem int := 0;
    n_oc int := 0;
    n int;
    v_sem int;
BEGIN
    RAISE NOTICE 'Modo: %', CASE WHEN v_solo_diagnostico THEN 'DIAGNÓSTICO (no aplica nada)' ELSE 'APLICAR' END;

    CREATE TEMP TABLE _rem ON COMMIT DROP AS
    SELECT id, folio_interno, su_pedido, notas, origen_externo, fecha_entrega,
           substring(upper(su_pedido) from '^[A-Z]{2,3}-(\d{2})')::int AS sem,
           upper(su_pedido) LIKE 'VH-%' AS es_vh
    FROM remisiones
    WHERE tenant_id = v_tenant AND deleted_at IS NULL AND estado = 'BORRADOR'
      AND ((su_pedido ~* '^[A-Z]{2,3}-39[A-Z]' AND fecha_entrega BETWEEN '2026-09-21' AND '2026-09-27')
        OR (su_pedido ~* '^[A-Z]{2,3}-40[A-Z]' AND fecha_entrega BETWEEN '2026-09-28' AND '2026-10-04'));

    CREATE TEMP TABLE _oc ON COMMIT DROP AS
    SELECT o.id, o.folio_externo, o.origen_externo, o.estado, o.fecha_entrega,
           substring(upper(o.folio_externo) from '^[A-Z]{2,3}-(\d{2})')::int AS sem,
           upper(o.folio_externo) LIKE 'VH-%' AS es_vh
    FROM oc_recibidas o
    LEFT JOIN remisiones rm ON rm.id = o.remision_id
    WHERE o.tenant_id = v_tenant
      AND (o.remision_id IS NULL OR (rm.estado = 'BORRADOR' AND rm.deleted_at IS NULL))
      AND ((o.folio_externo ~* '^[A-Z]{2,3}-39[A-Z]' AND o.fecha_entrega BETWEEN '2026-09-21' AND '2026-09-27')
        OR (o.folio_externo ~* '^[A-Z]{2,3}-40[A-Z]' AND o.fecha_entrega BETWEEN '2026-09-28' AND '2026-10-04'));

    FOR r IN SELECT sem, es_vh, count(*) n FROM _rem GROUP BY 1, 2 ORDER BY 1, 2 LOOP
        RAISE NOTICE 'remisiones  sem % %: %', r.sem, CASE WHEN r.es_vh THEN 'VH' ELSE 'resto' END, r.n;
    END LOOP;
    FOR r IN SELECT sem, es_vh, estado, count(*) n FROM _oc GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LOOP
        RAISE NOTICE 'órdenes     sem % % %: %', r.sem, CASE WHEN r.es_vh THEN 'VH' ELSE 'resto' END, r.estado, r.n;
    END LOOP;

    -- Cada remisión con su antes → después
    FOR r IN SELECT * FROM _rem ORDER BY fecha_entrega, su_pedido LOOP
        RAISE NOTICE '% %  %  →  %', r.folio_interno, r.fecha_entrega, r.su_pedido,
            pg_temp.sem_corte(r.su_pedido, r.sem, r.es_vh);
        -- Candado: el folio nuevo no puede existir ya (fuera de las que se mueven)
        IF EXISTS (SELECT 1 FROM remisiones x
                   WHERE x.tenant_id = v_tenant AND x.deleted_at IS NULL
                     AND x.id NOT IN (SELECT id FROM _rem)
                     AND upper(x.su_pedido) = pg_temp.sem_corte(upper(r.su_pedido), r.sem, r.es_vh)) THEN
            RAISE EXCEPTION 'Choque: % ya existe en otra remisión', pg_temp.sem_corte(r.su_pedido, r.sem, r.es_vh);
        END IF;
    END LOOP;
    -- Muestra de notas (que es lo que llega a la observación)
    FOR r IN SELECT * FROM _rem ORDER BY es_vh DESC, fecha_entrega LIMIT 3 LOOP
        RAISE NOTICE 'notas: «%»  →  «%»', r.notas, pg_temp.sem_corte(r.notas, r.sem, r.es_vh);
    END LOOP;

    -- Dos pasadas, primero la 39 y luego la 40: VH-40ROV-LUN pasa a VH-39ROV-LUN,
    -- que sólo queda libre cuando la 39 de hoy ya se movió a 38-B (el índice único
    -- de origen_externo se revisa renglón por renglón, no al final).
    FOR v_sem IN SELECT unnest(ARRAY[39, 40]) LOOP
    UPDATE remisiones x SET
        su_pedido      = pg_temp.sem_corte(x.su_pedido, t.sem, t.es_vh),
        notas          = pg_temp.sem_corte(x.notas, t.sem, t.es_vh),
        origen_externo = pg_temp.sem_corte(x.origen_externo, t.sem, t.es_vh),
        updated_at     = now()
    FROM _rem t WHERE x.id = t.id AND t.sem = v_sem;
    GET DIAGNOSTICS n = ROW_COUNT;
    n_rem := n_rem + n;

    UPDATE oc_recibidas x SET
        folio_externo  = pg_temp.sem_corte(x.folio_externo, t.sem, t.es_vh),
        origen_externo = pg_temp.sem_corte(x.origen_externo, t.sem, t.es_vh),
        payload = x.payload
            || jsonb_strip_nulls(jsonb_build_object(
                 'folio_externo',  pg_temp.sem_corte(x.payload->>'folio_externo', t.sem, t.es_vh),
                 'origen_externo', pg_temp.sem_corte(x.payload->>'origen_externo', t.sem, t.es_vh),
                 'observaciones',  pg_temp.sem_corte(x.payload->>'observaciones', t.sem, t.es_vh))),
        updated_at = now()
    FROM _oc t WHERE x.id = t.id AND t.sem = v_sem;
    GET DIAGNOSTICS n = ROW_COUNT;
    n_oc := n_oc + n;
    END LOOP;

    RAISE NOTICE 'Remisiones movidas: %   Órdenes movidas: %', n_rem, n_oc;

    -- Verificación: ya no queda nada de esas semanas con el número viejo
    IF EXISTS (SELECT 1 FROM remisiones WHERE tenant_id = v_tenant AND deleted_at IS NULL AND estado = 'BORRADOR'
                 AND su_pedido ~* '^[A-Z]{2,3}-39[A-Z]' AND fecha_entrega BETWEEN '2026-09-21' AND '2026-09-27') THEN
        RAISE EXCEPTION 'Quedó alguna remisión en 39 de la semana 21-27';
    END IF;

    IF v_solo_diagnostico THEN
        RAISE EXCEPTION 'DIAGNÓSTICO OK: movería % remisiones y % órdenes. Todo se deshace; cambia v_solo_diagnostico a false para aplicar.', n_rem, n_oc;
    END IF;
END $$;
