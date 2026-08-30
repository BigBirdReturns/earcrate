[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Source = "",
    [string]$PreviousRefusal = "",
    [string]$BaseUrl = "",
    [string]$OutputRoot = "",
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONUTF8 = "1"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DefaultRepo = (Resolve-Path (Join-Path $ScriptRoot "..")).Path
$ControllerName = "earcrate_robi_whoa_bed_first_v2.py"
$ExpectedRefusalSha256 = "DF50A385B1F8FEAD0AED8C1EAEF4F57979C6D5E340B5546B17BEEE075BF1C7BC"

function Test-EarCrateRoot([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path "AGENTS.md") -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Path "earcrate\app.py") -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Path "scripts\ace_step_v15_adapter.py") -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Path "scripts\$ControllerName") -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Path "configs\commissions\robi-whoa-bed-first-v2.json") -PathType Leaf)
}

function Resolve-EarCrateRoot([string]$Requested) {
    $Candidates = @(
        $Requested,
        $DefaultRepo,
        $env:EARCRATE_REPO,
        "S:\Projects\EarCrate",
        "D:\Projects\EarCrate",
        "E:\Projects\EarCrate",
        (Join-Path $env:USERPROFILE "Projects\EarCrate"),
        (Join-Path $env:USERPROFILE "source\repos\earcrate"),
        (Join-Path $env:USERPROFILE "Documents\EarCrate")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    foreach ($Candidate in $Candidates) {
        if (Test-EarCrateRoot $Candidate) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    throw "The EarCrate checkout containing the bed-first v2 contract was not found. Pull the PR branch or supply -RepoRoot."
}

function Add-FFmpegToPath {
    if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and
        (Get-Command ffprobe -ErrorAction SilentlyContinue)) { return }

    $Bins = @(
        $env:EARCRATE_FFMPEG_BIN,
        "D:\Toolchains\FFmpeg\8.1.2-essentials_build\bin",
        "D:\Toolchains\FFmpeg\bin",
        "C:\ffmpeg\bin",
        "S:\Tools\ffmpeg\bin"
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    foreach ($Bin in $Bins) {
        if ((Test-Path -LiteralPath (Join-Path $Bin "ffmpeg.exe") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $Bin "ffprobe.exe") -PathType Leaf)) {
            $env:PATH = "$Bin;$env:PATH"
            break
        }
    }
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        throw "ffmpeg was not found. No renderer or provider call was made."
    }
    if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
        throw "ffprobe was not found. No renderer or provider call was made."
    }
}

function Resolve-Python([string]$Root) {
    $Candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root "venv\Scripts\python.exe"),
        (Join-Path $Root "env\Scripts\python.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return @{ Exe = $Candidate; Prefix = @() }
        }
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $Python) { return @{ Exe = $Python.Source; Prefix = @() } }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $Py) { return @{ Exe = $Py.Source; Prefix = @("-3") } }
    throw "Python was not found. No renderer or provider call was made."
}

function Resolve-RobiSource([string]$Requested) {
    $Downloads = Join-Path $env:USERPROFILE "Downloads"
    $Candidates = @(
        $Requested,
        $env:EARCRATE_ROBI_SOURCE,
        (Join-Path $Downloads "EarCrate-Robi-WHOA-estate-campaign-20260829\source\Robi-WHOA-source-48k-mono.wav"),
        (Join-Path $Downloads "Robi-WHOA-source-48k-mono.wav")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    if (Test-Path -LiteralPath $Downloads -PathType Container) {
        $Found = Get-ChildItem -LiteralPath $Downloads -Filter "Robi-WHOA-source-48k-mono.wav" -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -ne $Found) { return $Found.FullName }
    }
    throw "The exact bound Robi source WAV was not found. Supply -Source or set EARCRATE_ROBI_SOURCE."
}

function Resolve-PreviousRefusal([string]$Requested) {
    $Downloads = Join-Path $env:USERPROFILE "Downloads"
    $Known = Join-Path $Downloads "EarCrate-Robi-WHOA-estate-run-20260829-2044-corrected\REFUSAL.json"
    $Candidates = @(
        $Requested,
        $env:EARCRATE_ROBI_V1_REFUSAL,
        $Known
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Candidate).Hash
            if ($Hash -eq $ExpectedRefusalSha256) {
                return (Resolve-Path -LiteralPath $Candidate).Path
            }
        }
    }
    if (Test-Path -LiteralPath $Downloads -PathType Container) {
        $Runs = Get-ChildItem -LiteralPath $Downloads -Directory -Filter "EarCrate-Robi-WHOA-estate-run-*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        foreach ($Run in $Runs) {
            $Candidate = Join-Path $Run.FullName "REFUSAL.json"
            if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { continue }
            $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Candidate).Hash
            if ($Hash -eq $ExpectedRefusalSha256) { return $Candidate }
        }
    }
    throw "The terminal v1 REFUSAL.json with SHA-256 $ExpectedRefusalSha256 was not found. Supply -PreviousRefusal or set EARCRATE_ROBI_V1_REFUSAL."
}

try {
    $ResolvedRepo = Resolve-EarCrateRoot $RepoRoot
    $Controller = Join-Path $ResolvedRepo "scripts\$ControllerName"
    $ResolvedSource = Resolve-RobiSource $Source
    $ResolvedRefusal = Resolve-PreviousRefusal $PreviousRefusal
    Add-FFmpegToPath
    $Python = Resolve-Python $ResolvedRepo

    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $OutputRoot = Join-Path (Join-Path $env:USERPROFILE "Downloads") "EarCrate-Robi-WHOA-bed-first-v2-run-$Stamp"
    }

    Write-Host "EarCrate:       $ResolvedRepo"
    Write-Host "Source:         $ResolvedSource"
    Write-Host "Closed v1:      $ResolvedRefusal"
    Write-Host "Output:         $OutputRoot"
    Write-Host "Candidate body: approved estate graph beds + ACE-Step BGM-only beds"
    Write-Host "Admission rule: a bed must pass alone before Robi is added; one loop or one refusal"
    Write-Host ""

    $Arguments = @()
    $Arguments += $Python.Prefix
    $Arguments += @(
        $Controller,
        "--repo", $ResolvedRepo,
        "--source", $ResolvedSource,
        "--previous-refusal", $ResolvedRefusal,
        "--output", $OutputRoot
    )
    if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
        $Arguments += @("--base-url", $BaseUrl)
    }
    if ($PlanOnly) { $Arguments += "--plan-only" }

    & $Python.Exe @Arguments
    $Code = $LASTEXITCODE
    if ($Code -ne 0) {
        Write-Host ""
        Write-Host "REFUSED. No failed bed was mixed under Robi and no substitute delivery was created." -ForegroundColor Yellow
        Write-Host (Join-Path $OutputRoot "REFUSAL.json") -ForegroundColor Yellow
        exit $Code
    }

    Write-Host ""
    if ($PlanOnly) {
        Write-Host "Plan and custody validated. No provider or renderer execution was requested." -ForegroundColor Green
        Write-Host (Join-Path $OutputRoot "CAMPAIGN.plan.json") -ForegroundColor Green
    } else {
        Write-Host "One complete bed-first loop qualified. The single owner surface is:" -ForegroundColor Green
        Write-Host (Join-Path $OutputRoot "Robi-WHOA-BED-FIRST-V2-DELIVERY.zip") -ForegroundColor Green
    }
    exit 0
}
catch {
    Write-Host ""
    Write-Host "REFUSED BEFORE EXECUTION: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "No substitute audio was created." -ForegroundColor Yellow
    exit 2
}
