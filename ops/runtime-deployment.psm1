Set-StrictMode -Version Latest

function Get-CoastalCanonicalPath {
    param([Parameter(Mandatory)] [string]$Path)

    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "Deployment path must be absolute: $Path"
    }
    return [IO.Path]::GetFullPath($Path).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}

function Assert-CoastalNoReparseTree {
    param([Parameter(Mandatory)] [string]$Path)

    $rootItem = Get-Item -LiteralPath $Path -Force
    if (-not $rootItem.PSIsContainer -or
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing unsafe deployment directory: $Path"
    }
    $reparsePoint = Get-ChildItem -LiteralPath $Path -Force -Recurse |
        Where-Object {
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        } |
        Select-Object -First 1
    if ($null -ne $reparsePoint) {
        throw "Refusing deployment tree containing a reparse point: $($reparsePoint.FullName)"
    }
}

function Assert-CoastalRuntimePath {
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string]$ProgramRoot,
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)]
        [ValidateSet('Active', 'Stage', 'Previous')]
        [string]$Kind,
        [switch]$MustExist
    )

    $rootFull = Get-CoastalCanonicalPath -Path $ProgramRoot
    if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
        throw "Program root not found: $rootFull"
    }
    $rootItem = Get-Item -LiteralPath $rootFull -Force
    if (-not $rootItem.PSIsContainer -or
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing unsafe program root: $rootFull"
    }

    $pathFull = Get-CoastalCanonicalPath -Path $Path
    $parentFull = Get-CoastalCanonicalPath -Path (Split-Path -Parent $pathFull)
    if (-not [string]::Equals(
            $parentFull,
            $rootFull,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Deployment path is not a direct child of the program root: $pathFull"
    }

    $leaf = Split-Path -Leaf $pathFull
    $validLeaf = switch ($Kind) {
        'Active' { $leaf -ceq 'runtime' }
        'Stage' { $leaf -cmatch '^runtime\.stage\.[0-9a-f]{32}$' }
        'Previous' { $leaf -cmatch '^runtime\.previous\.[0-9a-f]{32}$' }
    }
    if (-not $validLeaf) {
        throw "Invalid $Kind deployment directory name: $leaf"
    }

    if (Test-Path -LiteralPath $pathFull) {
        Assert-CoastalNoReparseTree -Path $pathFull
    }
    elseif ($MustExist) {
        throw "$Kind deployment directory not found: $pathFull"
    }
    return $pathFull
}

function Switch-CoastalRuntime {
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [string]$ProgramRoot,
        [Parameter(Mandatory)] [string]$StagingPath,
        [Parameter(Mandatory)] [string]$RuntimePath,
        [Parameter(Mandatory)] [string]$PreviousPath,
        [scriptblock]$AfterPreviousMoved
    )

    $stage = Assert-CoastalRuntimePath -ProgramRoot $ProgramRoot `
        -Path $StagingPath -Kind Stage -MustExist
    $active = Assert-CoastalRuntimePath -ProgramRoot $ProgramRoot `
        -Path $RuntimePath -Kind Active
    $previous = Assert-CoastalRuntimePath -ProgramRoot $ProgramRoot `
        -Path $PreviousPath -Kind Previous
    if (Test-Path -LiteralPath $previous) {
        throw "Previous runtime path already exists: $previous"
    }

    $hadPrevious = Test-Path -LiteralPath $active -PathType Container
    if ($hadPrevious) {
        [IO.Directory]::Move($active, $previous)
    }
    try {
        if ($null -ne $AfterPreviousMoved) {
            & $AfterPreviousMoved
        }
        [IO.Directory]::Move($stage, $active)
    }
    catch {
        $switchError = $_
        if ($hadPrevious -and
            -not (Test-Path -LiteralPath $active) -and
            (Test-Path -LiteralPath $previous -PathType Container)) {
            [IO.Directory]::Move($previous, $active)
        }
        throw $switchError
    }
    return $hadPrevious
}

function Restore-CoastalRuntime {
    param(
        [Parameter(Mandatory)] [string]$ProgramRoot,
        [Parameter(Mandatory)] [string]$RuntimePath,
        [Parameter(Mandatory)] [string]$PreviousPath,
        [Parameter(Mandatory)] [string]$FailedStagingPath,
        [Parameter(Mandatory)] [bool]$HadPrevious,
        [scriptblock]$BeforePreviousRestore
    )

    $active = Assert-CoastalRuntimePath -ProgramRoot $ProgramRoot `
        -Path $RuntimePath -Kind Active
    $previous = Assert-CoastalRuntimePath -ProgramRoot $ProgramRoot `
        -Path $PreviousPath -Kind Previous
    $failedStage = Assert-CoastalRuntimePath -ProgramRoot $ProgramRoot `
        -Path $FailedStagingPath -Kind Stage
    if (Test-Path -LiteralPath $failedStage) {
        throw "Rollback staging path is unexpectedly occupied: $failedStage"
    }

    $currentMoved = $false
    try {
        if (Test-Path -LiteralPath $active -PathType Container) {
            [IO.Directory]::Move($active, $failedStage)
            $currentMoved = $true
        }
        if ($HadPrevious) {
            if (-not (Test-Path -LiteralPath $previous -PathType Container)) {
                throw "Previous runtime is unavailable for rollback: $previous"
            }
            if ($null -ne $BeforePreviousRestore) {
                & $BeforePreviousRestore
            }
            [IO.Directory]::Move($previous, $active)
        }
    }
    catch {
        $restoreError = $_
        if (-not (Test-Path -LiteralPath $active)) {
            if ($HadPrevious -and
                (Test-Path -LiteralPath $previous -PathType Container)) {
                try {
                    [IO.Directory]::Move($previous, $active)
                }
                catch {
                    # If the old runtime cannot be restored, put the new runtime
                    # back so the fixed task path is never left empty.
                }
            }
            if (-not (Test-Path -LiteralPath $active) -and
                $currentMoved -and
                (Test-Path -LiteralPath $failedStage -PathType Container)) {
                [IO.Directory]::Move($failedStage, $active)
            }
        }
        throw $restoreError
    }
}

function Remove-CoastalTransientRuntime {
    param(
        [Parameter(Mandatory)] [string]$ProgramRoot,
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)]
        [ValidateSet('Stage', 'Previous')]
        [string]$Kind
    )

    $validated = Assert-CoastalRuntimePath -ProgramRoot $ProgramRoot `
        -Path $Path -Kind $Kind
    if (-not (Test-Path -LiteralPath $validated)) {
        return
    }
    Assert-CoastalNoReparseTree -Path $validated
    Remove-Item -LiteralPath $validated -Recurse -Force
}

Export-ModuleMember -Function @(
    'Assert-CoastalRuntimePath',
    'Switch-CoastalRuntime',
    'Restore-CoastalRuntime',
    'Remove-CoastalTransientRuntime'
)
