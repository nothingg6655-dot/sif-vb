[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$')]
    [string]$DailyTime = '11:01:59',
    [string]$TaskName = 'SIF Factsheet Monitor',
    [string]$PythonPath,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'output')
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'monitor.py'
$configPath = Join-Path $PSScriptRoot 'official_sources.json'
if (-not $PythonPath) {
    $projectPython = Join-Path $PSScriptRoot '..\db-test\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $projectPython) {
        $PythonPath = $projectPython
    } else {
        $PythonPath = (& python -c 'import sys; print(sys.executable)').Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Python could not be found.' }
    }
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'monitor.py is missing.' }
if (-not (Test-Path -LiteralPath $configPath)) { throw 'official_sources.json is missing.' }
& $PythonPath -c 'import pymupdf, requests, bs4, playwright, tzdata'
if ($LASTEXITCODE -ne 0) { throw 'Factsheet Python dependencies are missing.' }

# pythonw keeps the daily job from opening a console window; logs go to output.
$windowlessPython = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (Test-Path -LiteralPath $windowlessPython) { $PythonPath = $windowlessPython }
$arguments = '"{0}" --once --config "{1}" --output-dir "{2}"' -f $scriptPath, $configPath, $OutputDirectory
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument $arguments -WorkingDirectory $PSScriptRoot
$ist = [System.TimeZoneInfo]::FindSystemTimeZoneById('India Standard Time')
$todayIst = [System.TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $ist).ToString('yyyy-MM-dd')
$daily = New-ScheduledTaskTrigger -Daily -At $DailyTime
$daily.StartBoundary = "${todayIst}T${DailyTime}+05:30"
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$description = "Download new official SIF factsheets and extract validated JSON daily at $DailyTime IST while the user is logged in. Output: $OutputDirectory"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -notlike 'Download new official SIF factsheets and extract validated JSON*') {
    throw "An unrelated task named '$TaskName' already exists. Choose a different -TaskName."
}
if ($PSCmdlet.ShouldProcess($TaskName, "Register factsheet checks daily at $DailyTime IST")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $daily -Principal $principal -Settings $settings -Description $description -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, Description
}
