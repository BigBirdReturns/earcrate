[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$AnalysisBundle,
    [Parameter(Mandatory=$true)][string]$Workspace,
    [string]$PrivateExecutor,
    [string[]]$PrivateExecutorArgument = @(),
    [string]$CandidateManifest,
    [string]$OutputZip
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Commission = Join-Path $Repo 'scripts\earcrate_robi_whoa_30s_v1.py'
$ResolvedSource = (Resolve-Path -LiteralPath $Source).Path
$ResolvedBundle = (Resolve-Path -LiteralPath $AnalysisBundle).Path

& $Python $Commission prepare `
    --source $ResolvedSource `
    --analysis-bundle $ResolvedBundle `
    --workspace $Workspace
if ($LASTEXITCODE -ne 0) { throw 'Robi commission input binding refused.' }

$Request = Join-Path $Workspace 'estate-execution-request.private.json'
if ($PrivateExecutor) {
    $ExecutorPath = (Resolve-Path -LiteralPath $PrivateExecutor).Path
    $CandidateRoot = Join-Path $Workspace 'candidate'
    New-Item -ItemType Directory -Path $CandidateRoot -ErrorAction Stop | Out-Null
    & $ExecutorPath @PrivateExecutorArgument --request $Request --output $CandidateRoot
    if ($LASTEXITCODE -ne 0) { throw 'Private estate executor refused or failed.' }
    if (-not $CandidateManifest) {
        $CandidateManifest = Join-Path $CandidateRoot 'candidate-manifest.private.json'
    }
}

if (-not $CandidateManifest) {
    throw ('No private executor or candidate manifest was supplied. The exact estate request is at ' +
           $Request + '. No audio was rendered and no fallback is authorized.')
}
$ResolvedManifest = (Resolve-Path -LiteralPath $CandidateManifest).Path
if (-not $OutputZip) {
    $OutputZip = Join-Path $Workspace 'EarCrate-Robi-WHOA-30s-estate-candidate.zip'
}

& $Python $Commission finalize --candidate-manifest $ResolvedManifest --output-zip $OutputZip
if ($LASTEXITCODE -ne 0) { throw 'Robi candidate qualification or packaging refused.' }
Write-Host "Candidate package: $OutputZip"
