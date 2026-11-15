# ---------------------------------------------------------------------------
#  Levanta un tunel HTTPS temporal (cloudflared) hacia la app y reconfigura
#  BASE_URL para que los QR de las credenciales apunten a esa URL publica.
#
#  Uso:
#     powershell -ExecutionPolicy Bypass -File .\scripts\tunel-celular.ps1
#     powershell -ExecutionPolicy Bypass -File .\scripts\tunel-celular.ps1 -SoloTunel
#
#  Detener: cierra la ventana del tunel o mata el proceso cloudflared.
# ---------------------------------------------------------------------------
[CmdletBinding()]
param(
    [int]$Puerto = 8000,
    [switch]$SoloTunel
)

$ErrorActionPreference = 'Stop'

function Write-Paso($texto) { Write-Host "==> $texto" -ForegroundColor Cyan }

# --- 1) Localizar cloudflared ----------------------------------------------
$candidatos = @(
    "$env:ProgramFiles\cloudflared\cloudflared.exe",
    "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\cloudflared.exe"
)
$cf = $candidatos | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cf) {
    $cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
}
if (-not $cf) {
    Write-Host "No se encontro cloudflared. Instalalo con:" -ForegroundColor Red
    Write-Host "  winget install --id Cloudflare.cloudflared -e"
    exit 1
}
Write-Paso "cloudflared: $cf"

# --- 2) Comprobar que la app responde --------------------------------------
try {
    $health = Invoke-RestMethod "http://localhost:$Puerto/api/health" -TimeoutSec 6
    Write-Paso "App local OK (status=$($health.status))"
} catch {
    Write-Host "La app no responde en http://localhost:$Puerto ." -ForegroundColor Red
    Write-Host "Levantala primero con:  docker compose up -d"
    exit 1
}

# --- 3) Arrancar el tunel y leer la URL ------------------------------------
$log = Join-Path $env:TEMP "cloudflared-asistencia.log"
Remove-Item $log -ErrorAction SilentlyContinue

Write-Paso "Abriendo tunel HTTPS..."
Start-Process -FilePath $cf -WindowStyle Hidden -ArgumentList @(
    'tunnel', '--url', "http://localhost:$Puerto", '--no-autoupdate',
    '--logfile', $log, '--loglevel', 'info'
)

$url = $null
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches
        if ($m) { $url = ($m.Matches | Select-Object -First 1).Value; break }
    }
}

if (-not $url) {
    Write-Host "No se pudo obtener la URL del tunel. Ultimas lineas del log:" -ForegroundColor Red
    if (Test-Path $log) { Get-Content $log -Tail 15 }
    exit 1
}

Write-Host ""
Write-Host "  TUNEL LISTO:  $url" -ForegroundColor Green
Write-Host ""

# --- 4) Reapuntar BASE_URL y reiniciar la app ------------------------------
if (-not $SoloTunel) {
    Write-Paso "Reconfigurando BASE_URL y reiniciando la app..."
    $env:BASE_URL = $url
    Push-Location (Join-Path $PSScriptRoot '..')
    try {
        docker compose up -d
    } finally {
        Pop-Location
    }
    Write-Paso "Listo. Genera (o regenera) las credenciales para que el QR use esta URL."
} else {
    Write-Host "Modo -SoloTunel: no se toco BASE_URL. Para aplicarlo manualmente:" -ForegroundColor Yellow
    Write-Host "  `$env:BASE_URL = `"$url`"; docker compose up -d"
}

Write-Host ""
Write-Host "Abre esta direccion desde el celular (con datos o la misma Wi-Fi):" -ForegroundColor Cyan
Write-Host "  $url" -ForegroundColor White
Write-Host ""
Write-Host "Para detener el tunel:" -ForegroundColor DarkGray
Write-Host "  Get-Process cloudflared | Stop-Process" -ForegroundColor DarkGray
