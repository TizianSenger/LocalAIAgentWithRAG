# register_task.ps1
# -----------------
# Registers a Windows Task Scheduler job that runs update.py every day at 19:00.
# Run this script ONCE as Administrator:
#   Right-click PowerShell → "Run as administrator"
#   cd C:\Users\Sim2\Documents\GitHub\LocalAIAgentWithRAG
#   .\register_task.ps1

$TaskName   = "natMSS-VaultUpdate"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe  = (Get-Command python | Select-Object -ExpandProperty Source)
$UpdateScript = Join-Path $ScriptDir "update.py"

# Remove existing task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Existing task removed."
}

$Action  = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$UpdateScript`"" -WorkingDirectory $ScriptDir
$Trigger = New-ScheduledTaskTrigger -Daily -At "19:00"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action   $Action `
    -Trigger  $Trigger `
    -Settings $Settings `
    -Description "Daily natMSS repo index into Obsidian vault" `
    -RunLevel Highest

Write-Host ""
Write-Host "Task '$TaskName' registered successfully."
Write-Host "It will run every day at 19:00."
Write-Host ""
Write-Host "To trigger manually right now:"
Write-Host "  python `"$UpdateScript`""
Write-Host ""
Write-Host "To remove the task later:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
