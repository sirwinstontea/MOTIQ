# Inicio local de MOTIQ en Windows

Usa dos terminales: una exclusiva para xAI y el servidor de tokens existente; otra para RoboDK, Controller,
Bridge, robot-chooser y Expo. No se conecta hardware ni se ejecutan movimientos durante el arranque.

## 1. Terminal de Grok Voice

En PowerShell normal, sin transcripción de sesión, comprueba el archivo existente:

```powershell
node --version
Test-Path -LiteralPath "$env:TEMP\motiq-token-server.cjs"
```

Se comprobó Node v26.13.1 y la existencia del archivo en
`C:\Users\josea\AppData\Local\Temp\motiq-token-server.cjs`. Las herramientas pueden usar un TEMP aislado;
si devuelve `False`, comprueba también:

```powershell
Test-Path -LiteralPath "$env:LOCALAPPDATA\Temp\motiq-token-server.cjs"
```

Si ambos devuelven `False`, recupera el archivo existente de su ubicación original. No se crea otro servidor.
Si ya está escuchando en 8788, no abras una segunda instancia.

Introduce la clave manualmente, sin pegarla en el chat ni escribirla como literal en un comando:

```powershell
$secureKey = Read-Host "Introduce tu API Key de xAI" -AsSecureString
try {
    $env:XAI_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
} finally {
    Remove-Variable secureKey -ErrorAction SilentlyContinue
}
try {
    node "$env:TEMP\motiq-token-server.cjs"
} finally {
    Remove-Item Env:XAI_API_KEY -ErrorAction SilentlyContinue
}
```

Si el archivo está únicamente en el TEMP normal, sustituye solo la línea `node` por:

```powershell
node "$env:LOCALAPPDATA\Temp\motiq-token-server.cjs"
```

Mantén esta terminal abierta. La clave solo se entrega al servidor Node mediante el entorno de esa terminal.
No inicies MOTIQ desde ella: los otros procesos no deben heredar `XAI_API_KEY`. Ctrl+C detiene Node y el bloque
`finally` elimina la variable de esa terminal. No guardes la clave en archivos, `.env`, logs ni Git.

El servidor existente escucha en **http://127.0.0.1:8788/session**, acepta POST y solicita tokens temporales
a xAI mediante `/v1/realtime/client_secrets`. Debe permitir CORS desde http://localhost:8081.

## 2. Terminal de MOTIQ

Cierra las terminales antiguas de Controller/Bridge/Expo para liberar los puertos. RoboDK puede permanecer abierto.
En una segunda terminal PowerShell:

```powershell
cd C:\Users\josea\GROK_HACK\MOTIQ
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bridge\start_robodk.ps1
```

También puedes usar **Terminal → Run Task → MOTIQ: RoboDK local stack** en VS Code. No abras ese VS Code desde
la terminal que contiene la clave.

El launcher configura para Expo **http://127.0.0.1:8788/session**, solo en memoria. No lee ni modifica la API key,
el archivo del servidor ni `.env`. Comprueba OPTIONS/CORS sin solicitar ni imprimir tokens; si falla, avisa y puede
iniciar igualmente la simulación.

| Servicio | Puerto | Función |
| --- | --- | --- |
| Node existente, terminal 1 | 8788 | Tokens de Grok Voice |
| robot-chooser | 8771 | Robot seleccionado y configuración READY |
| RoboDK | 20500 | API de simulación |
| Robot Controller | 8765 | Validación y ejecución en robodk_sim |
| MOTIQ Bridge | 8080 | Herramientas autenticadas de la app |
| Expo web | 8081 | App local |

El launcher conserva el robot seleccionado y exige `READY` con backend `robodk_sim`. Comprueba cada servicio
antes del siguiente y configura una clave efímera compartida entre Bridge y Expo sin escribirla en archivos.
Mantén abierta esta terminal. Ctrl+C cierra sus servicios; deja abiertos RoboDK y el servidor de tokens separado.
Los logs de simulación van a una carpeta temporal `motiq-stack-*` indicada en la terminal.

## 3. Comprobar sin mover robots

1. Espera `[OK]` y abre **http://localhost:8081**, no una versión antigua desplegada en HTTPS.
2. Recarga tras reiniciar Expo y permite el micrófono.
3. En Robots, confirma **Arm online**, el modelo seleccionado y `simulation: true`.
4. Activa el micrófono y di «Hola», sin pedir movimientos. Solo una conversación confirma la autenticación
   completa; OPTIONS correcto no demuestra que xAI haya aceptado la clave.
5. Si falla, revisa si el servidor de tokens está disponible o devuelve un error de autenticación. No publiques
   respuestas de tokens, claves ni volcados de variables de entorno.

