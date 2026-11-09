[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'SIF NAV Sync',
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runner = Join-Path $PSScriptRoot 'run_nav_sync.py'
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
if (-not (Test-Path -LiteralPath $runner)) { throw 'run_nav_sync.py is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) { throw 'db-test/.env is missing.' }
& $PythonPath -c 'import psycopg, pyarrow, apscheduler, dotenv, tzdata'
if ($LASTEXITCODE -ne 0) { throw 'NAV worker Python dependencies are missing.' }
$windowlessPython = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (Test-Path -LiteralPath $windowlessPython) { $PythonPath = $windowlessPython }

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument ('"{0}"' -f $runner) -WorkingDirectory $projectRoot
$ist = [System.TimeZoneInfo]::FindSystemTimeZoneById('India Standard Time')
$todayIst = [System.TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $ist).ToString('yyyy-MM-dd')
$triggers = @(for ($hour = 0; $hour -lt 24; $hour += 2) {
    $clock = '{0:00}:00:00' -f $hour
    $trigger = New-ScheduledTaskTrigger -Daily -At $clock
    $trigger.StartBoundary = "${todayIst}T${clock}+05:30"
    $trigger
})
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 110)
$description = 'Check TIGZIG source checksums every two hours at 00:00, 02:00, ..., 22:00 IST. Import only changed snapshots; keep unchanged source rows intact.'
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -notlike 'Check TIGZIG source checksums*') {
    throw "An unrelated task named '$TaskName' already exists. Choose a different -TaskName."
}
if ($PSCmdlet.ShouldProcess($TaskName, 'Register NAV checks every two hours in IST')) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description $description -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, Description
}
