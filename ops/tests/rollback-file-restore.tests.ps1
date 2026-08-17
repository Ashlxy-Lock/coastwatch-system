[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$opsRoot = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $opsRoot 'install-startup.ps1'
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $installerPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) { throw 'Installer contains parser errors' }
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

# Production installation is elevated and exercises takeown plus protected
# DACLs. This fixture replaces only the privilege boundary so the atomic
# temporary-file replacement and absent-file rollback can run unprivileged.
function Protect-Path {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [bool]$IsDirectory,
        [switch]$IncludeCurrentUser,
        [switch]$Recurse
    )

    if (-not $IsDirectory -and (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $attributes = [IO.File]::GetAttributes($Path)
        if (($attributes -band [IO.FileAttributes]::ReadOnly) -ne 0) {
            [IO.File]::SetAttributes(
                $Path,
                $attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
            )
        }
    }
}

function Grant-CoastalRollbackFileAccess {
    param([Parameter(Mandatory)] [string]$Path)

    Assert-RegularFile -Path $Path
    Protect-Path -Path $Path -IsDirectory $false -IncludeCurrentUser
}

$testLeaf = 'CoastalWarning-FileRollback-Test-' +
    [Guid]::NewGuid().ToString('N')
$testRoot = Join-Path ([IO.Path]::GetTempPath()) $testLeaf
$testRootFull = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
$tempRootFull = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$originalBytes = $null

try {
    [void][IO.Directory]::CreateDirectory($testRootFull)
    $target = Join-Path $testRootFull 'credential.json'
    $originalBytes = [Text.Encoding]::UTF8.GetBytes('original-secret-fixture')
    [IO.File]::WriteAllBytes($target, $originalBytes)
    $snapshot = [pscustomobject]@{
        Path = $target
        Exists = $true
        Bytes = $originalBytes
        Attributes = [IO.FileAttributes]::Normal
    }
    [IO.File]::WriteAllText($target, 'mutated')
    [IO.File]::SetAttributes($target, [IO.FileAttributes]::ReadOnly)

    Restore-CoastalFileSnapshot -Snapshot $snapshot
    $restored = [IO.File]::ReadAllBytes($target)
    if ([Convert]::ToBase64String($originalBytes) -cne
        [Convert]::ToBase64String($restored)) {
        throw 'Atomic snapshot restore did not recover the original bytes'
    }
    $leftover = @(Get-ChildItem -LiteralPath $testRootFull -Force |
        Where-Object Name -Like '.coastal-rollback-*.tmp')
    if ($leftover.Count -ne 0) {
        throw 'Atomic snapshot restore left a temporary file behind'
    }

    $absentSnapshot = [pscustomobject]@{
        Path = $target
        Exists = $false
        Bytes = $null
        Attributes = $null
    }
    Restore-CoastalFileSnapshot -Snapshot $absentSnapshot
    if (Test-Path -LiteralPath $target) {
        throw 'Absent-file rollback did not remove the newly created file'
    }

    Write-Host 'Rollback file replacement fixtures passed.'
}
finally {
    if ($null -ne $originalBytes) {
        [Array]::Clear($originalBytes, 0, $originalBytes.Length)
    }
    $parentFull = [IO.Path]::GetFullPath(
        (Split-Path -Parent $testRootFull)
    ).TrimEnd('\')
    $leaf = Split-Path -Leaf $testRootFull
    if (-not [string]::Equals(
            $parentFull,
            $tempRootFull,
            [StringComparison]::OrdinalIgnoreCase
        ) -or $leaf -cnotmatch
            '^CoastalWarning-FileRollback-Test-[0-9a-f]{32}$') {
        throw "Refusing unsafe fixture cleanup path: $testRootFull"
    }
    if (Test-Path -LiteralPath $testRootFull -PathType Container) {
        [IO.Directory]::Delete($testRootFull, $true)
    }
}
