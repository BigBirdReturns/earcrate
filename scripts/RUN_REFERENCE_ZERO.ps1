[CmdletBinding(DefaultParameterSetName='Render')]
param(
    [Parameter(Mandatory=$true)][string]$Workspace,
    [Parameter(ParameterSetName='Import', Mandatory=$true)][string]$SourceRegistry,
    [Parameter(ParameterSetName='Import', Mandatory=$true)][string]$Edl,
    [Parameter(ParameterSetName='Import', Mandatory=$true)][string]$ScoreId,
    [Parameter(ParameterSetName='Import', Mandatory=$true)][string]$Title,
    [Parameter(ParameterSetName='Import', Mandatory=$true)][double]$DurationSeconds,
    [Parameter(ParameterSetName='Render')][string]$Score,
    [Parameter(ParameterSetName='Render')][string]$Bindings,
    [Parameter(ParameterSetName='Render')][switch]$VerifyReproduction,
    [Parameter(ParameterSetName='Template')][switch]$CreateTemplate,
    [int]$SampleRate = 48000,
    [ValidateSet(1,2)][int]$Channels = 2
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python).Source
$Cli = Join-Path $Repo 'scripts\earcrate_reference_zero.py'
$Root = [System.IO.Path]::GetFullPath($Workspace)

if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw "Reference Zero CLI missing: $Cli"
}

if ($CreateTemplate) {
    if (Test-Path -LiteralPath $Root) { throw "Workspace already exists: $Root" }
    New-Item -ItemType Directory -Path $Root | Out-Null
    Copy-Item (Join-Path $Repo 'configs\reference_zero\beggin.edl.template.csv') (Join-Path $Root 'performance.edl.csv')
    Copy-Item (Join-Path $Repo 'configs\reference_zero\source-registry.template.json') (Join-Path $Root 'source-registry.json')
    Write-Host "Reference Zero authoring workspace: $Root"
    Write-Host "Fill source-registry.json identities and performance.edl.csv decisions."
    exit 0
}

if ($PSCmdlet.ParameterSetName -eq 'Import') {
    if (-not (Test-Path -LiteralPath $Root)) { New-Item -ItemType Directory -Path $Root | Out-Null }
    $ScorePath = Join-Path $Root 'performance-score.json'
    & $Python $Cli import-edl `
      --source-registry $SourceRegistry `
      --edl $Edl `
      --score-id $ScoreId `
      --title $Title `
      --sample-rate $SampleRate `
      --channels $Channels `
      --duration-seconds $DurationSeconds `
      --output $ScorePath
    if ($LASTEXITCODE -ne 0) { throw "PerformanceScore import failed" }
    Write-Host "PerformanceScore: $ScorePath"
    exit 0
}

if (-not $Score) { $Score = Join-Path $Root 'performance-score.json' }
if (-not $Bindings) { $Bindings = Join-Path $Root 'source-bindings.private.json' }
if (-not (Test-Path -LiteralPath $Score -PathType Leaf)) { throw "Score missing: $Score" }
if (-not (Test-Path -LiteralPath $Bindings -PathType Leaf)) { throw "Bindings missing: $Bindings" }

$RenderRoot = Join-Path $Root 'render'
if (Test-Path -LiteralPath $RenderRoot) { throw "Render directory already exists: $RenderRoot" }
New-Item -ItemType Directory -Path $RenderRoot | Out-Null

if ($VerifyReproduction) {
    & $Python $Cli verify-reproduction `
      --score $Score `
      --bindings $Bindings `
      --output-directory $RenderRoot
    if ($LASTEXITCODE -ne 0) { throw "Reference Zero reproduction verification failed" }
} else {
    & $Python $Cli render `
      --score $Score `
      --bindings $Bindings `
      --output (Join-Path $RenderRoot 'reference-zero.wav') `
      --receipt (Join-Path $RenderRoot 'reference-zero.render.json') `
      --verify-source-pcm
    if ($LASTEXITCODE -ne 0) { throw "Reference Zero render failed" }
}

Write-Host "Reference Zero render workspace: $RenderRoot"
