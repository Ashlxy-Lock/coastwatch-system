[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$opsRoot = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $opsRoot 'install-startup.ps1'
$content = [IO.File]::ReadAllText($installerPath)
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $installerPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) { throw 'Installer contains parser errors' }

$functionNames = @(
    'Assert-RegularFile',
    'Ensure-SafeDirectory',
    'Get-CoastalInstallErrorLogPath',
    'ConvertTo-CoastalSafeDiagnosticText',
    'Write-CoastalInstallFailureLog',
    'Remove-CoastalInstallFailureLog'
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
    throw 'Installer diagnostic functions were not found'
}
. ([ScriptBlock]::Create(
    (($functions | ForEach-Object { $_.Extent.Text }) -join "`r`n")
))

$writerAst = $functions | Where-Object Name -EQ 'Write-CoastalInstallFailureLog'
foreach ($forbiddenText in @(
        'InvocationInfo',
        'PositionMessage',
        'PSBoundParameters',
        'GetEnvironmentVariables',
        'Get-ChildItem Env:',
        'Get-Item Env:',
        'CommandLine'
    )) {
    if ($writerAst.Extent.Text.IndexOf(
            $forbiddenText,
            [StringComparison]::OrdinalIgnoreCase
        ) -ge 0) {
        throw "Diagnostic writer includes forbidden context: $forbiddenText"
    }
}

# Replace only the elevated ACL boundary; atomic replacement, schema, and
# redaction execute exactly as production code does.
function Protect-Path {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [bool]$IsDirectory,
        [switch]$IncludeCurrentUser,
        [switch]$Recurse
    )

    if (-not $IsDirectory -and (Test-Path -LiteralPath $Path -PathType Leaf)) {
        [IO.File]::SetAttributes($Path, [IO.FileAttributes]::Normal)
    }
}

function Grant-CoastalRollbackFileAccess {
    param([Parameter(Mandatory)] [string]$Path)

    Assert-RegularFile -Path $Path
    [IO.File]::SetAttributes($Path, [IO.FileAttributes]::Normal)
}

$testLeaf = 'CoastalWarning-InstallDiagnostic-Test-' +
    [Guid]::NewGuid().ToString('N')
$testRoot = Join-Path ([IO.Path]::GetTempPath()) $testLeaf
$testRootFull = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
$tempRootFull = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$logPath = Join-Path (Join-Path $testRootFull 'logs') 'install-error.log'

function Invoke-SensitiveOuterFixture {
    param(
        [Parameter(Mandatory)] [string]$AdminPasswordHash,
        [Parameter(Mandatory)] [string]$FailureMessage
    )

    if ([string]::IsNullOrEmpty($AdminPasswordHash)) {
        throw 'Fixture requires a verifier'
    }
    throw [InvalidOperationException]::new($FailureMessage)
}