Diagnóstico sin iniciar servicios, pedir tokens ni mover robots:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bridge\start_robodk.ps1 -CheckOnly
```

Puedes cambiar la dirección con `-TokenUrl http://127.0.0.1:8788/session`, sin modificar `.env`.
El launcher es para navegador en el mismo PC; un móvil necesita URLs LAN y otra configuración.

## Rechazos del workspace y recuperación

Para destinos en toda la mesa, empujes con distancia/dirección y paths por puntos o círculos, consulta
[Table contact and paths](../robot_controller/docs/TABLE_CONTACT_PATHS.md). La política explícita
`collision_policy: table_and_tagged` conserva colisiones con mesa, objetos protegidos, geometría desconocida
y otros robots; permite contacto con objetos manipulables registrados sin tag. Requiere aplicar robot-chooser,
reiniciar MOTIQ y abrir una sesión Voice nueva. No desactives globalmente las colisiones en RoboDK.

En modo `box`, el workspace es una caja operacional configurada por robot-chooser, no todo el alcance cinemático del robot.
La app muestra los límites que el controlador tiene **en memoria**, en metros y marco base. Por ejemplo, el
catálogo demo xArm5 usa Y entre -0.29 y +0.29 m: desde Y=0, pedir 30 cm a la izquierda apunta a Y=0.30 y se
rechaza, aunque la distancia sea potencialmente alcanzable. El rechazo no significa que el robot se haya movido.
IK y colisiones también deben aprobarse aunque el destino esté dentro de la caja.

En modo `kinematic` ya no se aplican esa caja lateral ni el techo Z: RoboDK decide el alcance real mediante IK,
límites articulares y la comprobación completa de colisiones. Se conserva el mínimo Z del TCP (15 mm en la demo).
El modo se guarda con `robot_chooser set workspace_mode kinematic`; requiere reiniciar MOTIQ y una sesión de voz
nueva. Pedir 50 cm no se rechaza por la proyección de la mesa, pero puede rechazarse si el camino real es inviable.

Si aparece FAULT o CANCELLED, en **Robots → Arm → Reset simulation fault** puedes solicitar recuperación explícita.
También puedes decir **"Reset simulation fault"** o **"Reinicia el fallo de simulación"**. Voice propondrá un plan
de solo reset y pedirá confirmación. Tras tu sí, llama `arm_reset_simulation_fault` y comunica el resultado real.
No debes repetir el movimiento fallido como parte del reset; cualquier movimiento posterior requiere otra petición
y aprobación. Los resets no se guardan como skills de movimiento.
Solo se admite `robodk_sim`, sin tareas activas ni robot ocupado, con feedback fresco, modo/frame/TCP válidos y
posición/articulaciones dentro de límites. No mueve, no modifica el workspace, no reintenta el comando y no borra
su registro histórico. Una configuración o posición todavía inválida mantiene el rechazo.

Tras seleccionar otro robot, reinicia **solo la terminal MOTIQ** (Ctrl+C y el comando de inicio). La terminal de
tokens puede permanecer abierta. La selección en disco no cambia el modelo de un controlador que sigue ejecutándose.
No amplíes los límites solo para evitar una negativa: configura el workspace por robot en robot-chooser y verifica
la celda de simulación. El catálogo usa `default_workspace`; los cambios de celda usan `workspace_overrides` en
`robot-chooser/hardware.yaml` y requieren aplicar la configuración y reiniciar el controlador.

Si falta la estación, con RoboDK abierto y sin controlador activo:

```powershell
cd robot_controller
.venv\Scripts\python.exe -m robot_control.cli setup-station
```

Este comando crea una celda demo. No reemplaces una estación personalizada sin guardar sus cambios.
No conecta hardware ni ejecuta movimientos; verifica la geometría antes de cualquier futura prueba de movimiento.
# Repair a legacy simulation station before starting

If an old station has disabled object/table pairs or objects exactly touching the table, stop the MOTIQ stack
terminal (not the token-server terminal), open RoboDK, and run from the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bridge\start_robodk.ps1 -PrepareSimulationScene
```

This explicit option performs station-only setup before the controller starts: restores required table protection
and adjusts unheld upright objects at surface contact to 1 mm clearance. No robot movements or automatic task
retries run. Attached objects, deep penetration, invalid footprints and remaining collisions refuse setup; do not
disable table checking to bypass them. Station edits are not automatically saved. Every normal controller startup
also reapplies/verifies the required object/table pairs, so opening an older saved map cannot silently disable them.
See [pickup/placement recovery](../robot_controller/docs/PICK_PLACE_RECOVERY.md) for constraints and voice checks.
