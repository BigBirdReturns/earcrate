[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CoreRoot,
    [Parameter(Mandatory=$true)][string]$Workspace,
    [switch]$VerifyPcm
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python).Source
$Cli = Join-Path $Repo 'scripts\earcrate_reference_zero.py'
$Core = [System.IO.Path]::GetFullPath($CoreRoot)
$Root = [System.IO.Path]::GetFullPath($Workspace)

if (Test-Path -LiteralPath (Join-Path $Core 'CORE\READ_ME_FIRST.md')) {
    $Core = Join-Path $Core 'CORE'
}
if (-not (Test-Path -LiteralPath (Join-Path $Core 'READ_ME_FIRST.md') -PathType Leaf)) {
    throw "CORE/READ_ME_FIRST.md not found beneath: $CoreRoot"
}
if (Test-Path -LiteralPath $Root) {
    throw "Reference Zero workspace already exists: $Root"
}
New-Item -ItemType Directory -Path $Root | Out-Null

$Sources = [ordered]@{
    four_seasons_master = Join-Path $Core 'sources\four_seasons_beggin.mp3'
    maneskin_master = Join-Path $Core 'sources\maneskin_beggin.mp3'
    four_seasons_vocals = Join-Path $Core 'stems\four_seasons\vocals.flac'
    four_seasons_drums = Join-Path $Core 'stems\four_seasons\drums.flac'
    four_seasons_bass = Join-Path $Core 'stems\four_seasons\bass.flac'
    four_seasons_other = Join-Path $Core 'stems\four_seasons\other.flac'
    maneskin_vocals = Join-Path $Core 'stems\maneskin\vocals.flac'
    maneskin_drums = Join-Path $Core 'stems\maneskin\drums.flac'
    maneskin_bass = Join-Path $Core 'stems\maneskin\bass.flac'
    maneskin_other = Join-Path $Core 'stems\maneskin\other.flac'
}
foreach ($Pair in $Sources.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $Pair.Value -PathType Leaf)) {
        throw "Required CORE source missing: $($Pair.Value)"
    }
}

$Args = @($Cli, 'registry', '--sample-rate', '48000', '--channels', '2', '--output', (Join-Path $Root 'source-registry.json'))
foreach ($Pair in $Sources.GetEnumerator()) {
    $Args += @('--source', "$($Pair.Key)=$($Pair.Value)")
}
$Roles = [ordered]@{
    four_seasons_master = 'literal_source_control'
    maneskin_master = 'literal_donor_control'
    four_seasons_vocals = 'lead_vocal_authority'
    four_seasons_drums = 'source_upper_percussion_and_groove_identity'
    four_seasons_bass = 'source_bass_identity'
    four_seasons_other = 'source_harmonic_identity'
    maneskin_vocals = 'timing_witness_only_unless_explicitly_selected'
    maneskin_drums = 'modern_drum_material'
    maneskin_bass = 'modern_bass_material'
    maneskin_other = 'modern_harmonic_and_room_material'
}
foreach ($Pair in $Roles.GetEnumerator()) {
    $Args += @('--role', "$($Pair.Key)=$($Pair.Value)")
}
if ($VerifyPcm) { $Args += '--verify-pcm' }
& $Python @Args
if ($LASTEXITCODE -ne 0) { throw "Source registry creation failed" }

& $Python $Cli edl-template --output (Join-Path $Root 'gold.performance.edl.csv')
if ($LASTEXITCODE -ne 0) { throw "Gold EDL template creation failed" }
& $Python $Cli edl-template --output (Join-Path $Root 'naive-control.edl.csv')
if ($LASTEXITCODE -ne 0) { throw "Control EDL template creation failed" }

@"
Reference Zero workspace prepared from the private CORE fixture.

1. Author one 15-30 second performance in REAPER, Mixxx, or another DAW.
2. Record every selected clip in gold.performance.edl.csv.
3. Record the serious literal co-play baseline in naive-control.edl.csv.
4. Compile both EDLs to PerformanceScores.
5. Bind paths privately, render twice, and require identical canonical PCM.
6. Put GOLD against CONTROL only for owner acceptance. Do not run inference yet.

The source registry is path-free. The CORE source audio remains outside this workspace.
"@ | Set-Content -LiteralPath (Join-Path $Root 'NEXT_ACTION.txt') -Encoding UTF8

Write-Host "Reference Zero workspace: $Root"
Write-Host "Next file: $(Join-Path $Root 'gold.performance.edl.csv')"
