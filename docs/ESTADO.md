# Estado del proyecto — cierre del 28/08/2026

Lo reescribe `/endworking` al cerrar el día. Punto de entrada para retomar: basta abrir esta carpeta.

## Git

| | |
|---|---|
| Rama base | `main` @ `47bf1dd` — igual que `origin/main` |
| Remoto | `frutaskelly/facturador` |
| Working tree | limpio |
| Worktrees | 1 · `code-audit-best-practices-39c101` (rama `claude/oc-original-en-remisiones`) |

`Cristian/smartsupply-v2.0` es un enlace simbólico a esta carpeta, no otro clon — por eso ese
worktree aparece registrado bajo esa ruta.

## Deploy

**Al día y verificado.** El 28-ago se corrió `./deploy.sh`: la construcción salió de caché
porque las imágenes ya traían el código, alembic no encontró migraciones por aplicar, y los
contenedores se recrearon. Se comprobó dentro de la imagen del backend que están `0053`
(PR #34) y `0054` (PR #35).

Nota sobre la frescura: comparar la fecha de la imagen contra la del último commit da un falso
positivo aquí — las imágenes se construyeron **25 segundos antes** de que se fusionaran #34 y
#35, así que "parecen" atrasadas sin estarlo. Para saberlo de verdad hay que mirar el
contenido de la imagen, no la marca de tiempo.

La imagen del `landing` es del 20-ago y no se reconstruyó: su contexto de build no ha cambiado
desde entonces. Tampoco es un atraso.

## Lo que se limpió hoy

Se podaron 2 worktrees ya fusionados y 5 ramas locales sin trabajo propio:
`catalogo-productos-multicliente-c04900` (PR #34), `silly-jang-a3f64e` (PR #35),
`bandeja-oc-en-curso`, `code-audit-best-practices-39c101`, `multi-empresa-account-4105fd`,
`remote-control-c86a4e`.

Cuidado al verificar "ya fusionado": los PRs entran con **squash**, así que los commits de la
rama nunca aparecen en `main` por SHA y `git cherry` los marca como pendientes. Hay que
comparar el **contenido** de los archivos (`git rev-parse <rama>:<archivo>` contra
`origin/main:<archivo>`), no los SHAs ni el conteo de commits.

## Pendientes

1. **PR #33 abierto y mergeable** — "Abrir la OC original desde la lista de remisiones", rama
   `claude/oc-original-en-remisiones`. Ya está todo subido; solo falta que lo fusiones. Su
   worktree se dejó en pie a propósito.
2. **Rama `claude/worktree-remisiones-localhost-41a283` sin worktree y sin revisar.** Su PR #32
   se fusionó el 27-ago, pero la cabeza local (`3e2a2d41`) **no coincide** con la que se
   fusionó (`a6edec17`): tiene 6 commits más allá de lo que entró. No se borró. Hay que
   revisar si eso es trabajo que falta o basura de un rebase.
3. **Contenedor `smartsupplyv2-postgres-1` huérfano.** Corre desde
   `.claude/worktrees/worktree-price-lists-schema-348ec1`, **una ruta que ya no existe**: el
   worktree se borró y el contenedor siguió vivo. Sigue sirviendo, pero nadie puede
   reconstruirlo desde ahí. Hay que repuntarlo a un compose de este checkout antes de que
   alguien lo reinicie.
