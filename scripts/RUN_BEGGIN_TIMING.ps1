[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BindingsDirectory,
    [string]$EstateInventory = "",
    [Parameter(Mandatory = $true)]
    [string]$SmokeDirectory,
    [string]$DrumStem = "",
    [string]$BaselineSmoke = "",
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$CampaignSha256 = "cc8bc09a3659817711a539aeb0f6e9a8eb6cf09a8d768803fefb28c8cad3c01e",
    [string]$SuiteSha256 = "e9726dfd4048a88a13b973b6bb9af03fb6e58285f432cf9747b17a0d7ec5a666",
    [string]$TargetVocalWitness = "",
    [string]$Python = "python",
    [switch]$OpenReviewDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ScriptRoot "beggin_timing_pass.py"
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Timing runner not found: $Runner"
}
if (-not (Test-Path -LiteralPath $BindingsDirectory -PathType Container)) {
    throw "Bindings directory not found: $BindingsDirectory"
}
if (-not (Test-Path -LiteralPath $EstateInventory -PathType Leaf) -and [string]::IsNullOrWhiteSpace($TargetVocalWitness)) {
    throw "Authoritative estate inventory not found and no explicit timing witness was supplied: $EstateInventory"
}
if (Test-Path -LiteralPath $OutputDirectory) {
    $children = @(Get-ChildItem -LiteralPath $OutputDirectory -Force)
    if ($children.Count -gt 0) {
        throw "Output directory must be new or empty: $OutputDirectory"
    }
}

$bindingFiles = @(Get-ChildItem -LiteralPath $BindingsDirectory -Filter "*.binding.json" -File)
$sourceBinding = $null
$targetBinding = $null
foreach ($file in $bindingFiles) {
    $value = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
    if ($value.case_id -ne "beggin-four-seasons-x-maneskin-handoff") { continue }
    if ($value.source_id -eq "four_seasons_beggin") { $sourceBinding = $file.FullName }
    if ($value.source_id -eq "maneskin_beggin") { $targetBinding = $file.FullName }
}
if (-not $sourceBinding) { throw "Four Seasons binding not found beneath $BindingsDirectory" }
if (-not $targetBinding) { throw "Måneskin binding not found beneath $BindingsDirectory" }

if ([string]::IsNullOrWhiteSpace($DrumStem)) {
    if (-not (Test-Path -LiteralPath $SmokeDirectory -PathType Container)) {
        throw "Smoke directory not found and -DrumStem was not supplied: $SmokeDirectory"
    }
    $candidates = @(
        Get-ChildItem -LiteralPath $SmokeDirectory -Recurse -File -Filter "*.wav" |
        Where-Object {
            $_.Name -match "(?i)drum" -and
            $_.Name -notmatch "(?i)SMOKE-LISTEN" -and
            $_.FullName -ne $BaselineSmoke
        }
    )
    if ($candidates.Count -ne 1) {
        $names = ($candidates | ForEach-Object FullName) -join "`n"
        throw "Expected exactly one separated drum WAV beneath $SmokeDirectory; found $($candidates.Count). Supply -DrumStem explicitly.`n$names"
    }
    $DrumStem = $candidates[0].FullName
}
if (-not (Test-Path -LiteralPath $DrumStem -PathType Leaf)) {
    throw "Drum stem not found: $DrumStem"
}

if ([string]::IsNullOrWhiteSpace($BaselineSmoke)) {
    $defaultBaseline = Join-Path $SmokeDirectory "SMOKE-LISTEN-four-seasons-acapella-plus-maneskin-drums.wav"
    if (Test-Path -LiteralPath $defaultBaseline -PathType Leaf) {
        $BaselineSmoke = $defaultBaseline
    }
}

$arguments = @(
    $Runner,
    "run",
    "--source-vocal-binding", $sourceBinding,
    "--target-instrumental-binding", $targetBinding,
    "--drum-stem", $DrumStem,
    "--output", $OutputDirectory,
    "--campaign-sha256", $CampaignSha256,
    "--suite-sha256", $SuiteSha256
)
if (-not [string]::IsNullOrWhiteSpace($TargetVocalWitness)) {
    $arguments += @("--target-vocal-witness", $TargetVocalWitness)
} else {
    $arguments += @("--estate-inventory", $EstateInventory)
}
if (Test-Path -LiteralPath $BaselineSmoke -PathType Leaf) {
    $arguments += @("--baseline-smoke", $BaselineSmoke)
}

Write-Host "Running Beggin' phrase-local timing pass..."
Write-Host "Source binding: $sourceBinding"
Write-Host "Target binding: $targetBinding"
Write-Host "Drum stem: $DrumStem"
Write-Host "Output: $OutputDirectory"

& $Python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Beggin timing pass failed with exit code $LASTEXITCODE"
}

& $Python $Runner "verify" $OutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Beggin timing output verification failed"
}

$review = Join-Path $OutputDirectory "review-public"
Write-Host "Review candidates are ready at: $review"
Write-Host "Do not inspect review-private until after choosing A, B, C, tie, reject_all, or abstain."
if ($OpenReviewDirectory) {
    Start-Process explorer.exe $review
}
