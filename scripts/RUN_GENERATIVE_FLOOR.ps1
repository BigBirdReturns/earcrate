[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Workspace,
    [string]$ProviderCatalog,
    [string]$Campaign,
    [string[]]$ProviderOverride = @(),
    [string[]]$Provider = @(),
    [switch]$ProbeOnly
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Cli = Join-Path $Repo 'scripts\earcrate_generative_floor.py'
if (-not $ProviderCatalog) { $ProviderCatalog = Join-Path $Repo 'configs\generative_floor\providers.v1.json' }
if (-not $Campaign) { $Campaign = Join-Path $Repo 'configs\generative_floor\beggin-suno-bones.v1.json' }

if (Test-Path -LiteralPath $Workspace) {
    throw "Workspace already exists; use a new immutable campaign directory: $Workspace"
}
New-Item -ItemType Directory -Path $Workspace | Out-Null
$ProbeDir = Join-Path $Workspace 'probes'
New-Item -ItemType Directory -Path $ProbeDir | Out-Null

& $Python $Cli validate-catalog --catalog $ProviderCatalog
if ($LASTEXITCODE -ne 0) { throw 'Provider catalog validation failed' }

$CatalogObject = Get-Content -LiteralPath $ProviderCatalog -Raw | ConvertFrom-Json
$RequestedProviders = @($Provider)
if ($RequestedProviders.Count -eq 0) {
    $RequestedProviders = @($CatalogObject.providers | ForEach-Object { $_.provider_id })
}

$OverrideMap = @{}
foreach ($OverridePath in $ProviderOverride) {
    $OverrideObject = Get-Content -LiteralPath $OverridePath -Raw | ConvertFrom-Json
    if (-not $OverrideObject.provider_id) { throw "Override has no provider_id: $OverridePath" }
    $OverrideMap[$OverrideObject.provider_id] = $OverridePath
}

$ProbePaths = @()
foreach ($ProviderId in $RequestedProviders) {
    $ProbePath = Join-Path $ProbeDir ($ProviderId + '.probe.json')
    $Args = @($Cli, 'probe', '--catalog', $ProviderCatalog, '--provider', $ProviderId, '--output', $ProbePath)
    if ($OverrideMap.ContainsKey($ProviderId)) { $Args += @('--override', $OverrideMap[$ProviderId]) }
    & $Python @Args
    if ($LASTEXITCODE -ne 0) { throw "Probe command failed for $ProviderId" }
    $ProbePaths += $ProbePath
}

$PlanPath = Join-Path $Workspace 'generation-campaign.json'
$PlanArgs = @($Cli, 'plan', '--catalog', $ProviderCatalog, '--campaign', $Campaign, '--output', $PlanPath)
foreach ($ProbePath in $ProbePaths) { $PlanArgs += @('--probe', $ProbePath) }
& $Python @PlanArgs
if ($LASTEXITCODE -ne 0) { throw 'Generation campaign compilation failed' }

$Plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
$NextActions = @()
$NextActions += '# EarCrate generative-floor local next actions'
$NextActions += ''
$NextActions += ('Campaign: `' + $Plan.campaign_sha256 + '`')
$NextActions += ('Ready tasks: ' + $Plan.summary.ready)
$NextActions += ('Blocked tasks: ' + $Plan.summary.blocked)
$NextActions += ''
foreach ($Task in $Plan.tasks) {
    $NextActions += ('## ' + $Task.task_id + ' — ' + $Task.task_mode)
    $NextActions += ''
    $NextActions += $Task.purpose
    $NextActions += ''
    if ($Task.status -eq 'ready') {
        $NextActions += ('Selected provider: `' + $Task.selected_provider_id + '`')
        $NextActions += 'Create an exact generation request with pinned repository revision, checkpoint/codec hashes, seed, portable conditioning commitments, and a private local adapter.'
    } else {
        $NextActions += ('Blocked: ' + $Task.blocked_reason)
        foreach ($Candidate in $Task.provider_candidates) {
            $NextActions += ('- `' + $Candidate.provider_id + '`: ' + $Candidate.reason)
        }
    }
    $NextActions += ''
}
$NextActions | Set-Content -LiteralPath (Join-Path $Workspace 'LOCAL_NEXT_ACTIONS.md') -Encoding utf8

Write-Host ""
Write-Host "Generative floor workspace: $Workspace"
Write-Host "Campaign: $PlanPath"
Write-Host "Next actions: $(Join-Path $Workspace 'LOCAL_NEXT_ACTIONS.md')"
if ($ProbeOnly) {
    Write-Host 'Probe-only boundary requested; no provider execution performed.'
} else {
    Write-Host 'No model is executed automatically. Exact requests and local private adapters are required before each run.'
}
