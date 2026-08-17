[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$modulePath = Join-Path (Split-Path -Parent $PSScriptRoot) `
    'runtime-deployment.psm1'
Import-Module -Name $modulePath -Force

function Assert-Test {
    param(
        [Parameter(Mandatory)] [bool]$Condition,
        [Parameter(Mandatory)] [string]$Message
    )

    if (-not $Condition) { throw $Message }
}

function New-TestRuntime {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Marker
    )

    [void][IO.Directory]::CreateDirectory($Path)
    [IO.File]::WriteAllText(
        (Join-Path $Path 'marker.txt'),
        $Marker,
        [Text.UTF8Encoding]::new($false)
    )
}

$testLeaf = 'CoastalWarning-RuntimeDeployment-Test-' +
    [Guid]::NewGuid().ToString('N')
$testRoot = Join-Path ([IO.Path]::GetTempPath()) $testLeaf
$testRootFull = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
$tempRootFull = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')

try {
    [void][IO.Directory]::CreateDirectory($testRootFull)
    $runtime = Join-Path $testRootFull 'runtime'
    $stage = Join-Path $testRootFull `
        ('runtime.stage.' + [Guid]::NewGuid().ToString('N'))
    $previous = Join-Path $testRootFull `
        ('runtime.previous.' + [Guid]::NewGuid().ToString('N'))

    New-TestRuntime -Path $runtime -Marker 'old'
    New-TestRuntime -Path $stage -Marker 'new'
    $hadPrevious = Switch-CoastalRuntime -ProgramRoot $testRootFull `
        -StagingPath $stage -RuntimePath $runtime -PreviousPath $previous
    Assert-Test -Condition $hadPrevious -Message 'Previous runtime was not detected'
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $runtime 'marker.txt')) -ceq 'new'
    ) -Message 'Staged runtime did not become active'
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $previous 'marker.txt')) -ceq 'old'
    ) -Message 'Previous runtime was not preserved'

    Restore-CoastalRuntime -ProgramRoot $testRootFull -RuntimePath $runtime `
        -PreviousPath $previous -FailedStagingPath $stage `
        -HadPrevious $hadPrevious
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $runtime 'marker.txt')) -ceq 'old'
    ) -Message 'Rollback did not restore the previous runtime'
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $stage 'marker.txt')) -ceq 'new'
    ) -Message 'Rollback did not isolate the failed runtime'
    Remove-CoastalTransientRuntime -ProgramRoot $testRootFull -Path $stage `
        -Kind Stage

    New-TestRuntime -Path $stage -Marker 'candidate'
    $switchFailed = $false
    try {
        [void](Switch-CoastalRuntime -ProgramRoot $testRootFull `
            -StagingPath $stage -RuntimePath $runtime -PreviousPath $previous `
            -AfterPreviousMoved { throw 'injected switch failure' })
    }
    catch {
        $switchFailed = $_.Exception.Message -eq 'injected switch failure'
    }
    Assert-Test -Condition $switchFailed -Message 'Switch failure was not injected'
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $runtime 'marker.txt')) -ceq 'old'
    ) -Message 'Failed switch did not restore the active runtime'
    Assert-Test -Condition (-not (Test-Path -LiteralPath $previous)) `
        -Message 'Failed switch left a previous directory behind'
    Remove-CoastalTransientRuntime -ProgramRoot $testRootFull -Path $stage `
        -Kind Stage

    New-TestRuntime -Path $stage -Marker 'candidate-2'
    $hadPrevious = Switch-CoastalRuntime -ProgramRoot $testRootFull `
        -StagingPath $stage -RuntimePath $runtime -PreviousPath $previous
    $restoreFailed = $false
    try {
        Restore-CoastalRuntime -ProgramRoot $testRootFull -RuntimePath $runtime `
            -PreviousPath $previous -FailedStagingPath $stage `
            -HadPrevious $hadPrevious `
            -BeforePreviousRestore { throw 'injected restore failure' }
    }
    catch {
        $restoreFailed = $_.Exception.Message -eq 'injected restore failure'
    }
    Assert-Test -Condition $restoreFailed -Message 'Restore failure was not injected'
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $runtime 'marker.txt')) -ceq 'old'
    ) -Message 'Restore retry did not recover the previous runtime'
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $stage 'marker.txt')) -ceq 'candidate-2'
    ) -Message 'Restore retry did not isolate the failed runtime'
    Remove-CoastalTransientRuntime -ProgramRoot $testRootFull -Path $stage `
        -Kind Stage

    # First-install rollback has no previous runtime; it must remove the fixed
    # active name by renaming the failed candidate back to its verified stage.
    [IO.Directory]::Delete($runtime, $true)
    New-TestRuntime -Path $stage -Marker 'first-install-candidate'
    $hadPrevious = Switch-CoastalRuntime -ProgramRoot $testRootFull `
        -StagingPath $stage -RuntimePath $runtime -PreviousPath $previous
    Assert-Test -Condition (-not $hadPrevious) `
        -Message 'First-install switch incorrectly reported a previous runtime'
    Restore-CoastalRuntime -ProgramRoot $testRootFull -RuntimePath $runtime `
        -PreviousPath $previous -FailedStagingPath $stage `
        -HadPrevious $hadPrevious
    Assert-Test -Condition (-not (Test-Path -LiteralPath $runtime)) `
        -Message 'First-install rollback left a failed active runtime'
    Assert-Test -Condition (
        [IO.File]::ReadAllText((Join-Path $stage 'marker.txt')) -ceq
            'first-install-candidate'
    ) -Message 'First-install rollback did not isolate the failed runtime'
    Remove-CoastalTransientRuntime -ProgramRoot $testRootFull -Path $stage `
        -Kind Stage

    $rejected = $false
    try {
        [void](Assert-CoastalRuntimePath -ProgramRoot $testRootFull `
            -Path (Join-Path $testRootFull '..\runtime.stage.bad') -Kind Stage)
    }
    catch {
        $rejected = $true
    }
    Assert-Test -Condition $rejected `
        -Message 'Out-of-root deployment path was not rejected'

    Write-Host 'Runtime deployment switch and rollback tests passed.'
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
            '^CoastalWarning-RuntimeDeployment-Test-[0-9a-f]{32}$') {
        throw "Refusing unsafe test cleanup path: $testRootFull"
    }
    if (Test-Path -LiteralPath $testRootFull -PathType Container) {
        [IO.Directory]::Delete($testRootFull, $true)
    }
    Remove-Module runtime-deployment -ErrorAction SilentlyContinue
}
