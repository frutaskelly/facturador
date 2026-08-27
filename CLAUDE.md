# Facturador — reglas de trabajo

## Varias sesiones a la vez (LEER ANTES DE COMMITEAR O DESPLEGAR)

Este checkout lo comparten varias sesiones de Claude al mismo tiempo, y
**producción se construye de los archivos EN DISCO**. Sin cuidado, una sesión
publica el trabajo a medias de otra (ya ocurrió: la bandeja de OC salió a
producción sin que su sesión lo pidiera).

**Regla: nadie trabaja en `main`. `main` solo se actualiza con `git pull`, y
solo se despliega desde `main` idéntico a `origin/main`.**

1. **Antes de tocar nada**: `git fetch origin` y mira dónde estás parado.
2. **Trabaja en tu rama**: `git switch -c claude/<tema>`. Si te toca un `main`
   con commits sin pushear, son de otra sesión: **no los pushees ni los muevas**.
3. **Al terminar**: `fetch` → rebase sobre `origin/main` → tests → push → merge.
4. **Desplegar**: solo `./deploy.sh`, que se detiene si el checkout no coincide
   con GitHub. `FORCE=1` existe, pero saltarlo publica trabajo ajeno a medias.

Si encuentras commits sin pushear que no son tuyos, preserva el trabajo en una
rama con nombre (`git branch -f claude/<tema>-en-curso main`) antes de nada, y
**no muevas `main` ni el working tree**: otra sesión puede estar escribiendo ahí
en este momento.

## Deploy

`./deploy.sh` desde la raíz: construye, aplica migraciones a Supabase y levanta
el stack `facturadorprod`. Verifica después con los dominios reales
(facturador.mx, api., app., admin.).

## Antes de dar algo por terminado

- Backend: `cd backend && ./.venv/bin/python -m pytest -q`
- Frontend: `cd frontend && npx tsc --noEmit`
- Cambios de UI: recuerda que el usuario recarga con Ctrl+Shift+R.
