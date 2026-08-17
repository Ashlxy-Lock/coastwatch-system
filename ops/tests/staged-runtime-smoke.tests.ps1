[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$opsRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $opsRoot
$installerPath = Join-Path $opsRoot 'install-startup.ps1'
$serverDirectory = Join-Path $projectRoot 'server'
$pythonExe = Join-Path $serverDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Development virtual environment not found: $pythonExe"
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
$functionDefinitions = $ast.FindAll(
    {
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst]
    },
    $true
) | Sort-Object { $_.Extent.StartOffset }
. ([ScriptBlock]::Create(
    (($functionDefinitions | ForEach-Object { $_.Extent.Text }) -join "`r`n")
))

$databaseFile = Join-Path $serverDirectory 'data\coastal_warning.db'
$modelDir = Join-Path $serverDirectory 'models'
$officialDatasetRoot = Join-Path $serverDirectory 'data\official_datasets'
$officialRegistryDir = Join-Path $serverDirectory 'data\official_registry'
$officialArtifactDir = Join-Path $modelDir 'official_runs'
$riskModelFile = Join-Path $modelDir 'coastal_risk_v1.json'
$riskModelSha256 = (
    Get-FileHash -LiteralPath $riskModelFile -Algorithm SHA256
).Hash
$deploymentIdFile = Join-Path ([IO.Path]::GetTempPath()) (
    'CoastalWarning-StagedSmoke-' + [Guid]::NewGuid().ToString('N') + '.txt'
)
[IO.File]::WriteAllText(
    $deploymentIdFile,
    [Guid]::NewGuid().ToString('N'),
    [Text.ASCIIEncoding]::new()
)
$password = ConvertTo-SecureString 'staged-runtime-test-only' `
    -AsPlainText -Force
$verifier = New-AdminPasswordVerifier -Password $password
$sessionBytes = New-CryptographicBytes -Count 32
try {
    $sessionSecret = ConvertTo-Base64Url -Bytes $sessionBytes
    Test-StagedRuntime -ServerDirectory $serverDirectory `
        -RuntimeOpsDirectory $opsRoot `
        -DeploymentIdFile $deploymentIdFile `
        -RiskModelSha256 $riskModelSha256 `
        -PythonExe $pythonExe `
        -DeviceTokenValue ('d' * 32) `
        -AdminPasswordVerifier $verifier `
        -AdminSessionSecret $sessionSecret
}
finally {
    [Array]::Clear($sessionBytes, 0, $sessionBytes.Length)
    $verifier = $null
    $sessionSecret = $null
    $password = $null
    $riskModelSha256 = $null
    $deploymentParent = [IO.Path]::GetFullPath(
        (Split-Path -Parent $deploymentIdFile)
    ).TrimEnd('\')
    $tempParent = [IO.Path]::GetFullPath(
        [IO.Path]::GetTempPath()
    ).TrimEnd('\')
    $deploymentLeaf = Split-Path -Leaf $deploymentIdFile
    if (-not [string]::Equals(
            $deploymentParent,
            $tempParent,
            [StringComparison]::OrdinalIgnoreCase
        ) -or $deploymentLeaf -cnotmatch
            '^CoastalWarning-StagedSmoke-[0-9a-f]{32}\.txt$') {
        throw "Refusing unsafe staged smoke cleanup: $deploymentIdFile"
    }
    Remove-Item -LiteralPath $deploymentIdFile -Force -ErrorAction SilentlyContinue
}

Write-Host 'Staged runtime dependency, compile, and import checks passed.'
