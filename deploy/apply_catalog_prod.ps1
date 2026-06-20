<#
.SYNOPSIS
    Aplica el catálogo ACRISS y reclasifica los PVCs en PRODUCCIÓN, por el túnel.
    Abre el túnel SSH, ejecuta seed_acriss_codes.py + reclassify_all.py contra
    prod, y cierra el túnel. La reclasificación llama a Gemini (coste).
#>
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Repo    = Split-Path $PSScriptRoot -Parent
$EnvFile = Join-Path $Repo '.env.prod'
$Python  = Join-Path $Repo '.venv\Scripts\python.exe'

foreach ($raw in Get-Content $EnvFile) {
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }
    $idx = $line.IndexOf('='); if ($idx -lt 1) { continue }
    Set-Item -Path ("env:" + $line.Substring(0, $idx).Trim()) -Value $line.Substring($idx + 1).Trim()
}
$LocalPort  = if ($env:TUNNEL_LOCAL_PORT)  { $env:TUNNEL_LOCAL_PORT }  else { '15433' }
$RemotePort = if ($env:TUNNEL_REMOTE_PORT) { $env:TUNNEL_REMOTE_PORT } else { '5433' }
if ($env:SUPER_DATABASE_URL -notmatch ":$LocalPort/") {
    throw "ABORTADO: SUPER_DATABASE_URL no usa el puerto del túnel ($LocalPort)."
}

$sshArgs = @('-N','-o','ExitOnForwardFailure=yes','-o','BatchMode=yes',
             '-L', "${LocalPort}:127.0.0.1:${RemotePort}", "$($env:VPS_SSH_USER)@$($env:VPS_SSH_HOST)")
if ($env:VPS_SSH_KEY) { $sshArgs = @('-i', $env:VPS_SSH_KEY) + $sshArgs }
Write-Host "Abriendo túnel a $($env:VPS_SSH_USER)@$($env:VPS_SSH_HOST) (local $LocalPort)…"
$Tunnel = Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -PassThru -WindowStyle Hidden
try {
    $deadline = (Get-Date).AddSeconds(20); $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($Tunnel.HasExited) { throw "Túnel cerrado (exit $($Tunnel.ExitCode))." }
        try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', [int]$LocalPort); if ($c.Connected) { $ready=$true; $c.Close(); break } } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "Túnel no listo en 20s." }
    Set-Location $Repo
    Write-Host "`n=== seed_acriss_codes.py (prod) ==="
    & $Python scripts/seed_acriss_codes.py
    Write-Host "`n=== reclassify_all.py (prod) ==="
    & $Python scripts/reclassify_all.py
}
finally {
    if ($Tunnel -and -not $Tunnel.HasExited) { Write-Host "`nCerrando túnel…"; Stop-Process -Id $Tunnel.Id -Force }
}
