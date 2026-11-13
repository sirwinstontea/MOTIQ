param(
    [string]$TokenUrl = 'http://127.0.0.1:8788/session',
    [string]$SimVisionUrl = '',
    [string]$RoboDKPath = 'C:\RoboDK\bin\RoboDK.exe',
    [switch]$PrepareSimulationScene,
    [switch]$CheckOnly
)

# Local desktop stack only. Never reads .env, opens a hardware driver, or submits motion.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$controllerRoot = Join-Path $projectRoot 'robot_controller'
$appRoot = Join-Path $projectRoot 'groky-app'
$pythonExe = Join-Path $controllerRoot '.venv\Scripts\python.exe'
$nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
$children = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$logRoot = Join-Path $env:TEMP ('motiq-stack-' + [guid]::NewGuid().ToString('N'))

function Read-Health([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 3 } catch { return $null }
}

function Test-VoiceServer {
    # OPTIONS does not request a session token or send an API key.
    try {
        $response = Invoke-WebRequest -Uri $TokenUrl -Method Options -UseBasicParsing -TimeoutSec 3 `
            -Headers @{ Origin = 'http://localhost:8081'; 'Access-Control-Request-Method' = 'POST' }
        $allowedOrigin = $response.Headers['Access-Control-Allow-Origin']
        $allowedMethods = $response.Headers['Access-Control-Allow-Methods']
        if ($allowedOrigin -notin @('*', 'http://localhost:8081') -or $allowedMethods -notmatch 'POST|\*') {
            Write-Warning 'Token server responds, but CORS for POST from http://localhost:8081 is not confirmed.'
            return $false
        }
        Write-Host '[OK] Voice token server responds; CORS allows the local app (xAI authentication not tested)'
        return $true
    } catch {
        Write-Warning 'Voice token server is unavailable or its CORS preflight failed. Start the existing server in a separate PowerShell; see bridge/WINDOWS_ROBODK_START.md.'
        return $false
    }
}

function Wait-Service([string]$Name, [string]$Url, $Process) {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $deadline) {
        $reply = Read-Health $Url
        if ($null -ne $reply) { Write-Host "[OK] $Name"; return $reply }
        if ($Process.HasExited) { throw "$Name exited. See logs: $logRoot" }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not become ready at $Url. See logs: $logRoot"
}

function Start-ServiceProcess([string]$Name, [string]$Exe, [string]$Arguments, [string]$Directory) {
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $Directory `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot "$Name.out.log") `
        -RedirectStandardError (Join-Path $logRoot "$Name.err.log")
    $children.Add($p)
    return $p
}

if ($CheckOnly) {
    # Agent tools may have an isolated TEMP. Check the normal Windows TEMP too; never read this file.
    $tokenPaths = @((Join-Path $env:TEMP 'motiq-token-server.cjs'),
                    (Join-Path $env:LOCALAPPDATA 'Temp\motiq-token-server.cjs')) | Select-Object -Unique
    foreach ($path in $tokenPaths) { Write-Host ("Token server file: {0} | exists={1}" -f $path, (Test-Path -LiteralPath $path -PathType Leaf)) }
    Test-VoiceServer | Out-Null
    foreach ($endpoint in @('http://127.0.0.1:8771/api/state', 'http://127.0.0.1:8765/health',
                            'http://127.0.0.1:8080/health', 'http://localhost:8081/status')) {
        $reply = Read-Health $endpoint
        Write-Host ("{0}: {1}" -f $endpoint, $(if ($null -ne $reply) { 'responding' } else { 'unavailable / authentication required' }))
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Missing robot_controller virtual environment.' }
foreach ($port in @(8771, 8765, 8080, 8081)) {
    $probe = [System.Net.Sockets.TcpClient]::new()
    try {
        $probe.Connect('127.0.0.1', $port)
        throw "Port $port is already occupied. Stop the old MOTIQ service before starting this stack."
    } catch [System.Net.Sockets.SocketException] {
        # No listener: available.
    } finally { $probe.Dispose() }
}

New-Item -ItemType Directory -Path $logRoot | Out-Null
Test-VoiceServer | Out-Null
# A fresh ephemeral key matches this Bridge and Expo process, overriding stale local settings without writing .env.
$savedEnv = @{}
$overrides = @{
    'BRIDGE_KEY' = [guid]::NewGuid().ToString('N');
    'REAL_ARM' = '0'; 'SIM' = '1'; 'PYTHONDONTWRITEBYTECODE' = '1';
    'EXPO_PUBLIC_ARM_URL' = 'http://127.0.0.1:8080';
    'EXPO_PUBLIC_CHOOSER_URL' = 'http://127.0.0.1:8771'
}
$overrides['EXPO_PUBLIC_BRIDGE_KEY'] = $overrides['BRIDGE_KEY']
if ($TokenUrl) { $overrides['EXPO_PUBLIC_TOKEN_URL'] = $TokenUrl }
try {
    foreach ($name in $overrides.Keys) {
        $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
    }
    $chooser = Start-ServiceProcess 'chooser' $pythonExe '-m robot_chooser serve --host 127.0.0.1 --port 8771' (Join-Path $projectRoot 'robot-chooser')
    $selection = Wait-Service 'robot-chooser' 'http://127.0.0.1:8771/api/state' $chooser
    if ($selection.report.status -ne 'READY' -or $selection.hardware.backend -ne 'robodk_sim') {
        throw 'Select a READY robot with backend robodk_sim in robot-chooser before starting. No config was changed.'
    }
    Write-Host "Selected robot: $($selection.hardware.active_robot) (RoboDK simulation)"

    $rdkProbe = [System.Net.Sockets.TcpClient]::new()
    try { $rdkProbe.Connect('127.0.0.1', 20500) } catch {
        if (-not (Test-Path -LiteralPath $RoboDKPath)) { throw 'Open RoboDK manually or pass -RoboDKPath.' }
        # RoboDK is deliberately kept open when this script exits; no robot motion is requested.
        Start-Process -FilePath $RoboDKPath -WindowStyle Hidden | Out-Null
        Start-Sleep -Seconds 5
    } finally { $rdkProbe.Dispose() }

    $visionFile = Join-Path $controllerRoot 'runs\vision_bridge.json'
    $detectorArgs = '--detector mock'
    $visionArgs = ''
    if ($SimVisionUrl) {
        if ($SimVisionUrl -notmatch '^http://(?:127\.0\.0\.1|localhost):[0-9]+/?$') {
            throw 'SimVisionUrl must be a local http://127.0.0.1:PORT URL for the existing RoboDK-camera vision server.'
        }
        $detectorArgs = '--detector vision_file --vision-file "' + $visionFile + '"'
        $visionArgs = ' --sim-vision --vision ' + $SimVisionUrl + ' --vision-file "' + $visionFile + '"'
    }
    if ($PrepareSimulationScene) {
        # Explicit station edit, before any executor exists: no robot moves, no station replacement or save.
        Push-Location $controllerRoot
        try {
            & $pythonExe -B -m robot_control.cli prepare-simulation --repair-resting-contacts
            if ($LASTEXITCODE -ne 0) { throw 'Simulation scene preparation refused; inspect its error before starting.' }
        } finally { Pop-Location }
    }
    $controller = Start-ServiceProcess 'controller' $pythonExe ('-m robot_control.cli serve --backend robodk_sim ' + $detectorArgs + ' --host 127.0.0.1 --port 8765') $controllerRoot
    $health = Wait-Service 'robot_controller' 'http://127.0.0.1:8765/health' $controller
    if ($health.backend -ne 'robodk_sim' -or -not $health.simulation) { throw 'Controller is not RoboDK simulation.' }
    $bridgeScript = Join-Path $PSScriptRoot 'motiq_bridge.py'
    $bridge = Start-ServiceProcess 'bridge' $pythonExe ('"' + $bridgeScript + '" --host 127.0.0.1 --port 8080 --controller http://127.0.0.1:8765 --robot-chooser http://127.0.0.1:8771 --sim' + $visionArgs) $projectRoot
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        try {
            $bridgeHealth = Invoke-RestMethod 'http://127.0.0.1:8080/health' -Headers @{ 'X-Bridge-Key' = $env:BRIDGE_KEY } -TimeoutSec 3
        } catch { $bridgeHealth = $null }
        if ($bridgeHealth -and $bridgeHealth.ok) { break }
        if ($bridge.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw "Bridge not ready. Logs: $logRoot" }
        Start-Sleep -Milliseconds 500
    } while ($true)
    Write-Host '[OK] Bridge authenticated; hardware disabled'
    $expoCli = Join-Path $appRoot 'node_modules\expo\bin\cli'
    $expo = Start-ServiceProcess 'expo' $nodeExe ('"' + $expoCli + '" start --web --localhost --port 8081 --clear') $appRoot
    Wait-Service 'Expo web' 'http://localhost:8081/status' $expo | Out-Null
    Write-Host "Open http://localhost:8081 in your browser. Logs: $logRoot"
    Write-Host "Voice endpoint: $TokenUrl (existing server, started in a separate terminal)"
    Write-Host 'Keep this terminal open. Ctrl+C stops only the services started here (not RoboDK).'
    while ($true) {
        foreach ($p in $children) { if ($p.HasExited) { throw "A MOTIQ service exited. Logs: $logRoot" } }
        Start-Sleep -Seconds 1
    }
} finally {
    foreach ($p in $children) { if (-not $p.HasExited) { Stop-Process -Id $p.Id -ErrorAction SilentlyContinue } }
    foreach ($name in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnv[$name], 'Process') }
}
