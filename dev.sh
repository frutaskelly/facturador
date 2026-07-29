#!/usr/bin/env bash
# Levanta el entorno de DESARROLLO local (paridad con mini-conta):
#   - backend  FastAPI en :8011 (uvicorn --reload) contra la BD Supabase cloud
#   - frontend Next.js en :3000 (next dev, hot reload)
# Editas y ves los cambios al instante. Ctrl-C detiene ambos.
# Uso:  ./dev.sh
set -euo pipefail
cd "$(dirname "$0")"

# Settings del backend desde .env, PERO forzando la BD Supabase cloud (el .env y
# el compose de dev apuntan al postgres local vacío → "Usuario no provisionado").
# Las URLs de la BD cloud viven SOLO en .env.prod (gitignored), nunca en git.
set -a; source .env; set +a
export DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env.prod | sed 's/^DATABASE_URL=//')
export DATABASE_URL_ASYNC=$(grep -m1 '^DATABASE_URL_ASYNC=' .env.prod | sed 's/^DATABASE_URL_ASYNC=//')

# Libera puertos por si quedaron procesos viejos (BSD xargs no tiene -r).
for p in 8011 3000; do
  pids=$(lsof -nP -iTCP:$p -sTCP:LISTEN -t 2>/dev/null || true)
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
done

echo "→ Backend  http://localhost:8011  (Supabase cloud, --reload)"
( cd backend && .venv/bin/python -m uvicorn app.main:app --port 8011 --host 0.0.0.0 --reload ) &
BACK=$!

# Ctrl-C detiene también el backend.
trap 'echo; echo "Deteniendo…"; kill "$BACK" 2>/dev/null || true; exit 0' INT TERM

echo "→ Frontend http://localhost:3000  (next dev, hot reload)"
cd frontend && npm run dev
