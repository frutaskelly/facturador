-- Datos de negocio DESPUÉS de desplegar las migraciones 0060/0061 (sucursal
-- como unidad de negocio). Correr UNA vez contra prod, ya con el esquema nuevo.
-- Decisiones del dueño (01-sep-2026): los 6 proyectos históricos son de la
-- plaza Pachuca; el HOSPITALES de Tabasco es P-HOSPITALES-TAB (ya insertado) y
-- la lista EHMOVH0826 que colgaba de (EHMO, Tabasco, HOSPITALES) se muda a él.
-- Todo acotado al tenant de la operación (cristian-gerardo-zarate-orozco).

BEGIN;

-- 1) Los 6 proyectos históricos entregan en Pachuca.
UPDATE proyectos p
   SET sucursal_id = s.id, updated_at = now()
  FROM tenants t, sucursales s
 WHERE t.slug = 'cristian-gerardo-zarate-orozco'
   AND p.tenant_id = t.id
   AND s.tenant_id = t.id AND s.nombre = 'Pachuca' AND s.deleted_at IS NULL
   AND p.deleted_at IS NULL
   AND p.codigo IN ('P-CEREZOS', 'P-DIF', 'P-HOSPITALES', 'P-IMSSBIENESTAR',
                    'P-SECRETARIONERI', 'P-SEGURIDADPUBLICA');

-- 2) La negociación de Tabasco (EHMOVH0826, espec-11 provisional del 31-ago)
--    se cuelga del proyecto de SU plaza.
UPDATE lista_asignaciones la
   SET proyecto_id = pnew.id
  FROM tenants t, proyectos pnew
 WHERE t.slug = 'cristian-gerardo-zarate-orozco'
   AND la.tenant_id = t.id
   AND pnew.tenant_id = t.id AND pnew.codigo = 'P-HOSPITALES-TAB'
   AND la.id = 'da5e658b-a49a-4c80-9c42-153df6a754b2';

-- 3) Limpieza de lo que un INSERT sin acotar sembró de más en frutas-kelly
--    (ese tenant no opera Tabasco): su HOSPITALES sin plaza sobra.
UPDATE proyectos p
   SET deleted_at = now(), updated_at = now()
  FROM tenants t
 WHERE t.slug = 'frutas-kelly' AND p.tenant_id = t.id
   AND p.codigo = 'P-HOSPITALES-TAB' AND p.deleted_at IS NULL;

COMMIT;

-- Verificación:
SELECT t.slug, p.codigo, p.nombre, s.nombre AS plaza
  FROM proyectos p
  JOIN tenants t ON t.id = p.tenant_id
  LEFT JOIN sucursales s ON s.id = p.sucursal_id
 WHERE p.deleted_at IS NULL
 ORDER BY t.slug, p.nombre;
