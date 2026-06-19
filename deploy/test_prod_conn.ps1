<#
.SYNOPSIS
    Prueba la conexión a la BBDD de PRODUCCIÓN por el túnel SSH (sin scrapear).
    Abre el túnel, lista los providers de prod y cierra el túnel.

    Uso:
      powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\jmorell\Proyectos\smart-rental-scraper\deploy\test_prod_conn.ps1"
#>

$ErrorActionPreference = 'Stop'

$Repo    = Split-Path $PSScriptRoot -Parent
$EnvFile = Join-Path $Repo '.env.prod'
$Python  = Join-Path $Repo '.venv\Scripts\python.exe'

# ── Cargar .env.prod ──────────────────────────────────────────────────────────
if (-not (Test-Path $EnvFile)) { throw ".env.prod no encontrado en $EnvFile" }
foreach ($raw in Get-Content $EnvFile) {
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }
    $idx = $line.IndexOf('=')
    if ($idx -lt 1) { continue }
    Set-Item -Path ("env:" + $line.Substring(0, $idx).Trim()) -Value $line.Substring($idx + 1).Trim()
}

$VpsHost    = $env:VPS_SSH_HOST
$VpsUser    = $env:VPS_SSH_USER
$LocalPort  = if ($env:TUNNEL_LOCAL_PORT)  { $env:TUNNEL_LOCAL_PORT }  else { '15433' }
$RemotePort = if ($env:TUNNEL_REMOTE_PORT) { $env:TUNNEL_REMOTE_PORT } else { '5433' }

# Guardarraíl: debe apuntar al túnel, no a la BBDD local.
if ($env:SUPER_DATABASE_URL -notmatch ":$LocalPort/") {
    throw "ABORTADO: SUPER_DATABASE_URL no usa el puerto del túnel ($LocalPort). Revisa .env.prod."
}

# ── Abrir túnel ───────────────────────────────────────────────────────────────
$sshArgs = @('-N','-o','ExitOnForwardFailure=yes','-o','BatchMode=yes',
             '-L', "${LocalPort}:127.0.0.1:${RemotePort}", "${VpsUser}@${VpsHost}")
if ($env:VPS_SSH_KEY) { $sshArgs = @('-i', $env:VPS_SSH_KEY) + $sshArgs }

Write-Host "Abriendo túnel a ${VpsUser}@${VpsHost} (local ${LocalPort} -> remoto ${RemotePort})…"
$Tunnel = Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -PassThru -WindowStyle Hidden

try {
    $deadline = (Get-Date).AddSeconds(20)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($Tunnel.HasExited) { throw "El túnel SSH se cerró (exit $($Tunnel.ExitCode)). ¿Clave/host correctos?" }
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', [int]$LocalPort)
            if ($c.Connected) { $ready = $true; $c.Close(); break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "El túnel no quedó listo en 20s." }
    Write-Host "Túnel listo. Consultando providers en prod…`n"

    $url = ($env:SUPER_DATABASE_URL -replace '\+psycopg', '')
    & $Python -c "import sys,psycopg; c=psycopg.connect(sys.argv[1]); print('providers:', c.execute('select code from providers order by code').fetchall()); print('pvcs_clasificadas:', c.execute('select count(*) from provider_vehicle_categories where acriss_code is not null and active').fetchone()[0])" $url
}
finally {
    if ($Tunnel -and -not $Tunnel.HasExited) {
        Write-Host "`nCerrando túnel…"
        Stop-Process -Id $Tunnel.Id -Force
    }
}
