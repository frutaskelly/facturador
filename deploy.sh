#!/usr/bin/env bash
# Reconstruye y levanta el stack de PRODUCCIÓN del Facturador — lo que sirve
# facturador.mx (túnel de Cloudflare incluido en docker-compose.prod.yml, con
# restart:unless-stopped, así que sobrevive un reinicio del Mini).
#
# Construye desde los ARCHIVOS LOCALES en disco. Como el checkout lo comparten
# varias sesiones, antes de construir verifica que lo que hay en disco sea
# exactamente lo que está en GitHub (ver la puerta de seguridad abajo).
#
# Uso:  ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.prod.yml"

# ── Puerta de seguridad ───────────────────────────────────────────────────────
# Se despliegan los ARCHIVOS EN DISCO, y este checkout lo comparten varias
# sesiones a la vez. Sin esta puerta, un deploy publica el trabajo a medias de
# otra sesión (ya pasó: la bandeja de OC salió a producción sin que su sesión
# lo pidiera). Regla: solo se despliega lo que ya está en GitHub.
#
#   FORCE=1 ./deploy.sh   salta la puerta (a propósito, avisando qué se lleva).
if [ "${FORCE:-0}" != "1" ] && git rev-parse --git-dir >/dev/null 2>&1; then
  sucio=$(git status --porcelain | head -20)
  git fetch -q origin 2>/dev/null || true
  sin_pushear=$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)
  atrasado=$(git rev-list --count 'HEAD..@{upstream}' 2>/dev/null || echo 0)

  if [ -n "$sucio" ] || [ "$sin_pushear" != "0" ] || [ "$atrasado" != "0" ]; then
    echo "✋ Deploy detenido: este checkout NO coincide con GitHub."
    [ -n "$sucio" ] && { echo "   · Cambios sin guardar:"; echo "$sucio" | sed 's/^/     /'; }
    [ "$sin_pushear" != "0" ] && {
      echo "   · $sin_pushear commit(s) sin pushear — puede ser trabajo en curso de otra sesión:"
      git log --oneline '@{upstream}..HEAD' | head -10 | sed 's/^/     /'
    }
    [ "$atrasado" != "0" ] && echo "   · Te faltan $atrasado commit(s) del remoto: haz git pull antes."
    echo
    echo "   Qué hacer: que cada sesión pushee su trabajo (o muévelo a su rama),"
    echo "   deja main igual que origin/main y vuelve a correr ./deploy.sh."
    echo "   Si de verdad quieres publicar el estado actual:  FORCE=1 ./deploy.sh"
    exit 1
  fi
  echo "✓ Checkout limpio y al día con GitHub ($(git rev-parse --short HEAD))"
fi

echo "→ 1/3 Construyendo imágenes de producción…"
$COMPOSE build

echo "→ 2/3 Aplicando migraciones a la BD de prod (Supabase)…"
# El backend no migra al arrancar (su CMD es solo uvicorn), así que lo hacemos
# aquí ANTES de servir el código nuevo. Si tus migraciones se manejan por otra
# vía, borra esta línea.
$COMPOSE run --rm backend alembic upgrade head

echo "→ 3/3 Levantando/reemplazando contenedores…"
$COMPOSE up -d

echo "✓ Live en https://facturador.mx"
$COMPOSE ps
