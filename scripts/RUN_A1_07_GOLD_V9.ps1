[CmdletBinding()]
param(
    [string]$V7Workspace = "S:\Temp\EarCrate\album-one\a1-07-gold-v7",
    [string]$Output = "D:\Projects\Products\EarCrate\sessions\a1-07-gold-v9-timing-laws",
    [switch]$Verify,
    [switch]$OpenReviewDirectory
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python).Source
$Cli = Join-Path $Repo 'scripts\earcrate_a1_07_gold_v9.py'
$Contract = Join-Path $Repo 'configs\album_one\a1-07\gold-v9-timing-laws.v1.json'

if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw "A1-07 gold-v9 CLI missing: $Cli"
}
if (-not (Test-Path -LiteralPath $Contract -PathType Leaf)) {
    throw "A1-07 gold-v9 contract missing: $Contract"
}

& $Python $Cli --contract $Contract verify-contract
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v9 contract verification failed" }

if ($Verify) {
    & $Python $Cli --contract $Contract verify --workspace $Output
    if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v9 workspace verification failed" }
    exit 0
}

& $Python $Cli --contract $Contract build `
  --v7-workspace $V7Workspace `
  --output $Output
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v9 build failed" }

& $Python $Cli --contract $Contract verify --workspace $Output
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v9 post-build verification failed" }

$Review = Join-Path $Output 'review'
Write-Host "A1-07 gold-v9 transparent timing-law review: $Review"
Write-Host "Read first: $(Join-Path $Review 'CUT_NOTES.md')"
Write-Host "A = single-speed; B = native-pocket; C = phrase-reset. No hidden map."
if ($OpenReviewDirectory) {
    Start-Process explorer.exe -ArgumentList ('"' + $Review + '"')
}
