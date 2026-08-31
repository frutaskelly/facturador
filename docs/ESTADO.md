# Estado del proyecto — cierre del 31/08/2026

Lo reescribe `/endworking` al cerrar el día. Punto de entrada para retomar: basta abrir esta
carpeta y leer este archivo.

## Git

| | |
|---|---|
| Rama base | `main` @ `3a17f01` — igual que `origin/main` |
| Remoto | `frutaskelly/facturador` |
| Working tree | limpio |
| Worktrees | 1 (`new-session-7acb50`, rama ya fusionada — podable) |
| PRs abiertos | ninguno |
| Migración head | `0057_export_rastro_portal_rbac` |

`Cristian/smartsupply-v2.0` es un enlace simbólico a esta carpeta, no otro clon.

## Deploy

**En vivo en https://facturador.mx** y al día: se comprobó **dentro del contenedor** que
`backend/app/api/v1/facturas.py` trae el parámetro de búsqueda `q` que introdujo el PR #47, el
commit más reciente de `main`. Los cinco contenedores (`frontend`, `backend`, `landing`,
`tunnel`, `redis`) están arriba y sanos, y los cuatro propios se construyen desde **este**
checkout, no desde un worktree.

⚠️ **La marca de tiempo de la imagen no sirve para saber si el deploy está al día.** Una imagen
construida segundos antes de fusionar un PR "parece" atrasada sin estarlo, y una imagen vieja
cuyo contexto de build no cambió no está atrasada en absoluto. Lo único que decide es mirar el
contenido dentro de la imagen.

**La puerta de `deploy.sh` bloquea si el checkout no coincide con GitHub** — incluidos archivos
sin rastrear. Por eso este `docs/ESTADO.md` va commiteado: suelto, detiene cada deploy. Nunca
usar `FORCE=1` para saltarla: publica el trabajo a medias de otra sesión.

## Lo que se cerró el 29–31 de agosto (PRs #40 a #47)

**El export a SAE ya no inventa folios.** Era el problema de fondo: el sistema estampaba
`serie + folio` al generar el Excel masivo, aunque ese Excel nunca se subiera al SAE. Quedaban
remisiones "RESERVADAS" contra facturas que no existían (así nació la ZHGO 588 fantasma). Ahora
`remisiones.export_sae_at` y `export_sae_folio` son **solo rastro**; el campo `factura_sae` lo
escribe únicamente el espejo del SAE o una captura manual. Sin confirmación del SAE, la remisión
se queda en BORRADOR.

**El espejo liga las facturas por su OC.** Dos formatos conviven: `OC <folio>` en las
observaciones, y el folio interno al final de la línea sin prefijo (así escriben EHMO y MAFAN).
El cruce exige candidata única e importes dentro de tolerancia; con eso se ligaron 39 remisiones
que estaban sueltas (de 99 a 138).

**Conciliación automática.** Facturador ↔ SAE: 148 = 148, cero diferencias. Master ↔ bandeja:
se encontraron 61 órdenes que solo vivían en el Master y se recuperaron todas; ahora se revisa
cada 6 h y solo avisa cuando hay excepción.

**Roles por menú y portal de cliente.** Permisos ver/editar/borrar por pantalla, rol preset
PORTAL CLIENTE, y `memberships.cliente_scope` para limitar qué empresas ve un usuario. El
alcance se aplica como filtro explícito: RLS aísla el tenant, no al cliente, y OWNER se salta
los permisos pero **no** el alcance. Una revisión adversarial cerró 8 fugas por las que el
portal alcanzaba datos de otros clientes.

**Cotizador.** Sube PDF/foto/Excel y devuelve la cotización completa; solo cotiza productos que
estén en la lista de precios del cliente, y lo que no cruza sale marcado con su motivo. Los
reportes usan el membrete y el layout de Smart Supply (logo del tenant, azul `#305496`, folio
de página).

**Formato de fecha del masivo.** Aspel lee la columna FECHA con la configuración regional del
Windows que importa, así que el formato es configurable (`SAE_FORMATO_FECHA`, por omisión
`%m/%d/%Y`) y se ve un ejemplo antes de importar. Con DD/MM se corrieron fechas en silencio
(ZHGO 312/324/335/365-369, una ya timbrada).

**Estructura del SAE, verificada.** La empresa 01 **no existe**; 02, 03, 04 y 05 emiten todas
con el mismo RFC que el Facturador y con CSD vigente, así que **no hace falta multiemisor**.

## Cómo verificar "ya fusionado"

Los PRs entran con **squash**: los commits de la rama nunca aparecen en `main` por SHA, así que
`rev-list --count`, `git cherry` y el `diff` de tres puntos **los tres mienten**. Lo que decide
es el `headRefOid` del PR contra el tip local, comparar el **contenido** de los archivos, o el
**patch-id** cuando se sospecha trabajo duplicado. Ejemplo del 31-ago: la rama
`cotizador-documentos` no coincidía con la cabeza de su PR #41 porque se rebasó y entró por el
#42; se confirmó por contenido (los cinco marcadores que introducía están en `main`) antes de
podarla.

## Pendientes

1. **51 textos de producto sin cruzar** — necesitan ojo humano; ninguna automatización los va a
   resolver sola.
2. **ZMAFAN 167: cancelación en proceso en el SAT** (verificado 31-ago en CFDI02: «Cancelación
   enviada al SAT», motivo 02, acuse recibido). Por el importe, el receptor debe aceptarla en su
   buzón — 3 días hábiles, o se cancela por plazo vencido. No requiere acción: el espejo la
   marcará CANCELADA solo cuando SAE reciba `FECHA_CANCELA`/`STATUS='C'`. Sí queda el descuadre
   contra la remisión que la sustituye (RZMAFAN4): $4,914.46 facturados contra $12,017.36 —
   revisar antes de refacturar.
3. **Confirmar el formato de fecha en la primera importación real** del masivo, con el Windows
   que de hecho la importa.
4. **Onboarding de los 4 clientes nuevos del SAE** (más la tercera operación de EHMO), según las
   Etapas A–E de `PLAN-onboarding-clientes-sae.md`. Los grupos de WhatsApp ya están
   configurados pero en `activo: false`.
5. **Extender `SERIES_POR_EMPRESA` del conector a las empresas 04 y 05** — hoy solo cubre 02
   y 03.
