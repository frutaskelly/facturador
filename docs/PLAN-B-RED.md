# Plan B de red — cuando el peering ISP↔Cloudflare se rompe

*Escrito el 9-sep-2026, el día que pasó. Diseño investigado y verificado contra
documentación oficial de Cloudflare/Tailscale/Supabase; las afirmaciones críticas
pasaron revisión adversarial (ver §Hechos verificados).*

## El problema

Todo el Facturador (facturador.mx, app., api., admin.) sale por **un solo camino**:
Mini → internet residencial de Magyar Telekom (Hungría) → Cloudflare (túnel anclado
en Viena). El 9-sep-2026 ese peering se degradó (58 KB/s vía CF, 20–33% de pérdida,
ambas familias IP y ambos transportes) mientras el internet general estaba perfecto
(28 MB/s a Hetzner). La app estaba sana (`/health` interno <1.5 ms). Nada local lo
arregla: ni reiniciar Docker, ni cambiar quic→http2 (TCP también estaba roto).

**Diagnóstico en 30 segundos**: `./planb-check.sh` en la raíz del repo distingue
PEERING-ROTO / INTERNET-CAIDO / OK.

## El plan, por capas

### Capa 0 — Detección (US$0) — *pendiente de instalar*

1. Sonda launchd en el Mini cada 5 min que corre `planb-check.sh`; a los 2 fallos
   seguidos, notificación macOS + correo.
2. Monitor externo gratis (UptimeRobot, lo da de alta el dueño) contra
   `https://api.facturador.mx/health` — ve la caída total aunque el Mini muera.
3. Hueco aceptado: un endpoint lento-pero-vivo puede pasar el monitor externo;
   la sonda local es la que ve la degradación.

### Capa 1 — Gemelo VPS: conector standby del túnel (~US$9–12/mes) — *pendiente de decisión de gasto*

Stack completo del Facturador en un VPS x86 en **us-east** (recomendado: Hetzner
CPX21 Ashburn ≈€8.5/mes), siempre construido y sincronizado cada noche desde
`origin/main`, con un conector del **mismo túnel** 745371ba que en operación normal
permanece **apagado** (cero tráfico, cero riesgo). El día del incidente: 2 comandos
y los usuarios en México entran por Cloudflare directo al VPS — sin tocar DNS.
Bonus: cubre también apagón/hardware/internet muerto del Mini, y el backend queda
a ~2 ms de Supabase us-east-1 (hoy 127 ms).

**Preparación (una vez, ~1 tarde):**

1. VPS Ubuntu 24.04, usuario `deploy`, SSH solo por llave, ufw solo 22 (cloudflared
   no necesita NINGÚN puerto entrante), swap 2 GB para el build de Next,
   docker+compose, unirlo a la tailnet como `facturador-vps`.
2. Deploy key de **solo lectura** al repo (cuenta `frutaskelly`); clone a `/opt/facturador`.
3. Copiar los únicos dos secretos fuera de git (scp desde el Mini):
   `.env.prod` y `cloudflared-config/` completo. **OJO (verificado): en Linux hay
   que `chmod 644 credentials.json` (o `chown 65532`)** — el cloudflared del
   contenedor corre como uid 65532 y con 600/root no arranca. El `config.yml`
   sirve verbatim: `frontend`/`backend`/`landing` resuelven igual dentro de la
   red del compose del VPS.
4. Build y stack caliente **sin túnel**:
   `docker compose -f docker-compose.prod.yml up -d redis backend frontend landing`
   (el servicio `tunnel` NO se arranca). Esto valida hoy que las imágenes compilan
   en x86_64 (los lockfiles ya traen los binarios linux-x64), no el día del incidente.
5. Pooler verificado: max clients 200 en el tier actual; dos backends (~24
   conexiones) caben de sobra.
6. Scripts en el VPS:
   - `planb-on.sh`: `compose run --rm backend alembic upgrade head` → `up -d tunnel`
     → esperar 4× "Registered tunnel connection".
   - `planb-off.sh`: `compose stop tunnel`.
7. Cron nocturno (03:00 Europa): `git fetch`; si hay commits nuevos, pull + build +
   `up -d` de redis/backend/frontend/landing. **El túnel jamás en el cron y el cron
   no corre migraciones** (eso lo hace `planb-on.sh` al activar).
8. **Ensayo controlado de 2 min** (fuera de horario MX): `planb-on.sh` → confirmar
   conexiones en POPs de EE.UU. (iad/atl, no vie) → abrir la app → `planb-off.sh`.
   Sin ensayo no hay plan B, hay una esperanza.
