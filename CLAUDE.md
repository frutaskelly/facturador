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

## Dónde está el estado (empieza por aquí)

`docs/ESTADO.md` lleva el estado vivo del proyecto: en qué commit está `main`, si el deploy está
al día, qué se cerró en los últimos días y los **pendientes numerados**. Lo reescribe
`/endworking` al cerrar. Va **commiteado a propósito**: la puerta de checkout limpio de
`deploy.sh` se dispara con archivos sin rastrear, así que un `ESTADO.md` suelto detiene el
deploy.

## Cómo está armado

- `backend/` — FastAPI + SQLAlchemy, migraciones Alembic en `backend/migrations/versions`.
  Multi-tenant con RLS (`public.current_tenant_id()`); los permisos y el alcance por cliente
  viven en `backend/app/core/rbac.py`.
- `frontend/` — Next.js (App Router). El cliente de la API se **genera**: tras cambiar el
  backend corre `python -m scripts.export_openapi` y luego `npm run gen:api`.
- El conector con SAE (Aspel) y el bot de WhatsApp viven en otro repo
  (`SmartSupply/bot`), corren desde disco vía launchd y consultan SAE por sqlcmd.

## Dos reglas que no se rompen

1. **El Facturador no estampa un folio del SAE que el SAE no haya confirmado.** Generar el Excel
   masivo deja rastro (`export_sae_at`, `export_sae_folio`) y nada más; `factura_sae` lo escribe
   solo el espejo o una captura manual. Sin confirmación, la remisión se queda en BORRADOR.
2. **Los folios siempre los pone el sistema**, sin ceros a la izquierda ni espacios. SAE los
   guarda rellenados con ceros (`0000024736`), así que al comparar hay que normalizar.
