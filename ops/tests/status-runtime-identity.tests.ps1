[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$opsRoot = Split-Path -Parent $PSScriptRoot
$statusPath = Join-Path $opsRoot 'status.ps1'
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $statusPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) { throw 'Status script contains parser errors' }
$functionNames = @(
    'Read-RuntimeDeploymentId',
    'Test-RuntimeIdentityFile'
)
$functions = $ast.FindAll(
    {
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -in $functionNames
    },
    $true
) | Sort-Object { $_.Extent.StartOffset }
if ($functions.Count -ne $functionNames.Count) {
    throw 'Status runtime identity functions were not found'
}
. ([ScriptBlock]::Create(
    (($functions | ForEach-Object { $_.Extent.Text }) -join "`r`n")
))

$testLeaf = 'CoastalWarning-StatusIdentity-Test-' +
    [Guid]::NewGuid().ToString('N')
$testRoot = Join-Path ([IO.Path]::GetTempPath()) $testLeaf
$testRootFull = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
$tempRootFull = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')

try {
    [void][IO.Directory]::CreateDirectory($testRootFull)
    $deploymentFile = Join-Path $testRootFull 'deployment-id.txt'
    $identityFile = Join-Path $testRootFull 'identity.json'
    $deploymentId = 'a' * 32
    [IO.File]::WriteAllText($deploymentFile, "$deploymentId`r`n")
    [IO.File]::WriteAllText(
        $identityFile,
        '{"deployment_id":"' + $deploymentId +
            '","pid":1234,"port":8000}'
    )

    $loadedDeploymentId = Read-RuntimeDeploymentId -Path $deploymentFile
    if ($loadedDeploymentId -cne $deploymentId) {
        throw 'Valid status deployment identifier was rejected'
    }
    if (-not (Test-RuntimeIdentityFile -Path $identityFile `
            -DeploymentId $deploymentId -Port 8000 -ListenerPid 1234)) {
        throw 'Valid status runtime identity was rejected'
    }
    if (Test-RuntimeIdentityFile -Path $identityFile `
        -DeploymentId $deploymentId -Port 8001 -ListenerPid 1234) {
        throw 'Status accepted an identity for the wrong port'
    }
    if (Test-RuntimeIdentityFile -Path $identityFile `
        -DeploymentId $deploymentId -Port 8000 -ListenerPid 4321) {
        throw 'Status accepted an identity for the wrong listener PID'
    }

    [IO.File]::WriteAllText($deploymentFile, 'not-a-deployment')
    if ($null -ne (Read-RuntimeDeploymentId -Path $deploymentFile)) {
        throw 'Malformed status deployment identifier was accepted'
    }

    Write-Host 'Status runtime identity fixtures passed.'
}
finally {
    $parentFull = [IO.Path]::GetFullPath(
        (Split-Path -Parent $testRootFull)
    ).TrimEnd('\')
    $leaf = Split-Path -Leaf $testRootFull
    if (-not [string]::Equals(
            $parentFull,
            $tempRootFull,
            [StringComparison]::OrdinalIgnoreCase
        ) -or $leaf -cnotmatch
            '^CoastalWarning-StatusIdentity-Test-[0-9a-f]{32}$') {
        throw "Refusing unsafe fixture cleanup path: $testRootFull"
    }
    if (Test-Path -LiteralPath $testRootFull -PathType Container) {
        [IO.Directory]::Delete($testRootFull, $true)
    }
}
