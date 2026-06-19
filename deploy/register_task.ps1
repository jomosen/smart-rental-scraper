<#
.SYNOPSIS
    Registra (o re-registra) la tarea programada que corre el scraper contra
    producción los Lunes, Miércoles y Viernes.

    Cambia $At para ajustar la hora. Ejecuta:
      powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\jmorell\Proyectos\smart-rental-scraper\deploy\register_task.ps1"
#>

$ErrorActionPreference = 'Stop'

$TaskName = 'RentRadar Scraper Prod'
$At       = '4:00AM'   # <-- cambia la hora aquí si quieres
$Script   = Join-Path (Split-Path $PSScriptRoot -Parent) 'deploy\run_scraper_prod.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $Script)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Wednesday,Friday -At $At
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6)

# -Force re-registra si ya existía (idempotente).
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Tarea '$TaskName' registrada: L/X/V a las $At"
Write-Host "Apunta a: $Script"
Get-ScheduledTask -TaskName $TaskName | Format-List TaskName, State
