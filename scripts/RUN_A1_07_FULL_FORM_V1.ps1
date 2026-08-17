[CmdletBinding()]
param(
    # The private binding locations are deliberately NOT defaulted here. They name
    # copyrighted source custody, so baking them into a tracked runner would publish
    # the layout of private media. Supply them per invocation, or point
    # -LocalConfig at an untracked JSON file holding v7_workspace and core_archive.
    [string]$V7Workspace,
    [string]$CoreArchive,
    [string]$LocalConfig,
    [string]$Output = "D:\Projects\Products\EarCrate\sessions\a1-07-full-form-v1\frontier",
    [string]$FfmpegDir = "D:\Toolchains\FFmpeg\8.1.2-essentials_build\bin",
    [switch]$PlanOnly,
    [switch]$ShowContract,
    [switch]$OpenOutputDirectory
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Cli = Join-Path $Repo 'scripts\earcrate_a1_07_full_form_v1.py'
$Contract = Join-Path $Repo 'configs\album_one\a1-07\full-form-v1.v1.json'

# The audio dependencies live in the repo venv, and FFmpeg is not on the system
# PATH on this host. Without both, the render fails for a purely environmental
# reason that reads like a musical defect.
$Venv = Join-Path $Repo '.venv\Scripts\python.exe'
$Python = if (Test-Path -LiteralPath $Venv -PathType Leaf) { $Venv } else { (Get-Command python).Source }
if ($FfmpegDir -and (Test-Path -LiteralPath $FfmpegDir -PathType Container)) {
    $env:PATH = "$FfmpegDir;$env:PATH"
}

if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) { throw "A1-07 full-form CLI missing: $Cli" }
if (-not (Test-Path -LiteralPath $Contract -PathType Leaf)) { throw "A1-07 full-form contract missing: $Contract" }

# Resolve the private bindings from the untracked local config when not passed.
if (-not $LocalConfig) {
    $LocalConfig = Join-Path (Split-Path -Parent $Repo) 'a1-07-full-form-v1.local.json'
}
if ((-not $V7Workspace -or -not $CoreArchive) -and (Test-Path -LiteralPath $LocalConfig -PathType Leaf)) {
    $local = Get-Content -LiteralPath $LocalConfig -Raw | ConvertFrom-Json
    if (-not $V7Workspace) { $V7Workspace = $local.v7_workspace }
    if (-not $CoreArchive) { $CoreArchive = $local.core_archive }
}
if (-not $V7Workspace) {
    throw "no gold-v7 workspace supplied. Pass -V7Workspace, or create $LocalConfig with {""v7_workspace"": ""..."", ""core_archive"": ""...""}. These paths are private and are never tracked."
}

& $Python $Cli show-contract --contract $Contract
if ($LASTEXITCODE -ne 0) { throw "A1-07 full-form contract verification failed" }
if ($ShowContract) { exit 0 }

if (-not (Test-Path -LiteralPath $V7Workspace -PathType Container)) {
    throw "qualified gold-v7 workspace missing: $V7Workspace"
}

if ($PlanOnly) {
    & $Python $Cli plan --contract $Contract --v7-workspace $V7Workspace
    if ($LASTEXITCODE -ne 0) { throw "A1-07 full-form planning failed" }
    exit 0
}

if (-not (Test-Path -LiteralPath $CoreArchive -PathType Leaf)) {
    throw "Beggin CORE private store missing: $CoreArchive"
}

& $Python $Cli build `
  --contract $Contract `
  --v7-workspace $V7Workspace `
  --core-archive $CoreArchive `
  --output $Output
$code = $LASTEXITCODE
if ($code -eq 1) { throw "A1-07 full-form build failed" }

Write-Host ""
Write-Host "A1-07 full-form frontier: $Output"
Write-Host "Manifest:   $(Join-Path $Output 'ADAPTER_MANIFEST.json')"
Write-Host "Projection: $(Join-Path $Output 'PUBLIC_PROJECTION.json')"
if ($code -eq 2) {
    Write-Warning "The frontier is NOT admissible: fewer than two candidates qualified, or two candidates share one canonical PCM identity. Do not take this to the owner."
} else {
    Write-Host "Body and Frankie rows are invariant across all candidates; only the donor-band timing law varies."
    Write-Host "Review labels are permuted per pack under a private nonce. The mapping lives ONLY in"
    Write-Host "  $(Join-Path $Output 'review\private\authority.json')"
    Write-Host "Do not open it before the verdict is sealed."
}
if ($OpenOutputDirectory) {
    Start-Process explorer.exe -ArgumentList ('"' + $Output + '"')
}
exit $code
