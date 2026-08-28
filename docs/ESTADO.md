# Estado del proyecto — cierre del 28/08/2026

Lo reescribe `/endworking` al cerrar el día. Punto de entrada para retomar: basta abrir esta carpeta.

## Git

| | |
|---|---|
| Rama base | `main` @ `d7a3500` — igual que `origin/main` |
| Remoto | `frutaskelly/facturador` |
| Working tree | limpio |
| Worktrees | ninguno |
| PRs abiertos | ninguno |

`Cristian/smartsupply-v2.0` es un enlace simbólico a esta carpeta, no otro clon.

## Deploy

**En vivo y verificado** en https://facturador.mx. Se comprobó **dentro de la imagen** del
backend que trae `oc_archivo_url` (PR #33) y las migraciones `0053` (#34) y `0054` (#35).

⚠️ **La marca de tiempo de la imagen no sirve para saber si el deploy está al día.** El 28-ago
las imágenes se construyeron 25 segundos antes de que se fusionaran dos PRs y "parecían"
atrasadas sin estarlo; el `landing` lleva desde el 20-ago sin reconstruirse simplemente porque
su contexto de build no ha cambiado. Para saberlo de verdad hay que mirar el contenido de la
imagen, no su fecha.

**La puerta de `deploy.sh` bloquea si el checkout no coincide con GitHub** — incluidos archivos
sin rastrear. Por eso este `docs/ESTADO.md` va commiteado: si se deja suelto, cada deploy se
detiene.

## Base de datos local

`smartsupplyv2-postgres-1` (dev, `127.0.0.1:5434`, volumen `smartsupplyv2_pgdata`, 6 bases,
153 MB). El 28-ago estaba corriendo desde
`.claude/worktrees/worktree-price-lists-schema-348ec1`, **una ruta ya borrada**: seguía
sirviendo pero nadie podía reconstruirla. Se reapuntó al `docker-compose.yml` de esta carpeta
con `--force-recreate` (mismo proyecto `smartsupplyv2` → mismo volumen, datos intactos).
Respaldo `pg_dumpall` de esa fecha en `~/Documents/Claude/pgdump_smartsupplyv2_2026-08-28.sql`.

Producción **no** usa esta base: el `deploy.sh` migra contra Supabase.

## Lo que se limpió el 28-ago

Podados 3 worktrees fusionados y 7 ramas locales sin trabajo propio (PRs #32 a #35).

Cuidado al verificar "ya fusionado": los PRs entran con **squash**, así que los commits de la
rama nunca aparecen en `main` por SHA — `rev-list --count`, `git cherry` y el `diff` de tres
puntos los tres mienten. Lo que decide es comparar el **contenido** de los archivos, el
`headRefOid` del PR contra el tip local, o el **patch-id** cuando se sospecha trabajo duplicado
(así se confirmó que `worktree-remisiones-localhost-41a283` no tenía nada perdido: su commit
extra tenía el mismo patch-id `ff9089b9` que el del PR #33).

## Pendientes

Ninguno.
