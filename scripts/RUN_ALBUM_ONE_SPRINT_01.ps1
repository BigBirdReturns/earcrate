[CmdletBinding()]
param(
    [string]$Workspace = "D:\Projects\Products\EarCrate\sessions\album-one-sprint-01",
    [string]$Bindings,
    [switch]$VerifyBytes,
    [switch]$ExecuteReadyAdapters,
    [ValidateRange(1, 7)]
    [int]$MaxParallel = 4,
    [string]$RecordResult,
    [switch]$Status,
    [switch]$OpenWorkspace
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Cli = Join-Path $Repo 'scripts\earcrate_album_sprint_01.py'
$Contract = Join-Path $Repo 'configs\album_one\sprint-01\campaign.v1.json'

& $Python $Cli --contract $Contract verify-contract
if ($LASTEXITCODE -ne 0) { throw "Album Sprint contract verification failed" }

if ($RecordResult) {
    & $Python $Cli --contract $Contract record --workspace $Workspace --result $RecordResult
    if ($LASTEXITCODE -ne 0) { throw "Album Sprint result recording failed" }
    exit 0
}
if ($Status) {
    & $Python $Cli --contract $Contract status --workspace $Workspace
    if ($LASTEXITCODE -ne 0) { throw "Album Sprint status verification failed" }
    exit 0
}

if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) {
    & $Python $Cli --contract $Contract prepare --workspace $Workspace
    if ($LASTEXITCODE -ne 0) { throw "Album Sprint workspace preparation failed" }
}
if (-not $Bindings) {
    $Bindings = Join-Path $Workspace 'private\source-bindings.private.json'
    $Template = Join-Path $Workspace 'private\source-bindings.private.template.json'
    if (-not (Test-Path -LiteralPath $Bindings -PathType Leaf)) {
        Copy-Item -LiteralPath $Template -Destination $Bindings
    }
}

$Dispatch = @($Cli, '--contract', $Contract, 'dispatch', '--workspace', $Workspace, '--bindings', $Bindings)
if ($VerifyBytes) { $Dispatch += '--verify-bytes' }
& $Python @Dispatch
if ($LASTEXITCODE -ne 0) { throw "Album Sprint dispatch failed" }

if ($ExecuteReadyAdapters) {
    $Commands = @(Get-ChildItem -LiteralPath (Join-Path $Workspace 'tracks') -Filter NEXT_COMMAND.ps1 -Recurse -File)
    for ($Offset = 0; $Offset -lt $Commands.Count; $Offset += $MaxParallel) {
        $End = [Math]::Min($Offset + $MaxParallel - 1, $Commands.Count - 1)
        $Batch = @($Commands[$Offset..$End])
        $Jobs = foreach ($Command in $Batch) {
            Start-Job -ScriptBlock {
                param($Path, $WorkingDirectory)
                Set-Location -LiteralPath $WorkingDirectory
                & powershell -NoProfile -ExecutionPolicy Bypass -File $Path
                if ($LASTEXITCODE -ne 0) { throw "Adapter command failed: $Path" }
            } -ArgumentList $Command.FullName, $Repo
        }
        $Jobs | Wait-Job | Out-Null
        $Failures = @($Jobs | Where-Object State -ne 'Completed')
        $Jobs | Receive-Job
        $Jobs | Remove-Job -Force
        if ($Failures.Count) { throw "One or more Album Sprint adapter commands failed" }
    }
}

& $Python $Cli --contract $Contract status --workspace $Workspace
if ($LASTEXITCODE -ne 0) { throw "Album Sprint post-dispatch verification failed" }

Write-Host ""
Write-Host "ALBUM ONE SPRINT 01 DISPATCHED"
Write-Host "Workspace: $Workspace"
Write-Host "Private bindings: $Bindings"
Write-Host "Queue: $(Join-Path $Workspace 'TASK_QUEUE.json')"
Write-Host "Projection: $(Join-Path $Workspace 'PUBLIC_PROJECTION.json')"
Write-Host "Execute every tracks\A1-XX\TRACK_TASK.json to terminal evidence."
Write-Host "Owner audio is prohibited until a lane produces a full-form admitted frontier."

if ($OpenWorkspace) {
    Start-Process explorer.exe -ArgumentList ('"' + $Workspace + '"')
}
