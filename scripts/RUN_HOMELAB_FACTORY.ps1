[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Catalog,
    [Parameter(Mandatory=$true)][string]$Audit,
    [Parameter(Mandatory=$true)][string[]]$Bindings,
    [Parameter(Mandatory=$true)][string]$Workspace,
    [ValidateSet('smoke','core','full')][string]$Profile = 'core',
    [string[]]$Case = @(),
    [string[]]$Gpu = @(),
    [string[]]$AdapterPolicy = @(),
    [string]$Store,
    [int]$MaxRecipesPerCase = 12,
    [int]$MaxParallelCpu = 2,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python).Source
$Factory = Join-Path $Repo 'scripts\earcrate_factory.py'
$Suite = Join-Path $Repo 'configs\homelab_factory\specimen-suite.v1.json'
$RolePolicy = Join-Path $Repo 'configs\homelab_factory\provider-role-policy.v1.json'
$DefaultAdapters = Join-Path $Repo 'configs\homelab_factory\provider-adapters.v1.json'

if (-not (Test-Path -LiteralPath $Factory -PathType Leaf)) { throw "Factory CLI missing: $Factory" }
if (-not (Test-Path -LiteralPath $Suite -PathType Leaf)) { throw "Specimen suite missing: $Suite" }
if (-not (Test-Path -LiteralPath $RolePolicy -PathType Leaf)) { throw "Role policy missing: $RolePolicy" }

if ($VerifyOnly) {
    & $Python $Factory verify --workspace $Workspace
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $Workspace)) {
    $args = @(
        $Factory, 'bootstrap',
        '--suite', $Suite,
        '--catalog', $Catalog,
        '--audit', $Audit,
        '--role-policy', $RolePolicy,
        '--workspace', $Workspace,
        '--profile', $Profile,
        '--max-recipes-per-case', $MaxRecipesPerCase
    )
    foreach ($path in $Bindings) { $args += @('--bindings', $path) }
    foreach ($caseId in $Case) { $args += @('--case', $caseId) }
    & $Python @args
    if ($LASTEXITCODE -ne 0) { throw "Factory bootstrap failed" }
}

$runArgs = @($Factory, 'run', '--workspace', $Workspace, '--max-parallel-cpu', $MaxParallelCpu)
$runArgs += @('--adapter-policy', $DefaultAdapters)
foreach ($path in $AdapterPolicy) { $runArgs += @('--adapter-policy', $path) }
foreach ($device in $Gpu) { $runArgs += @('--gpu', $device) }
& $Python @runArgs
if ($LASTEXITCODE -ne 0) { throw "Factory execution failed" }

& $Python $Factory verify --workspace $Workspace
if ($LASTEXITCODE -ne 0) { throw "Factory verification failed" }

if ($Store) {
    & $Python $Factory sync-store --workspace $Workspace --store $Store
    if ($LASTEXITCODE -ne 0) { throw "Factory store synchronization failed" }
}

Write-Host ""
Write-Host "Factory workspace: $Workspace"
Write-Host "Listen only through each reviews\<case>\public directory."
Write-Host "Private mappings and tokens remain under reviews\<case>\private."
Write-Host "After review, run: python scripts\earcrate_factory.py review --workspace <workspace> --case <case-id> --choice <A|B|...> --dimensions-json '{...}'"
