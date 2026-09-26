# SAE 9 (Firebird) → Facturador

Un segundo Aspel SAE, **versión 9 sobre Firebird 2.5**, en otro servidor (`vmi2973834`,
Windows Server 2022, Tailscale `100.95.166.85`). El Facturador lo espeja **igual que el
SAE 10**: facturas, cancelaciones, REP, notas de crédito y catálogo de artículos, cada
empresa en su tenant. **Nunca se escribe en este SAE.**

## Qué hay en ese servidor (26-sep-2026)

| Empresa Aspel | Código en el Facturador | RFC | Tenant | Series vistas |
|---|---|---|---|---|
| 01 | **91** | ZAOC830517RF9 (Cristian) | `cristian-gerardo-zarate-orozco` | `FOR K`, `QRO AK` |
| 02 | **92** | ZAOC830517RF9 (Cristian) | `cristian-gerardo-zarate-orozco` | `KELLYSLP`, `KELLYQRO`, `KELLYFORA`, `CP` |
| 03 | — (pendiente) | **desconocido** (sin CFDI en disco; cliente ISS) | — | `AA`, `NC` |
| 04 | **94** | ZAAG5802226VA (Gerardo Alejandro) | `gerardo-alejandro-zarate-alvarez` | `SLPB`, `SLP C` |

- Las bases: `C:\ASPEL\Empresas\Sistemas Aspel\SAE9.00\EmpresaNN\Datos\SAE90EMPRENN.FDB`
  (un archivo por empresa; las tablas llevan el sufijo: `FACTF01`, `CLIE01`…).
- Charset `ISO8859_1`, usuario `SYSDBA` (el de Aspel). Firebird 2.5 escucha en 3050.
- Los clientes de estas empresas **no son los del SAE 10** (confirmado por el dueño).

## Por qué el código es 9x y no el número

La 02 del SAE 9 y la 02 del SAE 10 caen **en el mismo tenant** (mismo RFC). Todo lo que
el Facturador guarda por empresa (`facturas.espejo_empresa`, `claves_sae.empresa`, las
equivalencias `02:CLIENTE`, REP y notas) chocaría. Con el código `9` + último dígito
(01 → 91) cada una queda aparte, y sigue siendo de dos dígitos, que es lo que ya validan
las rutas. Ver `backend/app/services/sae_fuentes.py`.

## Puesta en marcha (en este orden)

### 1. En el servidor (PowerShell como administrador)

Dos cambios que el permiso automático de Claude no pudo aplicar:

- **Whitelist de Tailscale en TSplus.** Su regla "Malicious IP Protection – Block #1"
  bloquea `100.64.0.0–100.127.255.255`, que es todo Tailscale.
- **Firebird cerrado a internet.** La regla `"Allow RDP 3389"` abre TODOS los puertos al
  mundo; con esto el 3050 queda sólo para Tailscale y el propio servidor.

```powershell
& 'C:\Program Files (x86)\TSplus-Security\TSplus-Security.exe' /addwhitelistedip 100.64.0.0-100.127.255.255 "Tailscale - Facturador"
$locales = (Get-NetIPAddress -AddressFamily IPv4).IPAddress
function ToN($s){ $b=[Net.IPAddress]::Parse($s).GetAddressBytes(); [Array]::Reverse($b); [BitConverter]::ToUInt32($b,0) }
function ToS([uint32]$n){ $b=[BitConverter]::GetBytes($n); [Array]::Reverse($b); ([Net.IPAddress]::new($b)).ToString() }
$ex = @(@((ToN '100.64.0.0'),(ToN '100.127.255.255')), @((ToN '127.0.0.0'),(ToN '127.255.255.255')))
foreach ($l in $locales) { $n = ToN $l; $ex += ,@($n,$n) }
$ex = $ex | sort { $_[0] }; $rangos = @(); [uint64]$cur = 0
foreach ($e in $ex) { if ($e[0] -gt $cur) { $rangos += "{0}-{1}" -f (ToS ([uint32]$cur)), (ToS ([uint32]($e[0]-1))) }; if ($e[1] + 1 -gt $cur) { $cur = [uint64]$e[1] + 1 } }
if ($cur -le 4294967295) { $rangos += "{0}-255.255.255.255" -f (ToS ([uint32]$cur)) }
$rangos += '2000::/3'
New-NetFirewallRule -DisplayName 'Firebird 3050 - bloquear internet (Facturador)' -Direction Inbound -Protocol TCP -LocalPort 3050 -RemoteAddress $rangos -Action Block -Profile Any
```

Para deshacer el bloqueo: `Remove-NetFirewallRule -DisplayName 'Firebird 3050 - bloquear internet (Facturador)'`.

Comprobar desde el Mac mini: `nc -vz 100.95.166.85 3050` y `nc -vz 100.95.166.85 22`.

### 2. Variables en `.env.prod` (raíz del checkout de producción)

```bash
SAE_FB_HOST=100.95.166.85
SAE_FB_USER=SYSDBA
SAE_FB_PASSWORD=        # la de Firebird de ese servidor; la pone el dueño, nunca en git
SAE_FB_DESDE=2026-01-01
SAE_FB_EMPRESAS=[{"numero":"01","tenant":"0114d0d2-1e9b-47d1-b5de-5c6062ae94d8"},{"numero":"02","tenant":"0114d0d2-1e9b-47d1-b5de-5c6062ae94d8"},{"numero":"04","tenant":"42731b06-bad0-4b88-93d7-d5277e7a137d"}]
SAE_FB_CADA_SEG=0       # apagado hasta terminar el paso 4
```

