$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Format-TaskTime($value) {
    if ($null -eq $value -or $value.Year -lt 2000) { return $null }
    return $value.ToUniversalTime().ToString('o')
}

$results = foreach ($taskName in @('SIF Factsheet Monitor', 'SIF NAV Sync')) {
    try {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $task.TaskPath -ErrorAction Stop
        $times = @($task.Triggers | Where-Object StartBoundary | ForEach-Object {
            ([DateTimeOffset]::Parse($_.StartBoundary)).ToOffset([TimeSpan]::FromMinutes(330)).ToString('HH:mm:ss')
        } | Sort-Object -Unique)
        [pscustomobject]@{
            taskName = $taskName
            readStatus = 'ok'
            enabled = [bool]$task.Settings.Enabled
            state = [string]$task.State
            lastRunAt = Format-TaskTime $info.LastRunTime
            nextRunAt = Format-TaskTime $info.NextRunTime
            lastExitCode = [long]$info.LastTaskResult
            triggerTimes = $times
        }
    } catch {
        [pscustomobject]@{
            taskName = $taskName
            readStatus = if ($_.CategoryInfo.Category -eq 'ObjectNotFound') { 'missing' } else { 'unavailable' }
            enabled = $null
            state = 'Unknown'
            lastRunAt = $null
            nextRunAt = $null
            lastExitCode = $null
            triggerTimes = @()
        }
    }
}
ConvertTo-Json -InputObject @($results) -Depth 4 -Compress
