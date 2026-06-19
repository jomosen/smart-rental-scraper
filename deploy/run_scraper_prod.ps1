<#
.SYNOPSIS
    Corre el scraper local apuntando a la BBDD de PRODUCCIÓN, gestionando el
    túnel SSH de forma efímera (lo abre, scrapea y lo cierra).

.DESCRIPTION
    Pensado para Windows Task Scheduler (L/X/V). Flujo:
      1. Carga las variables de .env.prod en el entorno del proceso.
      2. Comprueba (guardarraíl) que apuntan al túnel, no a la BBDD local.
      3. Abre el túnel SSH al Postgres del VPS.
      4. Espera a que el puerto local responda y lanza el scraper.
      5. Cierra el túnel SIEMPRE (incluso si el scrape falla).

    La autenticación SSH debe ser por clave sin passphrase (o vía ssh-agent),
    porque corre desatendido.

    Invocación desde Task Scheduler:
      powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\jmorell\Proyectos\smart-rental-scraper\deploy\run_scraper_prod.ps1"
#>

$ErrorActionPreference = 'Stop'

# ── Forzar UTF-8 (logs legibles: € y acentos correctos) ───────────────────────
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# ── Rutas ─────────────────────────────────────────────────────────────────────
$Repo    = Split-Path $PSScriptRoot -Parent
$EnvFile = Join-Path $Repo '.env.prod'
$Python  = Join-Path $Repo '.venv\Scripts\python.exe'
$LogDir  = Join-Path $Repo 'logs'
$Stamp   = Get-Date -Format 'yyyyMMdd_HHmmss'
$Log     = Join-Path $LogDir "scraper_prod_$Stamp.log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Write-Log {
    param([string]$Msg)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Msg
    Write-Host $line
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

# ── 1. Cargar .env.prod en el entorno del proceso ─────────────────────────────
if (-not (Test-Path $EnvFile)) { throw ".env.prod no encontrado en $EnvFile" }
Write-Log "Cargando $EnvFile"
foreach ($raw in Get-Content $EnvFile) {
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }
    $idx = $line.IndexOf('=')
    if ($idx -lt 1) { continue }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim()
    Set-Item -Path "env:$key" -Value $val
}

# ── Config del túnel (desde .env.prod) ────────────────────────────────────────
$VpsHost    = $env:VPS_SSH_HOST
$VpsUser    = $env:VPS_SSH_USER
$LocalPort  = $env:TUNNEL_LOCAL_PORT
$RemotePort = $env:TUNNEL_REMOTE_PORT
if (-not $VpsHost -or $VpsHost -eq 'IP_O_DOMINIO_DEL_VPS') { throw "VPS_SSH_HOST sin configurar en .env.prod" }
if (-not $LocalPort)  { $LocalPort  = '15433' }
if (-not $RemotePort) { $RemotePort = '5433' }

# ── 2. Guardarraíl: las URLs deben apuntar al túnel, NO a la BBDD local ────────
# Evita el peor error posible: escribir en la BBDD local creyendo que es prod.
if ($env:SUPER_DATABASE_URL -notmatch ":$LocalPort/") {
    throw "ABORTADO: SUPER_DATABASE_URL no apunta al puerto del túnel ($LocalPort). Revisa .env.prod."
}
Write-Log "Guardarraíl OK: SUPER_DATABASE_URL usa el puerto $LocalPort (túnel)."

# ── 3. Abrir el túnel SSH ─────────────────────────────────────────────────────
$sshArgs = @(
    '-N',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'BatchMode=yes',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-L', "${LocalPort}:127.0.0.1:${RemotePort}",
    "${VpsUser}@${VpsHost}"
)
if ($env:VPS_SSH_KEY) { $sshArgs = @('-i', $env:VPS_SSH_KEY) + $sshArgs }

Write-Log "Abriendo túnel SSH a ${VpsUser}@${VpsHost} (local ${LocalPort} -> remoto ${RemotePort})"
$Tunnel = Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -PassThru -WindowStyle Hidden

try {
    # ── 4. Esperar a que el puerto local responda ─────────────────────────────
    $deadline = (Get-Date).AddSeconds(20)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($Tunnel.HasExited) { throw "El túnel SSH se cerró inesperadamente (exit $($Tunnel.ExitCode)). ¿Clave/host correctos?" }
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', [int]$LocalPort)
            if ($c.Connected) { $ready = $true; $c.Close(); break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "El túnel no quedó listo en 20s." }
    Write-Log "Túnel listo. Lanzando scraper…"

    # ── 5. Correr el scraper ──────────────────────────────────────────────────
    Set-Location $Repo
    & $Python -m src.scraper.presentation.cli.main 2>&1 | ForEach-Object {
        Write-Host $_
        Add-Content -Path $Log -Value $_ -Encoding UTF8
    }
    $code = $LASTEXITCODE
    Write-Log "Scraper finalizó con código $code"
    exit $code
}
finally {
    if ($Tunnel -and -not $Tunnel.HasExited) {
        Write-Log "Cerrando túnel SSH (pid $($Tunnel.Id))"
        Stop-Process -Id $Tunnel.Id -Force
    }
}
