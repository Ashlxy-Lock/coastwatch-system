[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$opsRoot = Split-Path -Parent $PSScriptRoot
$expected = @{
    COAST_OFFICIAL_DATASET_ROOT = 'C:\ProgramData\CoastalWarning\data\official_datasets'
    COAST_OFFICIAL_REGISTRY_DIR = 'C:\ProgramData\CoastalWarning\data\official_registry'
    COAST_OFFICIAL_ARTIFACT_DIR = 'C:\ProgramData\CoastalWarning\models\official_runs'
}

foreach ($wrapperName in @('run-main.ps1', 'run-gateway.ps1')) {
    $path = Join-Path $opsRoot $wrapperName
    $source = [IO.File]::ReadAllText($path)
    foreach ($pair in $expected.GetEnumerator()) {
        if (-not $source.Contains("`$env:$($pair.Key) =")) {
            throw "$wrapperName does not set $($pair.Key)"
        }
        if (-not $source.Contains("Remove-Item Env:$($pair.Key)")) {
            throw "$wrapperName does not clear $($pair.Key)"
        }
        if (-not $source.Contains($pair.Value)) {
            throw "$wrapperName does not pin $($pair.Key) to ProgramData"
        }
    }
}

$installer = [IO.File]::ReadAllText((Join-Path $opsRoot 'install-startup.ps1'))
foreach ($name in $expected.Keys) {
    if (-not $installer.Contains("'$name'")) {
        throw "installer does not preserve $name during staged smoke"
    }
    if (-not $installer.Contains("`$env:$name =")) {
        throw "installer staged smoke does not set $name"
    }
}
foreach ($leaf in @('official_datasets', 'official_registry', 'official_runs')) {
    if (-not $installer.Contains("'$leaf'")) {
        throw "installer does not create protected $leaf directory"
    }
}

Write-Host 'Official dataset, registry, and artifact runtime paths are pinned.'
