# Registrira Windows Task Scheduler zadatak koji svaki sat pokreće scraper.
#
#   powershell -ExecutionPolicy Bypass -File register_task.ps1
#
# Ukloniti zadatak:
#   Unregister-ScheduledTask -TaskName 'KvalitetaZraka-Zapresic' -Confirm:$false

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = 'KvalitetaZraka-Zapresic'

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\run_scrape.ps1`"" `
    -WorkingDirectory $root

# Mjerenja se osvježavaju na puni sat i budu dostupna nekoliko minuta kasnije,
# pa se čita u :39 svakog sata.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(39) `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName `
    -Description 'Dohvat parametara kvalitete zraka (Zapresic, smart-airq widgeti)' `
    -Action $action -Trigger $trigger -Settings $settings `
    -Force | Out-Null

Write-Host "Zadatak '$taskName' registriran: svaki sat u :39."
Write-Host "Provjera:  Get-ScheduledTask -TaskName '$taskName'"
Write-Host "Test:      Start-ScheduledTask -TaskName '$taskName'"
