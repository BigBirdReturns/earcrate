[CmdletBinding()]
param(
    [string]$Workspace = "D:\Projects\Products\EarCrate\sessions\album-one-sprint-01",
    [string]$Bindings,
    [switch]$VerifyBytes,
    [switch]$PreflightOnly,
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
$PreflightCli = Join-Path $Repo 'scripts\earcrate_album_sprint_preflight.py'
$Contract = Join-Path $Repo 'configs\album_one\sprint-01\campaign.v1.json'
$PreflightContract = Join-Path $Repo 'configs\album_one\sprint-01\executable-preflight.v1.json'
$PreflightAuthority = 'preflight'

& $Python $Cli --contract $Contract verify-contract
if ($LASTEXITCODE -ne 0) { throw "Album Sprint campaign verification failed" }

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
if ($ExecuteReadyAdapters -and -not $VerifyBytes) {
    throw "ExecuteReadyAdapters requires -VerifyBytes; unverified private bindings cannot authorize Estate execution"
}

$RepoPreflightArgs = @(
    $PreflightCli,
    '--campaign', $Contract,
    '--preflight-contract', $PreflightContract
)
if ($Bindings) { $RepoPreflightArgs += @('--bindings', $Bindings) }
if ($VerifyBytes) { $RepoPreflightArgs += '--verify-bytes' }
$RepoPreflightJson = & $Python @RepoPreflightArgs
if ($LASTEXITCODE -ne 0) { throw "Album Sprint executable preflight failed" }
$RepoPreflight = $RepoPreflightJson | ConvertFrom-Json
$RepoPreflightJson

if ($PreflightOnly) { exit 0 }

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

$BoundPreflightArgs = @(
    $PreflightCli,
    '--campaign', $Contract,
    '--preflight-contract', $PreflightContract,
    '--bindings', $Bindings,
    '--workspace', $Workspace
)
if ($VerifyBytes) { $BoundPreflightArgs += '--verify-bytes' }
$BoundPreflightJson = & $Python @BoundPreflightArgs
if ($LASTEXITCODE -ne 0) { throw "Album Sprint bound executable preflight failed" }
$BoundPreflight = $BoundPreflightJson | ConvertFrom-Json
$BoundPreflightJson

if ($ExecuteReadyAdapters) {
    if (-not $BoundPreflight.estate_execution_authorized) {
        Write-Host "No complete music-producing Album adapter passed preflight. Estate execution is not authorized."
    }
    else {
        $Authorized = @($BoundPreflight.authorized_track_ids)
        $Commands = @(
            foreach ($TrackId in $Authorized) {
                $Path = Join-Path $Workspace ("tracks\{0}\NEXT_COMMAND.ps1" -f $TrackId)
                if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
                    throw "Authorized track has no executable command: $TrackId"
                }
                Get-Item -LiteralPath $Path
            }
        )
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
}

& $Python $Cli --contract $Contract status --workspace $Workspace
if ($LASTEXITCODE -ne 0) { throw "Album Sprint post-dispatch verification failed" }

Write-Host ""
Write-Host "ALBUM ONE SPRINT 01 PREFLIGHTED"
Write-Host "Workspace: $Workspace"
Write-Host "Private bindings: $Bindings"
Write-Host "Executable preflight: $(Join-Path $Workspace 'PREFLIGHT.json')"
Write-Host "Campaign projection: $(Join-Path $Workspace 'PUBLIC_PROJECTION.json')"
if ($BoundPreflight.estate_execution_authorized) {
    Write-Host "Authorized tracks: $(@($BoundPreflight.authorized_track_ids) -join ', ')"
}
else {
    Write-Host "Estate execution remains closed. Repository adapter work or exact bindings are still required."
}

if ($OpenWorkspace) {
    Start-Process explorer.exe -ArgumentList ('"' + $Workspace + '"')
}
