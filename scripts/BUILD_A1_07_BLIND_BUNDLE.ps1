[CmdletBinding()]
param(
    [string]$Frontier = "D:\Projects\Products\EarCrate\sessions\a1-07-full-form-v1\frontier",
    [string]$Output = "D:\Projects\Products\EarCrate\sessions\a1-07-full-form-v1\blind-bundle",
    [switch]$Zip
)

# Assembles ONLY the seven files the reviewer may see. Everything that could decode
# the blind -- the label nonce, the label map, private receipts, CORE material,
# stems, source recordings, the adapter manifest -- stays behind.
$ErrorActionPreference = 'Stop'
$Public = Join-Path $Frontier 'review\public'
if (-not (Test-Path -LiteralPath $Public -PathType Container)) { throw "review pack missing: $Public" }

if (Test-Path -LiteralPath $Output) { Remove-Item -LiteralPath $Output -Recurse -Force }
New-Item -ItemType Directory -Path $Output | Out-Null

foreach ($name in @('A.flac', 'B.flac', 'C.flac', 'INCUMBENT.flac')) {
    $src = Join-Path $Public $name
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "missing review cut: $src" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $Output $name)
}

# CUT_NOTES.md -> CUT_NOTES.txt, verbatim.
Copy-Item -LiteralPath (Join-Path $Public 'CUT_NOTES.md') -Destination (Join-Path $Output 'CUT_NOTES.txt')

# REVIEW.txt: the verdict form, derived from the public assignment only.
$assignment = Get-Content -LiteralPath (Join-Path $Public 'assignment.json') -Raw | ConvertFrom-Json
$levels = foreach ($k in @('A', 'B', 'C', 'INCUMBENT')) {
    $o = $assignment.options.$k
    '  {0,-10} {1,7} LUFS   peak {2,5} dBFS' -f $k, $o.output_lufs, $o.output_peak_dbfs
}
$lines = @(
    'A1-07 FULL-FORM v1 - BLIND OWNER REVIEW',
    '',
    'A, B and C are the same 56.111 s form built from the same sources, the same',
    'lead-vocal rows and the same authored body. The ONLY difference between them is',
    'the donor-band timing law.',
    '',
    'INCUMBENT is the retained 38.15 s arc. It is the disclosed control, not an option.',
    'It is shorter, it has no body, and it carries its own clipping. The three options',
    'do not: every one of them renders with zero full-scale runs.',
    '',
    'Delivered levels (matched, so loudness is not a variable):'
) + $levels + @(
    '',
    'Choose the option whose terminal calls land and whose percussion adds force',
    'without reducing the lead vocal to an effect, and whose groove survives setup,',
    'development and payoff.',
    '',
    ('ADMISSIBLE VERDICTS: ' + ($assignment.choices -join ', ')),
    '',
    'SCORE EACH OPTION ON THESE DIMENSIONS:',
    ''
)
foreach ($d in $assignment.dimensions) { $lines += ('  - ' + $d + ':   A __  B __  C __  INCUMBENT __') }
$lines += @(
    '',
    'VERDICT: ______________',
    '',
    'COMPARATIVE NOTES vs INCUMBENT:',
    '',
    '',
    'NOTE: a verdict selects an owner frontier. It does not accept an album master.',
    'A tie is a real answer, and reject_all closes this timing family rather than',
    'inviting another nearby specimen.'
)
$lines | Set-Content -LiteralPath (Join-Path $Output 'REVIEW.txt') -Encoding utf8

# MANIFEST.sha256 over exactly what ships.
$manifest = Get-ChildItem -LiteralPath $Output -File | Sort-Object Name | ForEach-Object {
    "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()) *$($_.Name)"
}
$manifest | Set-Content -LiteralPath (Join-Path $Output 'MANIFEST.sha256') -Encoding utf8

# Refuse to ship anything that could decode the blind.
$allowed = @('A.flac', 'B.flac', 'C.flac', 'INCUMBENT.flac', 'REVIEW.txt', 'CUT_NOTES.txt', 'MANIFEST.sha256')
$actual = (Get-ChildItem -LiteralPath $Output -Recurse -File | Select-Object -ExpandProperty Name) | Sort-Object
$extra = $actual | Where-Object { $allowed -notcontains $_ }
if ($extra) { throw "bundle contains files outside the allowed set: $($extra -join ', ')" }
foreach ($text in @('REVIEW.txt', 'CUT_NOTES.txt')) {
    $body = Get-Content -LiteralPath (Join-Path $Output $text) -Raw
    foreach ($leak in @('single-speed', 'native-pocket', 'phrase-reset')) {
        if ($body -match ("(?m)^\s*[ABC]\b.*" + [regex]::Escape($leak))) {
            throw "$text appears to map a letter to $leak"
        }
    }
    if ($body -match 'nonce' -or $body -match 'authority\.json') { throw "$text references the label authority" }
}

Write-Host "Blind bundle: $Output"
Get-ChildItem -LiteralPath $Output -File | Select-Object Name, Length | Format-Table -AutoSize
if ($Zip) {
    $archive = "$Output.zip"
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
    Compress-Archive -Path (Join-Path $Output '*') -DestinationPath $archive
    Write-Host "Archive: $archive  ($((Get-Item $archive).Length) bytes)"
    Write-Host "SHA256:  $((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower())"
}
