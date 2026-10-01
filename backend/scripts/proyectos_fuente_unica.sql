-- Proyectos: una sola fuente (1-oct-2026, requiere la migración 0094).
--
-- Deja Catálogo → Proyectos igual al mapa del dueño, para que Reportes y
-- Cobranza (que ya leen del catálogo) muestren los mismos nombres:
--
--   ZEHMOHOS/ZEHMOFAC/FEHMOHOS → HOSPITALES HIDALGO     ZECA    → HOSPITALES CAMPECHE
--   ZEHMOTG                    → HOSPITALES TUXTLA      ZEHMOVH → HOSPITALES VILLAHERMOSA 2026
--   ZDIF                       → DIF CHIAPAS (EHMO)     ZSUR    → COMEDORES TUXTLA (Sureña)
--   ZMAFAN → CERESOS (el resto) · CDMX AZCAPOTZALCO (obs CDMX/AZCAPOTZALCO)
--          · DIF HIDALGO (obs DIF/COSTAL/COSTALES)
--
-- Decisiones: NERI y SEGURIDAD PÚBLICA siguen con su lista pero se reportan en
-- CERESOS; IMSS BIENESTAR se reporta en HOSPITALES HIDALGO (se deja ACTIVO: sus
-- OC cobran con la lista 9 vía el proyecto). GZPACHUCA no se toca. No cambia
-- ninguna lista de precios: los proyectos nuevos quedan sin lista y cobran con
-- la del cliente/plaza, como hoy.
--
-- Además: las 41 remisiones RZEHMOVH (29-ago..1-sep) ligadas al proyecto
-- HOSPITALES de Hidalgo pasan al de Villahermosa.
--
-- Idempotente: se puede correr dos veces.
BEGIN;

-- Renombres (el código sigue al nombre, como lo haría la API).
UPDATE proyectos SET nombre = 'HOSPITALES HIDALGO', codigo = 'HOSPITALESHIDALGO',
       series = '["ZEHMOHOS","ZEHMOFAC","FEHMOHOS"]', palabras_obs = '[]'
 WHERE id = '233e55be-f5d7-4101-a3d1-e396294152ee';
UPDATE proyectos SET nombre = 'HOSPITALES CAMPECHE', codigo = 'HOSPITALESCAMPECHE',
       series = '["ZECA"]', palabras_obs = '[]'
 WHERE id = 'caa417fc-868b-478c-8f80-6dd4e73d92bc';
UPDATE proyectos SET nombre = 'HOSPITALES TUXTLA', codigo = 'HOSPITALESTUXTLA',
       series = '["ZEHMOTG"]', palabras_obs = '[]'
 WHERE id = '27ad7d79-1df2-4354-ba51-a419a9da6f71';
UPDATE proyectos SET nombre = 'HOSPITALES VILLAHERMOSA 2026', codigo = 'HOSPITALESVILLAHERMO',
       series = '["ZEHMOVH"]', palabras_obs = '[]'
 WHERE id = '7c569109-19d5-41fc-8682-9f7b3fdf9cc8';
UPDATE proyectos SET nombre = 'CERESOS', codigo = 'CERESOS',
       series = '["ZMAFAN"]', palabras_obs = '[]'
 WHERE id = 'e630f36e-9ec7-44d9-ad52-7078dbd91b22';
UPDATE proyectos SET nombre = 'DIF HIDALGO', codigo = 'DIFHIDALGO',
       series = '["ZMAFAN"]', palabras_obs = '["DIF","COSTAL","COSTALES"]'
 WHERE id = '4a0639da-a923-462e-b10c-8cc63a2167c8';

-- Se reportan dentro de otro.
UPDATE proyectos SET reporta_en_id = 'e630f36e-9ec7-44d9-ad52-7078dbd91b22'
 WHERE id IN ('8d95f37e-d68b-4ca6-85c5-f03fb0fe2c17',   -- SECRETARIO NERI
              '82085ba7-1b5c-46ff-9496-4776970a9092');  -- SEGURIDAD PUBLICA
UPDATE proyectos SET reporta_en_id = '233e55be-f5d7-4101-a3d1-e396294152ee'
 WHERE id = '3de21f64-7f26-47ec-b5e0-bf2b7ba79e68';     -- IMSS BIENESTAR

-- Los que faltaban en el catálogo.
INSERT INTO proyectos (id, tenant_id, codigo, nombre, cliente_id, sucursal_id, activo, series, palabras_obs)
SELECT gen_random_uuid(), t.id, v.codigo, v.nombre, v.cliente_id::uuid, v.sucursal_id::uuid, true,
       v.series::jsonb, v.palabras::jsonb
  FROM tenants t,
       (VALUES
         ('DIFCHIAPAS', 'DIF CHIAPAS', 'ea22ff95-d339-4b8a-ba53-bd57b515c4d7',
          'd55b176c-f032-4ba5-acb7-acdad4adb226', '["ZDIF"]', '[]'),
         ('COMEDORESTUXTLA', 'COMEDORES TUXTLA', '47b76705-384d-48c9-9c6f-05afd8657e74',
          'd55b176c-f032-4ba5-acb7-acdad4adb226', '["ZSUR"]', '[]'),
         ('CDMXAZCAPOTZALCO', 'CDMX AZCAPOTZALCO', '0556ac6f-1e10-4bd0-a115-3715a3032806',
          NULL, '["ZMAFAN"]', '["CDMX","AZCAPOTZALCO"]')
       ) AS v(codigo, nombre, cliente_id, sucursal_id, series, palabras)
 WHERE t.slug = 'cristian-gerardo-zarate-orozco'
   AND NOT EXISTS (SELECT 1 FROM proyectos p WHERE p.tenant_id = t.id AND p.codigo = v.codigo);

-- Las remisiones de Villahermosa que quedaron con el proyecto de Hidalgo.
UPDATE remisiones r SET proyecto_id = '7c569109-19d5-41fc-8682-9f7b3fdf9cc8'
  FROM series s
 WHERE s.id = r.serie_id AND s.codigo = 'RZEHMOVH'
   AND r.proyecto_id = '233e55be-f5d7-4101-a3d1-e396294152ee';

-- Verificación: el catálogo como queda.
SELECT p.nombre, p.series, p.palabras_obs, rp.nombre AS se_reporta_en, p.activo
  FROM proyectos p LEFT JOIN proyectos rp ON rp.id = p.reporta_en_id
 WHERE p.tenant_id = (SELECT id FROM tenants WHERE slug = 'cristian-gerardo-zarate-orozco')
   AND p.deleted_at IS NULL
 ORDER BY coalesce(rp.nombre, p.nombre), p.reporta_en_id NULLS FIRST;

COMMIT;
