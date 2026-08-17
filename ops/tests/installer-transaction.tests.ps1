[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$opsRoot = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $opsRoot 'install-startup.ps1'
$content = [IO.File]::ReadAllText($installerPath)
$operationMarker = '$taskNames = @('
$operationIndex = $content.IndexOf(
    $operationMarker,
    [StringComparison]::Ordinal
)
if ($operationIndex -lt 0) { throw 'Installer operation marker not found' }
$operation = $content.Substring($operationIndex)

function Get-RequiredIndex {
    param(
        [Parameter(Mandatory)] [string]$Text,
        [Parameter(Mandatory)] [string]$Needle
    )

    $index = $Text.IndexOf($Needle, [StringComparison]::Ordinal)
    if ($index -lt 0) { throw "Required installer step not found: $Needle" }
    return $index
}

$stageBuild = Get-RequiredIndex -Text $operation `
    -Needle '& $systemPython -m venv'
$riskModelCopy = Get-RequiredIndex -Text $operation `
    -Needle 'Copy-Item -LiteralPath $sourceRiskModel -Destination $stagingRiskModel'
$stageValidation = Get-RequiredIndex -Text $operation `
    -Needle 'Test-StagedRuntime -ServerDirectory $stagingServer'
$stopTasks = Get-RequiredIndex -Text $operation `
    -Needle 'Stop-CoastalTasks -TaskNames $taskNames'
$portsReleased = Get-RequiredIndex -Text $operation `
    -Needle 'Wait-CoastalPortsReleased -Ports @(8000, 8001)'
$switchRuntime = Get-RequiredIndex -Text $operation `
    -Needle '$hadPreviousRuntime = Switch-CoastalRuntime'
$healthCheck = Get-RequiredIndex -Text $operation `
    -Needle "throw 'Gateway startup health check failed'"
$processCheck = Get-RequiredIndex -Text $operation `
    -Needle 'Assert-CoastalRuntimeProcesses -RuntimeServerDirectory $runtimeServer'
$markSuccess = $operation.IndexOf(
    '-Path $previousRoot -Kind Previous',
    $healthCheck,
    [StringComparison]::Ordinal
)
if ($markSuccess -lt 0) {
    throw 'Previous runtime cleanup after health was not found'
}
$rollback = Get-RequiredIndex -Text $operation `
    -Needle 'Restore-CoastalRuntime -ProgramRoot $programRoot'
$cloudStop = $operation.IndexOf(
    "Stop-Service -Name 'cloudflared' -Force",
    $rollback,
    [StringComparison]::Ordinal
)
if ($cloudStop -lt 0) { throw 'Rollback cloudflared stop was not found' }
$fileRestore = Get-RequiredIndex -Text $operation `
    -Needle 'Restore-CoastalFileSnapshot -Snapshot $snapshot'
$taskRestore = Get-RequiredIndex -Text $operation `
    -Needle 'Restore-CoastalTaskDefinitions -Snapshots $taskSnapshots'
$cloudRestore = Get-RequiredIndex -Text $operation `
    -Needle 'Restore-CloudflaredSnapshot -Snapshot $cloudflaredSnapshot'

if (-not ($riskModelCopy -lt $stageBuild -and
        $stageBuild -lt $stageValidation -and
        $stageValidation -lt $stopTasks -and
        $stopTasks -lt $portsReleased -and
        $portsReleased -lt $switchRuntime -and
        $switchRuntime -lt $healthCheck -and
        $healthCheck -lt $processCheck -and
        $processCheck -lt $markSuccess -and
        $markSuccess -lt $rollback -and
        $rollback -lt $cloudStop -and
        $cloudStop -lt $fileRestore -and
        $fileRestore -lt $taskRestore -and
        $rollback -lt $taskRestore -and
        $taskRestore -lt $cloudRestore)) {
    throw 'Installer staging, commit, health, cleanup, and rollback order is unsafe'
}
if ($content.Contains('Remove-Item -LiteralPath $runtimeRoot')) {
    throw 'Installer still recursively removes the active runtime'
}
if ($content.Contains('-Destination $runtimeRoot -Recurse')) {
    throw 'Installer still copies files directly into the active runtime'
}
foreach ($runScriptName in @('run-main.ps1', 'run-gateway.ps1')) {
    $runScriptPath = Join-Path $opsRoot $runScriptName
    $runScript = [IO.File]::ReadAllText($runScriptPath)
    if (-not $runScript.Contains('--app-dir $serverDir')) {
        throw "$runScriptName does not pass the explicit runtime app directory"
    }
}
if (-not $content.Contains("raise RuntimeError('staged risk model is not ready')") -or
    -not $content.Contains('-RiskModelSha256 $sourceRiskModelHash')) {
    throw 'Staged validation does not require the copied risk model artifact'
}

$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $installerPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw 'Installer contains PowerShell parser errors'
}
$transcriptCommands = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.CommandAst] -and
                $node.GetCommandName() -eq 'Start-Transcript'
        },
        $true
    )
)
if ($transcriptCommands.Count -ne 0) {
    throw 'Installer must not start a transcript containing credential parameters'
}

$testFunctionNames = @(
    'Test-CoastalExecutablePath',
    'Test-CoastalRuntimeIdentityPayload'
)
$testFunctions = $ast.FindAll(
    {
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -in $testFunctionNames
    },
    $true
) | Sort-Object { $_.Extent.StartOffset }
. ([ScriptBlock]::Create(
    (($testFunctions | ForEach-Object { $_.Extent.Text }) -join "`r`n")
))

$deploymentId = 'a' * 32
$validIdentity = [pscustomobject]@{
    deployment_id = $deploymentId
    pid = 1234
    port = 8000
}
if (-not (Test-CoastalRuntimeIdentityPayload -Identity $validIdentity `
        -DeploymentId $deploymentId -Port 8000 -ListenerPid 1234)) {
    throw 'Valid runtime nonce fixture was rejected'
}
foreach ($invalidIdentity in @(
        [pscustomobject]@{
            deployment_id = 'b' * 32
            pid = 1234
            port = 8000
        },
        [pscustomobject]@{
            deployment_id = $deploymentId
            pid = 4321
            port = 8000
        }
    )) {
    if (Test-CoastalRuntimeIdentityPayload -Identity $invalidIdentity `
        -DeploymentId $deploymentId -Port 8000 -ListenerPid 1234) {
        throw 'Invalid runtime nonce fixture was accepted'
    }
}
if (Test-CoastalExecutablePath -ObservedPath $null `
    -ExpectedPath 'C:\runtime\python.exe') {
    throw 'Unreadable process path was incorrectly treated as identity proof'
}
if (-not (Test-CoastalExecutablePath `
        -ObservedPath 'C:\runtime\python.exe' `
        -ExpectedPath 'C:\runtime\python.exe')) {
    throw 'Matching diagnostic process path was rejected'
}

Write-Host 'Installer transaction ordering and rollback static checks passed.'