9. Imprimir la tarjeta del runbook (abajo): una junto al Mini, una en el teléfono.

### Capa 2 — Failover automático (opcional, +~US$5/mes, solo si se repite)

Add-on Load Balancing de Cloudflare con dos túneles de UUID propio y monitor HTTPS:
es el **único** mecanismo oficial que saca de rotación un camino degradado-pero-vivo.
Precio a confirmar en el dashboard. No hoy: tras 1–2 incidentes reales.

## Runbook de activación (tarjeta)

1. **Confirmar** (1 min): `./planb-check.sh` en el Mini (local o por Tailscale).
   PEERING-ROTO → procede. INTERNET-CAIDO → procede igual (el VPS servirá), pero
   el espejo SAE queda pausado.
2. `ssh deploy@facturador-vps` (por Tailscale — WireGuard directo verificado, no
   depende de Cloudflare).
3. En el VPS: `/opt/facturador/planb-on.sh` — esperar los 4 "Registered tunnel
   connection" en POPs de EE.UU. (~30–60 s).
4. **Cortar el conector del Mini** (NO opcional): `docker stop facturador_tunnel`.
   Verificado en docs de CF: las réplicas **no** hacen traffic steering — un
   conector degradado-pero-vivo sigue recibiendo tráfico; solo el failover de
   conexiones **caídas** está garantizado. Parar el del Mini convierte la
   degradación en falla limpia. Con `restart:unless-stopped` queda parado hasta
   un `docker start` manual.
5. **Verificar como usuario en México** (teléfono con datos, o pedirlo a un
   usuario): app.facturador.mx carga en <1 s. NO validar solo con curl desde el
   Mini: su respuesta regresa por el peering roto y engaña.
6. Avisar a los usuarios y **congelar deploys** mientras dure el plan B
   (`deploy.sh` construye en el Mini; desplegar dejaría al VPS sirviendo código
   viejo contra una BD posiblemente migrada).

## Regreso a la normalidad

1. `./planb-check.sh` da OK sostenido ~15 min (el 9-sep sanó solo en ~1 h).
2. En el Mini: `docker start facturador_tunnel` → esperar sus 4 conexiones
   (vie06/vie07 es lo normal).
3. En el VPS: `planb-off.sh`. Verificar la app, descongelar deploys, y disparar
   «Sincronizar SAE» para el backlog del espejo.

## Reglas del negocio durante un incidente

- El espejo SAE vive en el Mini: en incidente se pausa o arrastra, y las
  remisiones sin confirmación quedan en **BORRADOR**. Eso es la regla dura #1
  funcionando, no una falla. Captura manual de folios después si hace falta.
- Cualquier rotación futura de credenciales (BD, Facturama, túnel) **incluye al
  VPS** — si se rota y no se replica, el plan B muere en silencio.

## Hechos verificados (revisión adversarial, 9-sep-2026)

- **Sostenido**: con el conector del Mini PARADO, Cloudflare manda el tráfico a la
  réplica; réplicas no hacen steering por salud (docs: *tunnel-availability*).
- **Refutado y corregido**: "el config sirve tal cual en Linux" — falta el
  `chmod 644 credentials.json` (uid 65532); y una réplica NO drena tráfico de un
  conector degradado (por eso el paso 4 del runbook es obligatorio).
- **Sostenido**: pooler :6543 aguanta dos backends (max clients 200 vs ~24 usadas);
  ni Supabase ni Facturama filtran por IP de origen.
- **Sostenido**: el camino del VPS us-east→CF no comparte nada con Magyar Telekom,
  y Tailscale MX↔HU sigue vivo durante un incidente de CF (WireGuard directo).
- **Descartado**: egreso por el exit node de la oficina (`oficina-mx`): un Windows
  idle como único camino a producción se apaga con un Windows Update, y no cubre
  la muerte del Mini.

## Decisiones pendientes del dueño

| # | Decisión | Sin ella |
|---|----------|----------|
| 1 | Gasto: VPS ~US$9–12/mes (Hetzner CPX21 Ashburn recomendado) | Solo hay detección: sabrás que está roto sin poder hacer nada |
| 2 | Seguridad: aceptar `.env.prod` + credenciales del túnel en una segunda máquina endurecida | — |
| 3 | Acceso: deploy key de solo lectura al repo | — |
| 4 | Futuro: add-on Load Balancing de CF tras 1–2 incidentes más | El failover sigue siendo manual (2 comandos) |
