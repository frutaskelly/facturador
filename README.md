# Facturador

Facturación electrónica CFDI 4.0 con IA — plataforma SaaS multi-tenant
gobierno–alimentos. Rebuild limpio de v1 (`cadena-de-suministro-ai`) con el plan
de acción de seguridad incorporado desde el día 1.

## Por qué v2

v1 era un prototipo avanzado corriendo como producción. v2 hereda el código
bueno módulo por módulo y corrige los problemas de raíz:

| # | Problema en v1 | Solución en v2 |
|---|----------------|----------------|
| 🔴 | ~155/187 endpoints confiaban en el header `x-tenant-id` (sin JWT) | **Todo** endpoint deriva identidad del JWT y `tenant_id` del membership validado. Nunca del header. |
| 🔴 | RLS anulado (GUC seteado desde el header del atacante) | `app.current_tenant_id` se setea desde el JWT; conexión con rol sin `BYPASSRLS`. |
| 🟠 | `next dev` servido a producción; `next build` roto | `next build && next start` (standalone). El build es un gate real. |
| 🟠 | Sin CI, sin gate de tipos | GitHub Actions: `alembic upgrade head` + `pytest` + build con type-check. |
| 🟡 | Código muerto (Chat AI, WhatsApp, Agentes, impersonación) | Eliminado del alcance. |

## Arquitectura

- **Backend** — FastAPI. Auth = verificación de JWT de Supabase contra el
  JWKS del proyecto (**ES256**, asimétrico; sin secreto compartido en el
  backend).
- **Frontend** — Next.js 16 (App Router, React 19, Tailwind v4), salida
  `standalone`.
- **Datos** — Postgres (local en dev vía Docker; Supabase Postgres en la nube).
  RLS por tenant en cada tabla, keyed en `public.current_tenant_id()`.
- **Auth/Identidad** — Supabase Auth. Onboarding provisionado por operador
  (sin self-signup abierto).

### Modelo de seguridad (el punto de v2)

Lo único que un request puede probar es *"soy el auth user `<sub>`"* (firma JWT
verificada). Tenant, rol y permisos se resuelven en el servidor a partir de esa
identidad — jamás desde headers del cliente.

## Puertos (no chocan con v1)

| Servicio  | v1   | v2   |
|-----------|------|------|
| Postgres  | 5432 | 5434 |
| Redis     | 6379 | 6380 |
| Backend   | 8001 | 8011 |
| Frontend  | 3002 | 3012 |

## Desarrollo local

```bash
cp .env.example .env        # llenar secretos (gitignored)
docker compose up --build   # backend :8011, frontend :3012, pg :5434, redis :6380
```

### Backend sin Docker

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5434/smartsupply_v2
alembic upgrade head
pytest
uvicorn app.main:app --reload --port 8000
```

### Frontend sin Docker

```bash
cd frontend
npm ci
npm run build && npm start
```

## Migraciones

```bash
cd backend
alembic revision -m "descripcion"      # crear
alembic upgrade head                   # aplicar (dev)
ALEMBIC_DB_URL="$SUPABASE_DB_URL" alembic upgrade head   # aplicar a la nube
```

## Fases del rebuild

1. ✅ **Cimientos** — repo, docker-compose, config + auth JWKS, CI, baseline RLS.
2. ✅ **Núcleo seguro** — schema (tenants/users/memberships/roles), RBAC, RLS.
3. ✅ **Catálogo** — productos, categorías, esquemas, listas de precios, clientes.
4. ✅ **Operaciones** — proveedores, almacenes, inventario (lotes + kardex + mermas),
   órdenes de compra, conversiones, remisiones (núcleo). Difiere a fases
   posteriores: overlay POS de la remisión (Fase 5), serie fiscal del folio +
   acoplamiento a facturas (Fase 6).
5. ⏳ **POS** — pedido → caja → almacén → salida + tracking + devoluciones.
6. **Fiscal** — facturas + CFDI 4.0 + series.
7. **Pulido** — dashboard, sistema de diseño, ESLint, tests, CI verde.

## Alcance

**Incluye:** POS, Dashboard, Facturas/CFDI, Remisiones/Conversión, Inventario,
Órdenes de compra, Productos/Categorías/Esquemas/Listas, Clientes/CRM,
Usuarios/Roles (IAM), Series fiscales, Sistema de diseño.

**Excluye (eliminado de v1):** Chat AI, WhatsApp, Agentes, Documentos,
`/admin/tenants` + impersonación.
