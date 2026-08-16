[CmdletBinding(DefaultParameterSetName='Scaffold')]
param(
    [Parameter(Mandatory=$true)][string]$Workspace,
    [Parameter(ParameterSetName='Scaffold', Mandatory=$true)][string]$ParentReviewReceipt,
    [Parameter(ParameterSetName='VerifyReturn', Mandatory=$true)][switch]$VerifyReturn,
    [Parameter(ParameterSetName='VerifyReturn', Mandatory=$true)][string]$ReturnLedger
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python).Source
$Cli = Join-Path $Repo 'scripts\earcrate_a1_07_gold_v7.py'
$Contract = Join-Path $Repo 'configs\album_one\a1-07\gold-v7-iteration.v1.json'

if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw "A1-07 gold-v7 CLI missing: $Cli"
}
if (-not (Test-Path -LiteralPath $Contract -PathType Leaf)) {
    throw "A1-07 gold-v7 contract missing: $Contract"
}

& $Python $Cli --contract $Contract verify-contract
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v7 contract verification failed" }

if ($VerifyReturn) {
    & $Python $Cli --contract $Contract verify-return --ledger $ReturnLedger
    if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v7 Estate return verification failed" }
    exit 0
}

& $Python $Cli --contract $Contract verify-parent --receipt $ParentReviewReceipt
if ($LASTEXITCODE -ne 0) { throw "Wrong gold-v6 owner-review receipt" }

& $Python $Cli --contract $Contract scaffold --workspace $Workspace
if ($LASTEXITCODE -ne 0) { throw "A1-07 gold-v7 workspace scaffold failed" }

Write-Host "A1-07 gold-v7 workspace prepared: $Workspace"
Write-Host "Execute NEXT_ACTIONS.md in order. Do not prepare an owner frontier unless at least two children machine-qualify."