`SAE_FB_RUTA`, `SAE_FB_CHARSET` (`ISO8859_1`) y `SAE_FB_PUERTO` (3050) ya traen el valor
correcto por omisión.

### 3. Desplegar

Fusionar el PR y `./deploy.sh` desde `main` idéntico a `origin/main` (ver CLAUDE.md).
Con `SAE_FB_CADA_SEG=0` el reloj del SAE 9 no corre: nada entra solo.

### 4. Dar de alta los clientes (antes de encender el reloj)

El espejo rechaza a propósito la factura de un cliente sin equivalencia `91:CLAVE`. Por
cada empresa, primero **en seco** y luego aplicando:

```
POST /api/v1/sae/fuentes/91/clientes              → el plan (no toca nada)
POST /api/v1/sae/fuentes/91/clientes?aplicar=true → liga por RFC o crea, con espejo encendido
```

Repetir con 92 (tenant de Cristian) y 94 (tenant de Gerardo). Lo que el plan NO aplica
solo, y se resuelve a mano:

- `revisar`: el RFC ya es de un cliente del tenant que factura **nativo** (espejo apagado)
  o que ya vive en **otra empresa de SAE**. Ligarlo le cambiaría cómo se trabaja con él.
- `ambiguo`: varios clientes con el mismo RFC.
- `rfc_invalido`: el SAT no lo aceptaría.

### 5. Encender el reloj y cuadrar

- `SAE_FB_CADA_SEG=300` en `.env.prod` y redeploy. Cada 5 minutos: facturas nuevas y
  cancelaciones, REP y notas; el catálogo cada 4 h; el cuadre una vez al día (repara
  hasta 200 faltantes por serie por día). Todo con relojes **por tenant**: el botón de
  Gerardo no mueve los del SAE 10.
- La primera pasada trae desde el 1-ene-2026 (500 por serie por vuelta) y puede tardar
  unos minutos; mientras, el SAE 10 espera esa vuelta. La cobranza se relee completa
  (desde el piso) una vez al día por empresa, y con el botón.
- Si el SAE 9 no contesta: sonda de 5 s, las demás empresas de ese servidor se saltan en
  esa vuelta y el error se queda en «SAE actualizado» hasta su siguiente vuelta.
- Faltantes de golpe (p. ej. clientes dados de alta tarde):
  `POST /api/v1/sae/fuentes/91/cuadre?tope=2000`.
- El botón «Sincronizar SAE» de cada tenant fuerza todo, SAE 10 y SAE 9.
- Revisar: `GET /api/v1/sae/fuentes` y `POST /api/v1/sae/fuentes/91/cuadre` (desde el tenant de
  cada empresa: la 94, desde el de Gerardo).

### 6. La empresa 03

Averiguar su RFC en la base (`SELECT RFC FROM PARAM_DATOSEMP03`, o el emisor de un XML de
`CFDI03.XML_DOC`). Si es de un tenant existente, agregarla a `SAE_FB_EMPRESAS`; si es de
alguien nuevo (¿Brenda?), primero se da de alta ese tenant.

## Cómo está hecho

- Rutas del SAE 9 en su propio router, `/api/v1/sae/fuentes/...` (`clientes`, `jalar`,
  `cuadre`): pasa un tenant con empresas registradas, y la empresa se resuelve DENTRO de
  su tenant (otra, o una del SAE 10, es 404). Las rutas de `/api/v1/sae/*` que leen el
  SAE 10 del despliegue siguen con el candado de #267: sólo el tenant dueño.

- `services/sae_fuentes.py`: el registro. El SAE 10 sale de las variables de siempre
  (`SAE_SERVER`, `ESPEJO_SAE_*`); el SAE 9 de `SAE_FB_*`.
- `services/sae_lectura.py`: `en_empresa(emp)` hace que todo lo leído dentro del bloque
  salga del `.FDB` de esa empresa (una sola conexión por bloque, transacción READ ONLY /
  READ COMMITTED: el servidor rechaza cualquier escritura y no se frena la limpieza de
  versiones del SAE que se usa por TSplus). Fuera del bloque, el SAE 10 de siempre.
- Cada lector (`espejo_sae`, `cobranza_sae`, `catalogo_inve`) tiene su SQL de Firebird:
  columnas crudas y el formato en Python (fechas del CFDI que en el SAE 9 son TEXTO,
  dinero redondeado a mitades hacia arriba como el `ROUND` de SQL Server).
- Las series con espacio (`FOR K`) se guardan sin espacio (`FORK`): así las parte la CxC y
  así el pago encuentra su factura.
- Un documento con el folio pegado (`KELLYSLP0000000031`) se parte contra las series
  CONOCIDAS de la empresa, y sólo si hay un único corte posible.
- El catálogo del SAE 9 (claves 91/92/94) vive en el tenant, pero el candado del alta y
  la búsqueda de claves del bot miran sólo las empresas del SAE 10 (02-05): al SAE 9
  nunca se le escribe.
- Prueba de punta a punta contra un Firebird 2.5 real con el esquema de un SAE 9:
  `backend/tests/test_sae9_firebird.py` (se salta sin `SAE9_FB_TEST_HOST`).
