#!/usr/bin/env bash
# Reconstruye y levanta el stack de PRODUCCIÓN de smartsupply — lo que sirve
# facturador.mx / smartsupply.mx (túnel de Cloudflare incluido en docker-compose.prod.yml, con
# restart:unless-stopped, así que sobrevive un reinicio del Mini).
#
# Construye desde los ARCHIVOS LOCALES en disco, igual que mini-conta: git no es
# requisito para desplegar. Trabaja en main, corre esto cuando quieras subirlo.
#
# Uso:  ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.prod.yml"

echo "→ 1/3 Construyendo imágenes de producción…"
$COMPOSE build

echo "→ 2/3 Aplicando migraciones a la BD de prod (Supabase)…"
# El backend no migra al arrancar (su CMD es solo uvicorn), así que lo hacemos
# aquí ANTES de servir el código nuevo. Si tus migraciones se manejan por otra
# vía, borra esta línea.
$COMPOSE run --rm backend alembic upgrade head

echo "→ 3/3 Levantando/reemplazando contenedores…"
$COMPOSE up -d

echo "✓ Live en https://facturador.mx (y smartsupply.mx)"
$COMPOSE ps
