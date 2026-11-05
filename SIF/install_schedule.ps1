[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateRange(1, 1440)]
    [int]$IntervalMinutes = 60,
    [string]$TaskName = 'SIF Factsheet Monitor',
    [string]$PythonPath,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'output')
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'monitor.py'
$configPath = Join-Path $PSScriptRoot 'official_sources.json'
if (-not $PythonPath) {
    $PythonPath = (& python -c 'import sys; print(sys.executable)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python could not be found.' }
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'monitor.py is missing.' }

# pythonw keeps the hourly job from opening a console window; logs go to output.
$windowlessPython = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (Test-Path -LiteralPath $windowlessPython) { $PythonPath = $windowlessPython }
$arguments = '"{0}" --once --config "{1}" --output-dir "{2}"' -f $scriptPath, $configPath, $OutputDirectory
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument $arguments -WorkingDirectory $PSScriptRoot
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$description = "Download new official SIF factsheets and extract validated JSON every $IntervalMinutes minutes while the user is logged in. Output: $OutputDirectory"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -notlike 'Download new official SIF factsheets and extract validated JSON*') {
    throw "An unrelated task named '$TaskName' already exists. Choose a different -TaskName."
}
if ($PSCmdlet.ShouldProcess($TaskName, "Register factsheet checks every $IntervalMinutes minutes and at logon")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($hourly, $logon) -Principal $principal -Settings $settings -Description $description -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, Description
}