try {
    $verifier = 'pbkdf2_sha256$310000$' + ('a' * 32) + '$' + ('b' * 64)
    $deviceToken = 'D' * 32
    $sessionSecret = 'S' * 43
    $standardBase64 = ('Q' * 42) + '=='
    $message = "fixture failure`r`nverifier=$verifier token=$deviceToken " +
        "session=$sessionSecret opaque=$standardBase64"
    try {
        Invoke-SensitiveOuterFixture -AdminPasswordHash $verifier `
            -FailureMessage $message
    }
    catch {
        $fixtureError = $_
    }
    $cloudflareError = '{"TunnelSecret":"' + $standardBase64 + '"}'
    $rollbackErrors = @(
        "COAST_DEVICE_TOKEN=$deviceToken",
        $cloudflareError
    )
    $sensitiveValues = @($verifier, $deviceToken, $sessionSecret)

    Write-CoastalInstallFailureLog -ProgramRoot $testRootFull `
        -Path $logPath -ErrorRecord $fixtureError `
        -Phase 'runtime_identity_check' `
        -RollbackErrors $rollbackErrors -SensitiveValues $sensitiveValues

    $raw = [IO.File]::ReadAllText($logPath)
    foreach ($secret in @(
            $verifier,
            $deviceToken,
            $sessionSecret,
            $standardBase64
        )) {
        if ($raw.Contains($secret)) {
            throw 'Installer diagnostic persisted a credential value'
        }
    }
    foreach ($credentialLabel in @(
            'COAST_DEVICE_TOKEN=',
            'TunnelSecret',
            'AdminPasswordHash',
            'pbkdf2_sha256$310000$'
        )) {
        if ($raw.IndexOf(
                $credentialLabel,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0) {
            throw "Installer diagnostic persisted a credential field: $credentialLabel"
        }
    }
    $payload = $raw | ConvertFrom-Json -ErrorAction Stop
    $actualFields = @($payload.PSObject.Properties.Name | Sort-Object)
    $expectedFields = @(
        'error_type',
        'message',
        'phase',
        'rollback_errors',
        'schema_version',
        'script_stack_trace',
        'timestamp_utc'
    ) | Sort-Object
    if (($actualFields -join '|') -cne ($expectedFields -join '|')) {
        throw 'Installer diagnostic contains fields outside the safe schema'
    }
    if ($payload.phase -cne 'runtime_identity_check') {
        throw 'Installer diagnostic did not retain the failed phase'
    }
    if ($payload.message -match '[\r\n]') {
        throw 'Installer diagnostic message retained a line break'
    }

    # A second failure must atomically replace, rather than append to, the log.
    try { throw 'replacement fixture' } catch { $replacementError = $_ }
    Write-CoastalInstallFailureLog -ProgramRoot $testRootFull `
        -Path $logPath -ErrorRecord $replacementError `
        -Phase 'health_checks' -SensitiveValues @()
    $replacementPayload = [IO.File]::ReadAllText($logPath) |
        ConvertFrom-Json -ErrorAction Stop
    if ($replacementPayload.phase -cne 'health_checks' -or
        $replacementPayload.message -cne 'replacement fixture') {
        throw 'Installer diagnostic was not atomically replaced'
    }
    $leftovers = @(Get-ChildItem -LiteralPath (Split-Path -Parent $logPath) `
        -Force | Where-Object Name -Like '.install-error-*.tmp')
    if ($leftovers.Count -ne 0) {
        throw 'Installer diagnostic left a temporary file behind'
    }

    Remove-CoastalInstallFailureLog -ProgramRoot $testRootFull -Path $logPath
    if (Test-Path -LiteralPath $logPath) {
        throw 'Successful-install diagnostic cleanup did not remove the log'
    }

    $failedPhaseIndex = $content.IndexOf(
        '$failedPhase = $installPhase',
        [StringComparison]::Ordinal
    )
    $diagnosticCallIndex = $content.LastIndexOf(
        'Write-CoastalInstallFailureLog -ProgramRoot $programRoot',
        [StringComparison]::Ordinal
    )
    $rollbackCleanupIndex = $content.IndexOf(
        'Remove-CoastalTransientRuntime -ProgramRoot $programRoot -Path $stagingRoot',
        $failedPhaseIndex,
        [StringComparison]::Ordinal
    )
    if ($failedPhaseIndex -lt 0 -or $rollbackCleanupIndex -lt 0 -or
        $diagnosticCallIndex -lt $rollbackCleanupIndex) {
        throw 'Installer does not persist the captured failure after rollback'
    }

    Write-Host 'Installer protected diagnostic and redaction fixtures passed.'
}
finally {
    $verifier = $null
    $deviceToken = $null
    $sessionSecret = $null
    $standardBase64 = $null
    $message = $null
    $sensitiveValues = $null
    $parentFull = [IO.Path]::GetFullPath(
        (Split-Path -Parent $testRootFull)
    ).TrimEnd('\')
    $leaf = Split-Path -Leaf $testRootFull
    if (-not [string]::Equals(
            $parentFull,
            $tempRootFull,
            [StringComparison]::OrdinalIgnoreCase
        ) -or $leaf -cnotmatch
            '^CoastalWarning-InstallDiagnostic-Test-[0-9a-f]{32}$') {
        throw "Refusing unsafe fixture cleanup path: $testRootFull"
    }
    if (Test-Path -LiteralPath $testRootFull -PathType Container) {
        [IO.Directory]::Delete($testRootFull, $true)
    }
}
