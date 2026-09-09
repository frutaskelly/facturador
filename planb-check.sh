#!/usr/bin/env bash
# Sonda del plan B de red: distingue en ~30 s entre
#   PEERING-ROTO   → la ruta ISP↔Cloudflare está degradada (el modo del 9-sep-2026:
#                    58 KB/s vía CF mientras Hetzner daba 28 MB/s). El Facturador se
#                    arrastra aunque la app esté sana. Aplica el runbook de
#                    docs/PLAN-B-RED.md.
#   INTERNET-CAIDO → todo el enlace está mal, no solo Cloudflare.
#   OK             → la red no es el problema; busca en la app (docs/PLAN-B-RED.md §Diagnóstico).
#
# Uso:  ./planb-check.sh        (exit 0=OK, 1=PEERING-ROTO, 2=INTERNET-CAIDO)
# No toca nada: solo mide. Seguro de correr en cualquier momento.
set -u

# Velocidad media de bajada en bytes/s; en timeout curl igual reporta lo que alcanzó.
medir() { curl -o /dev/null -sS --max-time 15 -w '%{speed_download}' "$1" 2>/dev/null | cut -d. -f1; }

CF=$(medir 'https://speed.cloudflare.com/__down?bytes=3000000');            CF=${CF:-0}
NOCF=$(medir 'https://fsn1-speed.hetzner.com/100MB.bin');                   NOCF=${NOCF:-0}
TTFB=$(curl -o /dev/null -sS --max-time 20 -w '%{time_starttransfer}' 'https://api.facturador.mx/health' 2>/dev/null || echo 'n/a')

mb() { echo "$(( ${1:-0} / 100000 ))" | sed 's/\(.\)$/.\1/'; }  # bytes/s → MB/s con 1 decimal
echo "Cloudflare : $(mb "$CF") MB/s"
echo "No-CF      : $(mb "$NOCF") MB/s (Hetzner)"
echo "Tunel      : ttfb ${TTFB}s (api.facturador.mx/health)"

UMBRAL_ROTO=1000000     # <1 MB/s vía CF = degradado
UMBRAL_SANO=5000000     # >5 MB/s no-CF = el internet general está bien

if [ "$CF" -lt "$UMBRAL_ROTO" ] && [ "$NOCF" -gt "$UMBRAL_SANO" ]; then
  echo "VEREDICTO  : PEERING-ROTO — ISP↔Cloudflare degradado. Runbook: docs/PLAN-B-RED.md"
  exit 1
elif [ "$CF" -lt "$UMBRAL_ROTO" ]; then
  echo "VEREDICTO  : INTERNET-CAIDO — todo el enlace está mal, no solo Cloudflare."
  exit 2
else
  echo "VEREDICTO  : OK — la red no es el problema."
  exit 0
fi
