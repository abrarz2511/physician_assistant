param(
    [string]$ApiApp = "physician-assistant-srck5q",
    [string]$PostgresApp = "physician-assistant-srck5q-db",
    [string]$RedisApp = "physician-assistant-srck5q-cache",
    [switch]$Stop
)

$unsupportedArguments = @($args | Where-Object { $_ -ine "--stop" })
if ($unsupportedArguments.Count -gt 0) {
    throw "Unsupported argument(s): $($unsupportedArguments -join ', ')"
}
if ($args -icontains "--stop") {
    $Stop = $true
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$flyctl = Get-Command flyctl -ErrorAction Stop
$services = @(
    [pscustomobject]@{ Label = "fastapi"; App = $ApiApp },
    [pscustomobject]@{ Label = "postgres"; App = $PostgresApp },
    [pscustomobject]@{ Label = "redis"; App = $RedisApp }
)

function Get-FlyMachines {
    param(
        [Parameter(Mandatory)]
        [string]$App
    )

    $raw = & $flyctl.Source machines list --app $App --json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not list Machines for Fly app '$App'."
    }

    $json = $raw -join "`n"
    return @($json | ConvertFrom-Json)
}

function Get-FlyMachineId {
    param(
        [Parameter(Mandatory)]
        [string]$App
    )

    $machine = Get-FlyMachines -App $App |
        Where-Object { $_.state -ne "destroyed" } |
        Select-Object -First 1

    if (-not $machine) {
        throw "Fly app '$App' does not have a Machine to start."
    }

    return [string]$machine.id
}

if ($Stop) {
    Write-Host "Discovering active Fly Machines..." -ForegroundColor Cyan
    $stopTargets = foreach ($service in $services) {
        foreach ($machine in @(Get-FlyMachines -App $service.App)) {
            if ($machine.state -in @("started", "starting")) {
                Write-Host ("  [{0}] {1} ({2})" -f $service.Label, $service.App, $machine.id)
                [pscustomobject]@{
                    Service = $service
                    MachineId = [string]$machine.id
                }
            }
        }
    }

    if (-not $stopTargets) {
        Write-Host "All three Fly apps are already stopped." -ForegroundColor Green
        return
    }

    Write-Host "Stopping active Machines..." -ForegroundColor Cyan
    $stopProcesses = foreach ($target in $stopTargets) {
        $process = Start-Process `
            -FilePath $flyctl.Source `
            -ArgumentList @("machine", "stop", $target.MachineId, "--app", $target.Service.App) `
            -NoNewWindow `
            -PassThru

        [pscustomobject]@{
            Target = $target
            Process = $process
        }
    }

    $stopFailed = $false
    foreach ($entry in $stopProcesses) {
        $entry.Process.WaitForExit()
        if ($entry.Process.ExitCode -ne 0) {
            Write-Warning ("[{0}] stop failed with exit code {1}." -f $entry.Target.Service.Label, $entry.Process.ExitCode)
            $stopFailed = $true
        } else {
            Write-Host ("[{0}] stopped" -f $entry.Target.Service.Label) -ForegroundColor Green
        }
    }

    if ($stopFailed) {
        throw "One or more Fly Machines failed to stop."
    }

    return
}

Write-Host "Discovering Fly Machines..." -ForegroundColor Cyan
foreach ($service in $services) {
    $service | Add-Member -NotePropertyName MachineId -NotePropertyValue (
        Get-FlyMachineId -App $service.App
    )
    Write-Host ("  [{0}] {1} ({2})" -f $service.Label, $service.App, $service.MachineId)
}

Write-Host "Starting all three Machines..." -ForegroundColor Cyan
$startProcesses = foreach ($service in $services) {
    $process = Start-Process `
        -FilePath $flyctl.Source `
        -ArgumentList @("machine", "start", $service.MachineId, "--app", $service.App) `
        -NoNewWindow `
        -PassThru

    [pscustomobject]@{
        Service = $service
        Process = $process
    }
}

$startFailed = $false
foreach ($entry in $startProcesses) {
    $entry.Process.WaitForExit()
    if ($entry.Process.ExitCode -ne 0) {
        Write-Warning ("[{0}] start failed with exit code {1}." -f $entry.Service.Label, $entry.Process.ExitCode)
        $startFailed = $true
    } else {
        Write-Host ("[{0}] started" -f $entry.Service.Label) -ForegroundColor Green
    }
}

if ($startFailed) {
    throw "One or more Fly Machines failed to start."
}

Write-Host "Following logs for all services. Press Ctrl+C to stop following logs." -ForegroundColor Cyan
$logJobs = @()

try {
    foreach ($service in $services) {
        $job = Start-Job `
            -Name ("fly-logs-{0}" -f $service.Label) `
            -ArgumentList $flyctl.Source, $service.App `
            -ScriptBlock {
                param($FlyctlPath, $App)
                & $FlyctlPath logs --app $App 2>&1
            }

        $logJobs += [pscustomobject]@{
            Label = $service.Label
            Job = $job
        }
    }

    while ($true) {
        foreach ($entry in $logJobs) {
            foreach ($line in @(Receive-Job -Job $entry.Job)) {
                Write-Host ("[{0}] {1}" -f $entry.Label, $line)
            }

            if ($entry.Job.State -eq "Failed") {
                throw "The log stream for '$($entry.Label)' failed."
            }
        }

        Start-Sleep -Milliseconds 250
    }
} finally {
    foreach ($entry in $logJobs) {
        Stop-Job -Job $entry.Job -ErrorAction SilentlyContinue
        Remove-Job -Job $entry.Job -Force -ErrorAction SilentlyContinue
    }
}
