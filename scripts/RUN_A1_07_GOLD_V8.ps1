[CmdletBinding()]
param(
    [string]$V7Workspace = "S:\Temp\EarCrate\album-one\a1-07-gold-v7",
    [string]$Output = "S:\Temp\EarCrate\album-one\a1-07-gold-v8",
    [switch]$Verify,
    [switch]$OpenReviewDirectory,
    [string]$WholeRanking,
    [string]$CoreRanking,
    [string]$Note = ""
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python).Source
$Cli = Join-Path $Repo 'scripts\earcrate_a1_07_gold_v8.py'
$Contract = Join-Path $Repo 'configs\album_one\a1-07\gold-v8-arc-rungs.v1.json'

if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw "A1-07 gold-v8 CLI missing: $Cli"
}
if (-not (Test-Path -LiteralPath $Contract -PathType Leaf)) {
    throw "A1-07 gold-v8 contract missing: $Contract"
}

& $Python $Cli --contract $Contract verify-contract
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v8 contract verification failed" }

if ($WholeRanking -or $CoreRanking) {
    if (-not $WholeRanking -or -not $CoreRanking) {
        throw "Both -WholeRanking and -CoreRanking are required"
    }
    & $Python $Cli --contract $Contract review `
      --workspace $Output `
      --whole-ranking $WholeRanking `
      --core-ranking $CoreRanking `
      --note $Note
    if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v8 owner review sealing failed" }
    exit 0
}

if ($Verify) {
    & $Python $Cli --contract $Contract verify --workspace $Output
    if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v8 workspace verification failed" }
    exit 0
}

& $Python $Cli --contract $Contract build `
  --v7-workspace $V7Workspace `
  --output $Output
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v8 build failed" }

& $Python $Cli --contract $Contract verify --workspace $Output
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v8 post-build verification failed" }

$Review = Join-Path $Output 'review'
Write-Host "A1-07 gold-v8 whole-arc review: $(Join-Path $Review 'whole-arc\public')"
Write-Host "A1-07 gold-v8 core-window review: $(Join-Path $Review 'core-window\public')"
if ($OpenReviewDirectory) {
    Start-Process explorer.exe -ArgumentList ('"' + $Review + '"')
}
