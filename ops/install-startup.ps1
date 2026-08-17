[CmdletBinding()]
param(
    [string]$AdminPasswordHash,
    [Security.SecureString]$AdminPassword,
    [switch]$RotateAdminSessionSecret,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$tunnelId = 'b8b930ea-02da-43e2-808b-8a10642c356e'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceServer = Join-Path $projectRoot 'server'
$sourceRiskModel = Join-Path $sourceServer 'models\coastal_risk_v1.json'
$sourceWheelhouse = Join-Path $sourceServer 'tmp\runtime-wheelhouse'
$sourceDeviceToken = Join-Path $sourceServer '.device_token'
$sourceFirmwareToken = Join-Path $projectRoot 'firmware\esp32\include\tunnel_secret.h'
$sourceWifiSecrets = Join-Path $projectRoot 'firmware\esp32\include\secrets.h'
$sourceTunnelCredential = Join-Path $env:USERPROFILE ".cloudflared\$tunnelId.json"
$sourceTunnelConfig = Join-Path $PSScriptRoot 'cloudflared-config.yml'
$sourceUvicornBootstrap = Join-Path $PSScriptRoot 'run-uvicorn.py'
$cloudflaredExe = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$systemPython = 'C:\Program Files\Python312\python.exe'

$programRoot = 'C:\ProgramData\CoastalWarning'
$runtimeRoot = Join-Path $programRoot 'runtime'
$runtimeServer = Join-Path $runtimeRoot 'server'
$runtimeOps = Join-Path $runtimeRoot 'ops'
$runtimeDeploymentIdFile = Join-Path $runtimeRoot 'deployment-id.txt'
$secretDir = Join-Path $programRoot 'secrets'
$logDir = Join-Path $programRoot 'logs'
$installErrorLog = Join-Path $logDir 'install-error.log'
$runDir = Join-Path $programRoot 'run'
$dataDir = Join-Path $programRoot 'data'
$modelDir = Join-Path $programRoot 'models'
$officialDatasetRoot = Join-Path $dataDir 'official_datasets'
$officialRegistryDir = Join-Path $dataDir 'official_registry'
$officialArtifactDir = Join-Path $modelDir 'official_runs'
$deviceToken = Join-Path $secretDir 'device-token.txt'
$adminPasswordHashFile = Join-Path $secretDir 'admin-password-hash.txt'
$adminSessionSecretFile = Join-Path $secretDir 'admin-session-secret.txt'
$mainRuntimeIdentityFile = Join-Path $runDir 'main-runtime.json'
$gatewayRuntimeIdentityFile = Join-Path $runDir 'gateway-runtime.json'
$databaseFile = Join-Path $dataDir 'coastal_warning.db'
$systemTunnelDir = 'C:\Windows\System32\config\systemprofile\.cloudflared'
$systemTunnelCredential = Join-Path $systemTunnelDir "$tunnelId.json"
$systemTunnelConfig = Join-Path $systemTunnelDir 'config.yml'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$currentUserSid = $identity.User.Value
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell window (Run as administrator).'
}

$legacyTranscriptPath = Join-Path $projectRoot 'tmp\startup-install.log'

function Assert-RegularFile {
    param([Parameter(Mandatory)] [string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file not found: $Path"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing reparse-point file: $Path"
    }
}

function Test-AdminPasswordHash {
    param([Parameter(Mandatory)] [string]$Value)

    return $Value -cmatch '^pbkdf2_sha256\$310000\$[0-9a-f]{32}\$[0-9a-f]{64}$'
}

function ConvertTo-LowerHex {
    param([Parameter(Mandatory)] [byte[]]$Bytes)

    return (($Bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function ConvertTo-Base64Url {
    param([Parameter(Mandatory)] [byte[]]$Bytes)

    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function New-CryptographicBytes {
    param([Parameter(Mandatory)] [ValidateRange(16, 4096)] [int]$Count)

    $bytes = [byte[]]::new($Count)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return ,$bytes
}

function New-AdminPasswordVerifier {
    param([Parameter(Mandatory)] [Security.SecureString]$Password)

    $bstr = [IntPtr]::Zero
    $plainText = $null
    $salt = $null
    $digest = $null
    $deriver = $null
    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
        $plainText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        if ([string]::IsNullOrEmpty($plainText)) {
            throw 'The administrator password cannot be empty'
        }
        $salt = New-CryptographicBytes -Count 16
        $deriver = [Security.Cryptography.Rfc2898DeriveBytes]::new(
            $plainText,
            $salt,
            310000,
            [Security.Cryptography.HashAlgorithmName]::SHA256
        )
        $digest = $deriver.GetBytes(32)
        return 'pbkdf2_sha256$310000${0}${1}' -f `
            (ConvertTo-LowerHex -Bytes $salt),
            (ConvertTo-LowerHex -Bytes $digest)
    }
    finally {
        if ($null -ne $deriver) { $deriver.Dispose() }
        if ($null -ne $salt) { [Array]::Clear($salt, 0, $salt.Length) }
        if ($null -ne $digest) { [Array]::Clear($digest, 0, $digest.Length) }
        $plainText = $null
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

function Read-ValidatedAdminPasswordHash {
    param([Parameter(Mandatory)] [string]$Path)

    Assert-RegularFile -Path $Path
    $value = [IO.File]::ReadAllText($Path).Trim()
    if (-not (Test-AdminPasswordHash -Value $value)) {
        throw "Invalid administrator password hash file: $Path"
    }
    return $value
}

function Write-ProtectedSecret {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Value
    )

    if (Test-Path -LiteralPath $Path) {
        Assert-RegularFile -Path $Path
    }
    [IO.File]::WriteAllText(
        $Path,
        "$Value`r`n",
        [Text.UTF8Encoding]::new($false)
    )
    Protect-Path -Path $Path -IsDirectory $false
}

function Ensure-SafeDirectory {
    param([Parameter(Mandatory)] [string]$Path)

    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing unsafe directory path: $Path"
        }
        return
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Assert-NoReparseTree {
    param([Parameter(Mandatory)] [string]$Path)

    $reparsePoint = Get-ChildItem -LiteralPath $Path -Force -Recurse |
        Where-Object {
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        } |
        Select-Object -First 1
    if ($null -ne $reparsePoint) {
        throw "Refusing directory tree containing a reparse point: $($reparsePoint.FullName)"
    }
}

function Set-TrustedTreeOwner {
    param([Parameter(Mandatory)] [string]$Path)

    & icacls.exe $Path /setowner '*S-1-5-32-544' /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to set trusted owner on $Path"
    }
}

function Protect-Path {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [bool]$IsDirectory,
        [switch]$IncludeCurrentUser,
        [switch]$Recurse
    )

    $systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $administratorsSid = [Security.Principal.SecurityIdentifier]::new(
        'S-1-5-32-544'
    )
    $trustedSids = @($systemSid, $administratorsSid)
    $ownerSid = $administratorsSid
    if ($IncludeCurrentUser) {
        $trustedSids += $identity.User
        $ownerSid = $identity.User
    }

    if ($IsDirectory) {
        $security = [Security.AccessControl.DirectorySecurity]::new()
        $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
        $security = [Security.AccessControl.FileSecurity]::new()
        $inheritance = [Security.AccessControl.InheritanceFlags]::None
    }
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($ownerSid)
    foreach ($sid in $trustedSids) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$security.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $security

    if ($Recurse) {
        $children = @(Get-ChildItem -LiteralPath $Path -Force)
        if ($children.Count -gt 0) {
            # Reset every child to inherit the exact protected parent DACL.
            # This removes stale or attacker-created explicit ACEs.
            $childPattern = Join-Path $Path '*'
            & icacls.exe $childPattern /reset /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to reset child ACLs under $Path"
            }
        }
    }
}

function Get-CoastalTaskSnapshots {
    param([Parameter(Mandatory)] [string[]]$TaskNames)

    $snapshots = @(
        foreach ($taskName in $TaskNames) {
            $task = Get-ScheduledTask -TaskName $taskName `
                -ErrorAction SilentlyContinue
            if ($null -eq $task) {
                [pscustomobject]@{
                    Name = $taskName
                    Exists = $false
                    WasRunning = $false
                    Xml = $null
                }
            }
            else {
                [pscustomobject]@{
                    Name = $taskName
                    Exists = $true
                    WasRunning = $task.State -eq 'Running'
                    Xml = Export-ScheduledTask -TaskName $taskName
                }
            }
        }
    )
    return $snapshots
}

function Stop-CoastalTasks {
    param([Parameter(Mandatory)] [string[]]$TaskNames)

    foreach ($taskName in $TaskNames) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $runningTasks = @(
            foreach ($taskName in $TaskNames) {
                $task = Get-ScheduledTask -TaskName $taskName `
                    -ErrorAction SilentlyContinue
                if ($null -ne $task -and $task.State -eq 'Running') { $task }
            }
        )
        if ($runningTasks.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Coastal Warning tasks did not stop in time'
}

function Wait-CoastalPortsReleased {
    param(
        [Parameter(Mandatory)] [int[]]$Ports,
        [ValidateRange(1, 120)] [int]$TimeoutSeconds = 15
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $listeners = @(
            foreach ($port in $Ports) {
                Get-NetTCPConnection -State Listen -LocalPort $port `
                    -ErrorAction SilentlyContinue
            }
        )
        if ($listeners.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    $occupied = @(
        $listeners |
            Sort-Object LocalPort, OwningProcess -Unique |
            ForEach-Object { "$($_.LocalPort):pid=$($_.OwningProcess)" }
    ) -join ', '
    throw "Coastal Warning ports remain occupied after task stop: $occupied"
}

function Test-CoastalExecutablePath {
    [OutputType([bool])]
    param(
        [AllowNull()] [string]$ObservedPath,
        [Parameter(Mandatory)] [string]$ExpectedPath
    )

    # ExecutablePath/Process.Path can be unavailable for a SYSTEM-owned child
    # even to an elevated caller. Port release before startup plus exact task
    # action validation remain mandatory; a readable path is an extra check.
    if ([string]::IsNullOrWhiteSpace($ObservedPath)) { return $false }
    try {
        $observedFull = [IO.Path]::GetFullPath($ObservedPath)
        $expectedFull = [IO.Path]::GetFullPath($ExpectedPath)
    }
    catch {
        return $false
    }
    return [string]::Equals(
        $observedFull,
        $expectedFull,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Test-CoastalRuntimeIdentityPayload {
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [object]$Identity,
        [Parameter(Mandatory)] [string]$DeploymentId,
        [Parameter(Mandatory)] [int]$Port,
        [Parameter(Mandatory)] [long]$ListenerPid
    )

    $identityPid = [long]0
    $identityPort = [int]0
    return (
        [string]$Identity.deployment_id -ceq $DeploymentId -and
        [long]::TryParse([string]$Identity.pid, [ref]$identityPid) -and
        [int]::TryParse([string]$Identity.port, [ref]$identityPort) -and
        $identityPort -eq $Port -and
        $identityPid -eq $ListenerPid
    )
}

function Assert-CoastalRuntimeProcesses {
    param(
        [Parameter(Mandatory)] [string]$RuntimeServerDirectory,
        [Parameter(Mandatory)] [string]$RuntimeOpsDirectory,
        [Parameter(Mandatory)] [string[]]$TaskNames,
        [Parameter(Mandatory)] [string]$DeploymentId,
        [Parameter(Mandatory)] [string]$DeploymentIdFile,
        [Parameter(Mandatory)] [hashtable]$PortIdentityFiles
    )

    Assert-RegularFile -Path $DeploymentIdFile
    $activeDeploymentId = [IO.File]::ReadAllText($DeploymentIdFile).Trim()
    if ($activeDeploymentId -cne $DeploymentId) {
        throw 'Active runtime deployment identifier does not match the candidate'
    }
    $expectedScripts = @{
        'CoastalWarning-Main' = Join-Path $RuntimeOpsDirectory 'run-main.ps1'
        'CoastalWarning-Gateway' = Join-Path $RuntimeOpsDirectory 'run-gateway.ps1'
    }
    $expectedWorkingDirectory = [IO.Path]::GetFullPath(
        $RuntimeServerDirectory
    )
    foreach ($taskName in $TaskNames) {
        $task = Get-ScheduledTask -TaskName $taskName `
            -ErrorAction SilentlyContinue
        if ($null -eq $task -or $task.State -ne 'Running') {
            throw "Scheduled task is not running after deployment: $taskName"
        }
        $actions = @($task.Actions)
        if ($actions.Count -ne 1) {
            throw "Scheduled task has an unexpected action count: $taskName"
        }
        $action = $actions[0]
        $workingDirectory = [IO.Path]::GetFullPath(
            [string]$action.WorkingDirectory
        )
        if (-not [string]::Equals(
                $workingDirectory,
                $expectedWorkingDirectory,
                [StringComparison]::OrdinalIgnoreCase
            )) {
            throw "Scheduled task does not use the active runtime: $taskName"
        }
        $expectedScript = [IO.Path]::GetFullPath($expectedScripts[$taskName])
        $actionArguments = [string]$action.Arguments
        if ($actionArguments.IndexOf(
                $expectedScript,
                [StringComparison]::OrdinalIgnoreCase
            ) -lt 0) {
            throw "Scheduled task does not launch its active runtime script: $taskName"
        }
    }

    $expectedPython = [IO.Path]::GetFullPath(
        (Join-Path $RuntimeServerDirectory '.venv\Scripts\python.exe')
    )
    foreach ($port in @(8000, 8001)) {
        $listeners = @(
            Get-NetTCPConnection -State Listen -LocalPort $port `
                -ErrorAction SilentlyContinue
        )
        $processIds = @(
            $listeners |
                ForEach-Object { $_.OwningProcess } |
                Sort-Object -Unique
        )
        if ($processIds.Count -ne 1) {
            throw "Expected one active runtime listener on port $port"
        }
        $identityFile = [string]$PortIdentityFiles[$port]
        Assert-RegularFile -Path $identityFile
        $identityText = [IO.File]::ReadAllText($identityFile)
        if ($identityText.Length -gt 4096) {
            throw "Runtime identity file is too large for port $port"
        }
        try {
            $identity = $identityText | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            throw "Runtime identity file is invalid for port $port"
        }
        $listenerPid = [long]$processIds[0]
        if (-not (Test-CoastalRuntimeIdentityPayload -Identity $identity `
                -DeploymentId $DeploymentId -Port $port `
                -ListenerPid $listenerPid)) {
            throw "Listener does not prove current runtime identity on port $port"
        }

        $listenerProcess = Get-Process -Id $listenerPid -ErrorAction Stop
        $observedPath = $null
        try {
            $observedPath = [string]$listenerProcess.Path
        }
        catch {
            # SYSTEM process metadata can be unreadable. This is not identity
            # evidence either way, so rely on the mandatory task/port checks.
        }
        if (-not [string]::IsNullOrWhiteSpace($observedPath) -and
            -not (Test-CoastalExecutablePath -ObservedPath $observedPath `
                -ExpectedPath $expectedPython)) {
            Write-Verbose (
                "Port $port runtime nonce is valid; Windows reports executable " +
                "'$observedPath' instead of '$expectedPython'."
            )
        }
    }
}

function Restore-CoastalTaskDefinitions {
    param([Parameter(Mandatory)] [object[]]$Snapshots)

    foreach ($snapshot in $Snapshots) {
        $current = Get-ScheduledTask -TaskName $snapshot.Name `
            -ErrorAction SilentlyContinue
        if ($snapshot.Exists) {
            Register-ScheduledTask -TaskName $snapshot.Name -Xml $snapshot.Xml `
                -Force | Out-Null
        }
        elseif ($null -ne $current) {
            Unregister-ScheduledTask -TaskName $snapshot.Name -Confirm:$false
        }
    }
}

function Start-PreviouslyRunningCoastalTasks {
    param([Parameter(Mandatory)] [object[]]$Snapshots)

    foreach ($snapshot in $Snapshots) {
        if ($snapshot.Exists -and $snapshot.WasRunning) {
            Start-ScheduledTask -TaskName $snapshot.Name
        }
    }
}

function Register-CoastalTask {
    param(
        [Parameter(Mandatory)] [string]$TaskName,
        [Parameter(Mandatory)] [string]$ScriptPath
    )

    $powershellExe = Join-Path $env:SystemRoot `
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $action = New-ScheduledTaskAction -Execute $powershellExe `
        -Argument $arguments -WorkingDirectory $runtimeServer
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
        -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 100 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $taskPrincipal -Settings $settings -Force | Out-Null
}

function Get-CoastalFileSnapshot {
    param([Parameter(Mandatory)] [string]$Path)

    if (Test-Path -LiteralPath $Path) {
        Assert-RegularFile -Path $Path
        $item = Get-Item -LiteralPath $Path -Force
        return [pscustomobject]@{
            Path = $Path
            Exists = $true
            Bytes = [IO.File]::ReadAllBytes($Path)
            Attributes = $item.Attributes
        }
    }
    return [pscustomobject]@{
        Path = $Path
        Exists = $false
        Bytes = $null
        Attributes = $null
    }
}

function Grant-CoastalRollbackFileAccess {
    param([Parameter(Mandatory)] [string]$Path)

    Assert-RegularFile -Path $Path
    $takeownExe = Join-Path $env:SystemRoot 'System32\takeown.exe'
    & $takeownExe /F $Path /A | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to take ownership for rollback: $Path"
    }
    Protect-Path -Path $Path -IsDirectory $false -IncludeCurrentUser
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReadOnly) -ne 0) {
        $writableAttributes = $item.Attributes -band `
            (-bnot [IO.FileAttributes]::ReadOnly)
        [IO.File]::SetAttributes($Path, $writableAttributes)
    }
}

function Remove-CoastalRuntimeIdentityFiles {
    param([Parameter(Mandatory)] [string[]]$Paths)

    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path) {
            Grant-CoastalRollbackFileAccess -Path $path
            Remove-Item -LiteralPath $path -Force
        }
    }
}

function Restore-CoastalFileSnapshot {
    param([Parameter(Mandatory)] [object]$Snapshot)

    $targetFull = [IO.Path]::GetFullPath([string]$Snapshot.Path)
    $parent = Split-Path -Parent $targetFull
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Rollback file parent is missing: $parent"
    }
    $parentItem = Get-Item -LiteralPath $parent -Force
    if (($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing reparse-point rollback parent: $parent"
    }

    if ($Snapshot.Exists) {
        $temporaryPath = Join-Path $parent (
            '.coastal-rollback-' + [Guid]::NewGuid().ToString('N') + '.tmp'
        )
        $backupPath = Join-Path $parent (
            '.coastal-rollback-backup-' +
            [Guid]::NewGuid().ToString('N') + '.tmp'
        )
        if ((Test-Path -LiteralPath $temporaryPath) -or
            (Test-Path -LiteralPath $backupPath)) {
            throw 'Generated rollback temporary path already exists'
        }
        try {
            [IO.File]::WriteAllBytes($temporaryPath, $Snapshot.Bytes)
            Protect-Path -Path $temporaryPath -IsDirectory $false `
                -IncludeCurrentUser
            if (Test-Path -LiteralPath $targetFull) {
                Grant-CoastalRollbackFileAccess -Path $targetFull
                [IO.File]::Replace(
                    $temporaryPath,
                    $targetFull,
                    $backupPath,
                    $true
                )
            }
            else {
                [IO.File]::Move($temporaryPath, $targetFull)
            }
            Protect-Path -Path $targetFull -IsDirectory $false
            [IO.File]::SetAttributes(
                $targetFull,
                [IO.FileAttributes]$Snapshot.Attributes
            )
        }
        finally {
            if (Test-Path -LiteralPath $temporaryPath) {
                Assert-RegularFile -Path $temporaryPath
                Remove-Item -LiteralPath $temporaryPath -Force
            }
            if (Test-Path -LiteralPath $backupPath) {
                Assert-RegularFile -Path $backupPath
                Grant-CoastalRollbackFileAccess -Path $backupPath
                Remove-Item -LiteralPath $backupPath -Force
            }
        }
    }
    elseif (Test-Path -LiteralPath $targetFull) {
        Grant-CoastalRollbackFileAccess -Path $targetFull
        Remove-Item -LiteralPath $targetFull -Force
    }
}

function Clear-CoastalFileSnapshot {
    param([Parameter(Mandatory)] [object]$Snapshot)

    if ($null -ne $Snapshot.Bytes) {
        [Array]::Clear($Snapshot.Bytes, 0, $Snapshot.Bytes.Length)
        $Snapshot.Bytes = $null
    }
}

function Get-CoastalInstallErrorLogPath {
    param(
        [Parameter(Mandatory)] [string]$ProgramRoot,
        [Parameter(Mandatory)] [string]$Path
    )

    $programFull = [IO.Path]::GetFullPath($ProgramRoot).TrimEnd('\')
    $pathFull = [IO.Path]::GetFullPath($Path)
    $expected = [IO.Path]::GetFullPath(
        (Join-Path (Join-Path $programFull 'logs') 'install-error.log')
    )
    if (-not [string]::Equals(
            $pathFull,
            $expected,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Refusing unexpected installer diagnostic path: $pathFull"
    }
    return $pathFull
}

function ConvertTo-CoastalSafeDiagnosticText {
    [OutputType([string])]
    param(
        [AllowNull()] [string]$Text,
        [AllowNull()] [string[]]$SensitiveValues,
        [ValidateRange(128, 16384)] [int]$MaxLength = 2048
    )

    if ($null -eq $Text) { return '' }
    $safe = $Text
    foreach ($sensitiveValue in @($SensitiveValues)) {
        if (-not [string]::IsNullOrEmpty($sensitiveValue)) {
            $safe = $safe.Replace($sensitiveValue, '[REDACTED]')
        }
    }
    $safe = [regex]::Replace(
        $safe,
        '(?i)pbkdf2_sha256\$310000\$[0-9a-f]{32}\$[0-9a-f]{64}',
        '[REDACTED_VERIFIER]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)COAST_[A-Z0-9_]+\s*[:=]\s*[^\s,;]+',
        '[REDACTED_ENV_ASSIGNMENT]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)"?(?:tunnelsecret|accounttag|token|password(?:_hash)?|' +
            'session_secret)"?\s*[:=]\s*"?[^",;\s}\]]+',
        '[REDACTED_CREDENTIAL_FIELD]'
    )
    # This deliberately also hides deployment nonces. A 32+ character opaque
    # value is not needed to locate an installer phase and might be a token.
    $safe = [regex]::Replace(
        $safe,
        '(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{32,}={0,2}' +
            '(?![A-Za-z0-9+/_=-])',
        '[REDACTED_OPAQUE_VALUE]'
    )
    $safe = $safe.Replace("`r", ' ').Replace("`n", ' ')
    $safe = [regex]::Replace(
        $safe,
        '[\x00-\x08\x0b\x0c\x0e-\x1f]',
        '?'
    )
    if ($safe.Length -gt $MaxLength) {
        return $safe.Substring(0, $MaxLength) + ' [TRUNCATED]'
    }
    return $safe
}

function Write-CoastalInstallFailureLog {
    param(
        [Parameter(Mandatory)] [string]$ProgramRoot,
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [Management.Automation.ErrorRecord]$ErrorRecord,
        [Parameter(Mandatory)]
        [ValidateSet(
            'preflight',
            'source_credentials',
            'admin_credentials',
            'prepare_directories',
            'build_staging',
            'snapshot_state',
            'commit_stop_services',
            'commit_switch_runtime',
            'commit_configuration',
            'commit_register_tasks',
            'commit_cloudflared',
            'start_services',
            'health_checks',
            'admin_route_check',
            'cloudflared_health',
            'runtime_identity_check',
            'cleanup_previous_runtime',
            'complete'
        )]
        [string]$Phase,
        [AllowEmptyCollection()] [string[]]$RollbackErrors = @(),
        [AllowNull()] [string[]]$SensitiveValues
    )

    $targetFull = Get-CoastalInstallErrorLogPath `
        -ProgramRoot $ProgramRoot -Path $Path
    $programFull = [IO.Path]::GetFullPath($ProgramRoot).TrimEnd('\')
    Ensure-SafeDirectory -Path $programFull
    Protect-Path -Path $programFull -IsDirectory $true
    $parent = Split-Path -Parent $targetFull
    Ensure-SafeDirectory -Path $parent
    Protect-Path -Path $parent -IsDirectory $true

    $safeRollbackErrors = @(
        foreach ($rollbackError in @($RollbackErrors)) {
            ConvertTo-CoastalSafeDiagnosticText -Text $rollbackError `
                -SensitiveValues $SensitiveValues -MaxLength 2048
        }
    )
    $payload = [ordered]@{
        schema_version = 1
        timestamp_utc = [DateTime]::UtcNow.ToString('o')
        phase = $Phase
        error_type = ConvertTo-CoastalSafeDiagnosticText `
            -Text $ErrorRecord.Exception.GetType().FullName `
            -SensitiveValues $SensitiveValues -MaxLength 512
        message = ConvertTo-CoastalSafeDiagnosticText `
            -Text $ErrorRecord.Exception.Message `
            -SensitiveValues $SensitiveValues -MaxLength 2048
        script_stack_trace = ConvertTo-CoastalSafeDiagnosticText `
            -Text ([string]$ErrorRecord.ScriptStackTrace) `
            -SensitiveValues $SensitiveValues -MaxLength 8192
        rollback_errors = @($safeRollbackErrors)
    }
    $json = $payload | ConvertTo-Json -Depth 3 -Compress
    $temporaryPath = Join-Path $parent (
        '.install-error-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    $backupPath = Join-Path $parent (
        '.install-error-backup-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    if ((Test-Path -LiteralPath $temporaryPath) -or
        (Test-Path -LiteralPath $backupPath)) {
        throw 'Generated installer diagnostic path already exists'
    }
    try {
        [IO.File]::WriteAllText(
            $temporaryPath,
            "$json`r`n",
            [Text.UTF8Encoding]::new($false)
        )
        Protect-Path -Path $temporaryPath -IsDirectory $false `
            -IncludeCurrentUser
        if (Test-Path -LiteralPath $targetFull) {
            Grant-CoastalRollbackFileAccess -Path $targetFull
            [IO.File]::Replace(
                $temporaryPath,
                $targetFull,
                $backupPath,
                $true
            )
        }
        else {
            [IO.File]::Move($temporaryPath, $targetFull)
        }
        Protect-Path -Path $targetFull -IsDirectory $false
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Assert-RegularFile -Path $temporaryPath
            Remove-Item -LiteralPath $temporaryPath -Force
        }
        if (Test-Path -LiteralPath $backupPath) {
            Assert-RegularFile -Path $backupPath
            Grant-CoastalRollbackFileAccess -Path $backupPath
            Remove-Item -LiteralPath $backupPath -Force
        }
    }
}

function Remove-CoastalInstallFailureLog {
    param(
        [Parameter(Mandatory)] [string]$ProgramRoot,
        [Parameter(Mandatory)] [string]$Path
    )

    $targetFull = Get-CoastalInstallErrorLogPath `
        -ProgramRoot $ProgramRoot -Path $Path
    if (Test-Path -LiteralPath $targetFull) {
        Grant-CoastalRollbackFileAccess -Path $targetFull
        Remove-Item -LiteralPath $targetFull -Force
    }
}

function Get-CloudflaredSnapshot {
    $service = Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        return [pscustomobject]@{
            Exists = $false
            WasRunning = $false
            ImagePath = $null
            StartValue = $null
            HasDelayedAutoStart = $false
            DelayedAutoStart = $null
        }
    }
    $serviceKey = Get-ItemProperty -LiteralPath `
        'HKLM:\SYSTEM\CurrentControlSet\Services\cloudflared'
    $hasDelayed = $serviceKey.PSObject.Properties.Name -contains `
        'DelayedAutoStart'
    return [pscustomobject]@{
        Exists = $true
        WasRunning = $service.Status -eq 'Running'
        ImagePath = [string]$serviceKey.ImagePath
        StartValue = [int]$serviceKey.Start
        HasDelayedAutoStart = $hasDelayed
        DelayedAutoStart = if ($hasDelayed) {
            [int]$serviceKey.DelayedAutoStart
        } else { $null }
    }
}

function Restore-CloudflaredSnapshot {
    param(
        [Parameter(Mandatory)] [object]$Snapshot,
        [Parameter(Mandatory)] [string]$CloudflaredExe
    )

    $service = Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue
    if (-not $Snapshot.Exists) {
        if ($null -ne $service) {
            if ($service.Status -ne 'Stopped') {
                Stop-Service -Name 'cloudflared' -Force
                $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
            }
            & $CloudflaredExe service uninstall | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw 'Failed to remove cloudflared service during rollback'
            }
        }
        return
    }
    if ($null -eq $service) {
        throw 'Original cloudflared service is missing during rollback'
    }
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name 'cloudflared' -Force
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
    }
    $serviceRegistry = 'HKLM:\SYSTEM\CurrentControlSet\Services\cloudflared'
    Set-ItemProperty -LiteralPath $serviceRegistry -Name ImagePath `
        -Value $Snapshot.ImagePath
    Set-ItemProperty -LiteralPath $serviceRegistry -Name Start `
        -Value $Snapshot.StartValue
    if ($Snapshot.HasDelayedAutoStart) {
        Set-ItemProperty -LiteralPath $serviceRegistry -Name DelayedAutoStart `
            -Value $Snapshot.DelayedAutoStart
    }
    else {
        Remove-ItemProperty -LiteralPath $serviceRegistry `
            -Name DelayedAutoStart -ErrorAction SilentlyContinue
    }
    if ($Snapshot.WasRunning) {
        Start-Service -Name 'cloudflared'
    }
}

function Test-StagedRuntime {
    param(
        [Parameter(Mandatory)] [string]$ServerDirectory,
        [Parameter(Mandatory)] [string]$RuntimeOpsDirectory,
        [Parameter(Mandatory)] [string]$DeploymentIdFile,
        [Parameter(Mandatory)] [string]$RiskModelSha256,
        [Parameter(Mandatory)] [string]$PythonExe,
        [Parameter(Mandatory)] [string]$DeviceTokenValue,
        [Parameter(Mandatory)] [string]$AdminPasswordVerifier,
        [Parameter(Mandatory)] [string]$AdminSessionSecret
    )

    $stagedRiskModel = Join-Path $ServerDirectory `
        'models\coastal_risk_v1.json'
    foreach ($required in @(
            $PythonExe,
            (Join-Path $ServerDirectory 'app\gateway.py'),
            (Join-Path $ServerDirectory 'app\main.py'),
            (Join-Path $ServerDirectory 'requirements.txt'),
            $stagedRiskModel,
            (Join-Path $RuntimeOpsDirectory 'run-uvicorn.py'),
            $DeploymentIdFile
        )) {
        Assert-RegularFile -Path $required
    }
    $stagedDeploymentId = [IO.File]::ReadAllText($DeploymentIdFile).Trim()
    if ($stagedDeploymentId -cnotmatch '^[0-9a-f]{32}$') {
        throw 'Staged runtime deployment identifier is invalid'
    }
    if ($RiskModelSha256 -cnotmatch '^[0-9A-F]{64}$') {
        throw 'Expected risk model SHA-256 is invalid'
    }
    $stagedRiskModelHash = (
        Get-FileHash -LiteralPath $stagedRiskModel -Algorithm SHA256
    ).Hash
    if ($stagedRiskModelHash -cne $RiskModelSha256) {
        throw 'Staged risk model artifact hash does not match the source'
    }
    & $PythonExe -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Staged runtime dependency check failed' }
    & $PythonExe -m compileall -q (Join-Path $ServerDirectory 'app')
    if ($LASTEXITCODE -ne 0) { throw 'Staged runtime compilation check failed' }
    & $PythonExe -m py_compile (Join-Path $RuntimeOpsDirectory 'run-uvicorn.py')
    if ($LASTEXITCODE -ne 0) { throw 'Staged runtime bootstrap check failed' }

    $environmentNames = @(
        'COAST_DEVICE_TOKEN',
        'COAST_ADMIN_PASSWORD_HASH',
        'COAST_ADMIN_PASSWORD_HASH_FILE',
        'COAST_ADMIN_SESSION_SECRET',
        'COAST_ADMIN_SESSION_SECRET_FILE',
        'COAST_RISK_MODEL_PATH',
        'COASTAL_DB_PATH',
        'COAST_CUSTOM_MODEL_PATH',
        'COAST_OFFICIAL_DATASET_ROOT',
        'COAST_OFFICIAL_REGISTRY_DIR',
        'COAST_OFFICIAL_ARTIFACT_DIR'
    )
    $originalEnvironment = @{}
    foreach ($name in $environmentNames) {
        $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable(
            $name,
            'Process'
        )
    }
    try {
        $env:COAST_DEVICE_TOKEN = $DeviceTokenValue
        $env:COAST_ADMIN_PASSWORD_HASH = $AdminPasswordVerifier
        Remove-Item Env:COAST_ADMIN_PASSWORD_HASH_FILE -ErrorAction SilentlyContinue
        $env:COAST_ADMIN_SESSION_SECRET = $AdminSessionSecret
        Remove-Item Env:COAST_ADMIN_SESSION_SECRET_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:COAST_RISK_MODEL_PATH -ErrorAction SilentlyContinue
        $env:COASTAL_DB_PATH = $databaseFile
        $env:COAST_CUSTOM_MODEL_PATH = Join-Path $modelDir 'custom_water_v1.json'
        $env:COAST_OFFICIAL_DATASET_ROOT = $officialDatasetRoot
        $env:COAST_OFFICIAL_REGISTRY_DIR = $officialRegistryDir
        $env:COAST_OFFICIAL_ARTIFACT_DIR = $officialArtifactDir
        Push-Location -LiteralPath $ServerDirectory
        try {
            $smokeTest = @'
from app.auth import load_admin_auth_config, verify_admin_credentials
from app.gateway import app as gateway_app
from app.main import app as main_app
from app.risk_model import load_risk_model

config = load_admin_auth_config()
model = load_risk_model()
if config.password_iterations < 100_000:
    raise RuntimeError('administrator password verifier is too weak')
if gateway_app is None or main_app is None:
    raise RuntimeError('staged ASGI application import failed')
if model is None or model.model_version == 'rule-fallback-v1':
    raise RuntimeError('staged risk model is not ready')
if verify_admin_credentials(config, 'not-admin', 'not-the-password'):
    raise RuntimeError('invalid staged administrator credential check')
'@
            & $PythonExe -c $smokeTest
            if ($LASTEXITCODE -ne 0) {
                throw 'Staged runtime import and configuration check failed'
            }
        }
        finally {
            Pop-Location
        }
    }
    finally {
        foreach ($name in $environmentNames) {
            $originalValue = $originalEnvironment[$name]
            if ($null -eq $originalValue) {
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
            else {
                [Environment]::SetEnvironmentVariable(
                    $name,
                    $originalValue,
                    'Process'
                )
            }
        }
    }
}

$taskNames = @('CoastalWarning-Main', 'CoastalWarning-Gateway')
$deploymentId = [Guid]::NewGuid().ToString('N')
$stagingRoot = Join-Path $programRoot "runtime.stage.$deploymentId"
$stagingServer = Join-Path $stagingRoot 'server'
$stagingModels = Join-Path $stagingServer 'models'
$stagingRiskModel = Join-Path $stagingModels 'coastal_risk_v1.json'
$stagingOps = Join-Path $stagingRoot 'ops'
$stagingDeploymentIdFile = Join-Path $stagingRoot 'deployment-id.txt'
$previousRoot = Join-Path $programRoot "runtime.previous.$deploymentId"
$runtimeDeploymentModule = Join-Path $PSScriptRoot 'runtime-deployment.psm1'

$deploymentModuleLoaded = $false
$commitStarted = $false
$runtimeSwitched = $false
$hadPreviousRuntime = $false
$configurationMutated = $false
$taskSnapshots = @()
$fileSnapshots = @()
$cloudflaredSnapshot = $null
$resolvedAdminPasswordHash = $null
$resolvedSessionSecret = $null
$tokenText = $null
$firmwareTokenText = $null
$firmwareTokenMatch = $null
$headers = $null
$sourceRiskModelHash = $null
$installPhase = 'preflight'

try {
    # Older installer versions used Start-Transcript, whose header recorded the
    # complete invocation including a supplied password verifier. Never start a
    # transcript for credential provisioning, and remove only that exact legacy
    # file after rejecting directories and reparse points.
    if (Test-Path -LiteralPath $legacyTranscriptPath) {
        Assert-RegularFile -Path $legacyTranscriptPath
        Remove-Item -LiteralPath $legacyTranscriptPath -Force
    }
    foreach ($requiredFile in @(
            $sourceDeviceToken,
            $sourceFirmwareToken,
            $sourceWifiSecrets,
            $sourceTunnelCredential,
            $sourceTunnelConfig,
            $sourceRiskModel,
            $sourceUvicornBootstrap,
            $cloudflaredExe,
            $systemPython,
            $runtimeDeploymentModule,
            (Join-Path $sourceServer 'requirements-runtime.txt'),
            (Join-Path $PSScriptRoot 'run-main.ps1'),
            (Join-Path $PSScriptRoot 'run-gateway.ps1')
        )) {
        Assert-RegularFile -Path $requiredFile
    }
    $sourceRiskModelDirectory = Split-Path -Parent $sourceRiskModel
    $sourceRiskModelDirectoryItem = Get-Item -LiteralPath $sourceRiskModelDirectory `
        -Force
    if (-not $sourceRiskModelDirectoryItem.PSIsContainer -or
        ($sourceRiskModelDirectoryItem.Attributes -band
            [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing unsafe source risk model directory: $sourceRiskModelDirectory"
    }
    $sourceRiskModelHash = (
        Get-FileHash -LiteralPath $sourceRiskModel -Algorithm SHA256
    ).Hash

    $useLocalWheelhouse = Test-Path -LiteralPath $sourceWheelhouse -PathType Container
    if ($useLocalWheelhouse) {
        $wheelhouseItem = Get-Item -LiteralPath $sourceWheelhouse -Force
        if (($wheelhouseItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing reparse-point wheelhouse: $sourceWheelhouse"
        }
        Assert-NoReparseTree -Path $sourceWheelhouse
        $wheelFiles = @(Get-ChildItem -LiteralPath $sourceWheelhouse -Filter '*.whl' -File)
        if ($wheelFiles.Count -eq 0) {
            throw "Local wheelhouse contains no wheels: $sourceWheelhouse"
        }
        foreach ($wheelFile in $wheelFiles) {
            Assert-RegularFile -Path $wheelFile.FullName
        }
    }

    $installPhase = 'source_credentials'
    # Close the original token exposure before reading or copying it. Files made
    # through the sandbox can have a sandbox owner, so an elevated install first
    # takes ownership and then replaces the DACL with trusted principals.
    foreach ($ownedFile in @(
            $sourceDeviceToken,
            $sourceFirmwareToken,
            $sourceWifiSecrets,
            $sourceTunnelCredential
        )) {
        $acl = Get-Acl -LiteralPath $ownedFile
        $acl.SetOwner($identity.User)
        Set-Acl -LiteralPath $ownedFile -AclObject $acl
    }
    Protect-Path -Path $sourceDeviceToken -IsDirectory $false -IncludeCurrentUser
    Protect-Path -Path $sourceFirmwareToken -IsDirectory $false -IncludeCurrentUser
    Protect-Path -Path $sourceWifiSecrets -IsDirectory $false -IncludeCurrentUser
    Protect-Path -Path $sourceTunnelCredential -IsDirectory $false -IncludeCurrentUser
    $tokenText = [IO.File]::ReadAllText($sourceDeviceToken).Trim()
    $firmwareTokenText = [IO.File]::ReadAllText($sourceFirmwareToken)
    $firmwareTokenMatch = [regex]::Match(
        $firmwareTokenText,
        '(?m)^#define DEVICE_TOKEN "([A-Za-z0-9_-]{32,})"\r?$'
    )
    if ($tokenText.Length -lt 32 -or -not $firmwareTokenMatch.Success -or
        $firmwareTokenMatch.Groups[1].Value -cne $tokenText) {
        throw 'Server and firmware device tokens do not match'
    }
    $firmwareTokenText = $null
    $firmwareTokenMatch = $null

    $installPhase = 'admin_credentials'
    foreach ($existingDirectory in @($programRoot, $secretDir)) {
        if (Test-Path -LiteralPath $existingDirectory) {
            Ensure-SafeDirectory -Path $existingDirectory
        }
    }
    if ($null -ne $AdminPassword -and
        -not [string]::IsNullOrWhiteSpace($AdminPasswordHash)) {
        throw 'Use either -AdminPassword or -AdminPasswordHash, not both'
    }
    if (-not [string]::IsNullOrWhiteSpace($AdminPasswordHash)) {
        $resolvedAdminPasswordHash = $AdminPasswordHash.Trim()
        if (-not (Test-AdminPasswordHash -Value $resolvedAdminPasswordHash)) {
            throw 'Invalid -AdminPasswordHash format'
        }
    }
    elseif ($null -ne $AdminPassword) {
        $resolvedAdminPasswordHash = New-AdminPasswordVerifier -Password $AdminPassword
    }
    elseif (Test-Path -LiteralPath $adminPasswordHashFile) {
        $resolvedAdminPasswordHash = Read-ValidatedAdminPasswordHash -Path $adminPasswordHashFile
    }
    else {
        throw ('Administrator credentials are not configured. Supply ' +
            '-AdminPassword with a SecureString or -AdminPasswordHash with a ' +
            'precomputed PBKDF2 verifier.')
    }
    $AdminPasswordHash = $null
    $AdminPassword = $null

    if (-not $RotateAdminSessionSecret -and
        (Test-Path -LiteralPath $adminSessionSecretFile)) {
        Assert-RegularFile -Path $adminSessionSecretFile
        $resolvedSessionSecret = [IO.File]::ReadAllText(
            $adminSessionSecretFile
        ).Trim()
        if ($resolvedSessionSecret -cnotmatch '^[A-Za-z0-9_-]{43}$') {
            throw "Invalid administrator session secret file: $adminSessionSecretFile"
        }
    }
    else {
        $sessionBytes = New-CryptographicBytes -Count 32
        try {
            $resolvedSessionSecret = ConvertTo-Base64Url -Bytes $sessionBytes
        }
        finally {
            [Array]::Clear($sessionBytes, 0, $sessionBytes.Length)
        }
    }

    $installPhase = 'prepare_directories'
    # Lock the deployment roots while the currently installed services continue
    # running. The candidate runtime is built as a protected sibling directory.
    Ensure-SafeDirectory -Path $programRoot
    Assert-NoReparseTree -Path $programRoot
    Set-TrustedTreeOwner -Path $programRoot
    Protect-Path -Path $programRoot -IsDirectory $true -Recurse
    foreach ($directory in @(
            $secretDir,
            $logDir,
            $runDir,
            $dataDir,
            $modelDir,
            $officialDatasetRoot,
            $officialRegistryDir,
            $officialArtifactDir
        )) {
        Ensure-SafeDirectory -Path $directory
    }
    Remove-CoastalInstallFailureLog -ProgramRoot $programRoot `
        -Path $installErrorLog
    Ensure-SafeDirectory -Path $systemTunnelDir
    Assert-NoReparseTree -Path $systemTunnelDir
    Set-TrustedTreeOwner -Path $systemTunnelDir
    Protect-Path -Path $systemTunnelDir -IsDirectory $true -Recurse

    $installPhase = 'build_staging'
    Import-Module -Name $runtimeDeploymentModule -Force
    $deploymentModuleLoaded = $true
    if ((Test-Path -LiteralPath $stagingRoot) -or
        (Test-Path -LiteralPath $previousRoot)) {
        throw 'Generated deployment sibling already exists'
    }
    Ensure-SafeDirectory -Path $stagingRoot
    Ensure-SafeDirectory -Path $stagingServer
    Ensure-SafeDirectory -Path $stagingModels
    Ensure-SafeDirectory -Path $stagingOps
    [void](Assert-CoastalRuntimePath -ProgramRoot $programRoot -Path $stagingRoot -Kind Stage -MustExist)
    [void](Assert-CoastalRuntimePath -ProgramRoot $programRoot -Path $previousRoot -Kind Previous)

    Copy-Item -LiteralPath (Join-Path $sourceServer 'app') -Destination $stagingServer -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $sourceServer 'requirements-runtime.txt') -Destination (Join-Path $stagingServer 'requirements.txt') -Force
    Copy-Item -LiteralPath $sourceRiskModel -Destination $stagingRiskModel `
        -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-main.ps1') -Destination $stagingOps -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-gateway.ps1') -Destination $stagingOps -Force
    Copy-Item -LiteralPath $sourceUvicornBootstrap -Destination $stagingOps -Force
    [IO.File]::WriteAllText(
        $stagingDeploymentIdFile,
        "$deploymentId`r`n",
        [Text.ASCIIEncoding]::new()
    )

    $stagingPython = Join-Path $stagingServer '.venv\Scripts\python.exe'
    & $systemPython -m venv (Join-Path $stagingServer '.venv')
    if ($LASTEXITCODE -ne 0 -or
        -not (Test-Path -LiteralPath $stagingPython -PathType Leaf)) {
        throw 'Failed to create the staged Python virtual environment'
    }
    if ($useLocalWheelhouse) {
        & $stagingPython -m pip install --disable-pip-version-check --no-input --no-index --find-links $sourceWheelhouse -r (Join-Path $stagingServer 'requirements.txt')
    }
    else {
        & $stagingPython -m pip install --disable-pip-version-check --no-input -r (Join-Path $stagingServer 'requirements.txt')
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to install staged server runtime dependencies'
    }
    Test-StagedRuntime -ServerDirectory $stagingServer `
        -RuntimeOpsDirectory $stagingOps `
        -DeploymentIdFile $stagingDeploymentIdFile `
        -RiskModelSha256 $sourceRiskModelHash `
        -PythonExe $stagingPython -DeviceTokenValue $tokenText `
        -AdminPasswordVerifier $resolvedAdminPasswordHash `
        -AdminSessionSecret $resolvedSessionSecret
    Assert-NoReparseTree -Path $stagingRoot
    Protect-Path -Path $stagingRoot -IsDirectory $true -Recurse
    [void](Assert-CoastalRuntimePath -ProgramRoot $programRoot -Path $stagingRoot -Kind Stage -MustExist)

    if (-not (Test-Path -LiteralPath $databaseFile -PathType Leaf)) {
        $sourceDatabase = Join-Path $sourceServer 'data\coastal_warning.db'
        if (Test-Path -LiteralPath $sourceDatabase -PathType Leaf) {
            Assert-RegularFile -Path $sourceDatabase
            Copy-Item -LiteralPath $sourceDatabase -Destination $databaseFile -Force
        }
    }

    $installPhase = 'snapshot_state'
    $managedFilePaths = @(
        $deviceToken,
        $adminPasswordHashFile,
        $adminSessionSecretFile,
        $systemTunnelCredential,
        $systemTunnelConfig
    )
    $fileSnapshots = @(
        foreach ($managedPath in $managedFilePaths) {
            Get-CoastalFileSnapshot -Path $managedPath
        }
    )
    $taskSnapshots = @(Get-CoastalTaskSnapshots -TaskNames $taskNames)
    $cloudflaredSnapshot = Get-CloudflaredSnapshot

    # Commit window: only rename operations happen to the runtime tree. The
    # exact previous directory remains untouched until post-start health passes.
    $installPhase = 'commit_stop_services'
    $commitStarted = $true
    Stop-CoastalTasks -TaskNames $taskNames
    Wait-CoastalPortsReleased -Ports @(8000, 8001)
    Remove-CoastalRuntimeIdentityFiles -Paths @(
        $mainRuntimeIdentityFile,
        $gatewayRuntimeIdentityFile
    )
    $installPhase = 'commit_switch_runtime'
    $hadPreviousRuntime = Switch-CoastalRuntime -ProgramRoot $programRoot -StagingPath $stagingRoot -RuntimePath $runtimeRoot -PreviousPath $previousRoot
    $runtimeSwitched = $true

    $installPhase = 'commit_configuration'
    $configurationMutated = $true
    Write-ProtectedSecret -Path $adminPasswordHashFile -Value $resolvedAdminPasswordHash
    Write-ProtectedSecret -Path $adminSessionSecretFile -Value $resolvedSessionSecret
    Copy-Item -LiteralPath $sourceDeviceToken -Destination $deviceToken -Force
    Copy-Item -LiteralPath $sourceTunnelCredential -Destination $systemTunnelCredential -Force
    Copy-Item -LiteralPath $sourceTunnelConfig -Destination $systemTunnelConfig -Force
    Protect-Path -Path $deviceToken -IsDirectory $false
    Protect-Path -Path $systemTunnelCredential -IsDirectory $false
    Protect-Path -Path $systemTunnelConfig -IsDirectory $false
    $resolvedAdminPasswordHash = $null
    $resolvedSessionSecret = $null
    $tokenText = $null

    $installPhase = 'commit_register_tasks'
    Register-CoastalTask -TaskName 'CoastalWarning-Main' -ScriptPath (Join-Path $runtimeOps 'run-main.ps1')
    Register-CoastalTask -TaskName 'CoastalWarning-Gateway' -ScriptPath (Join-Path $runtimeOps 'run-gateway.ps1')

    $installPhase = 'commit_cloudflared'
    if ($null -eq (Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue)) {
        & $cloudflaredExe service install
        if ($LASTEXITCODE -ne 0) {
            throw 'cloudflared service installation failed'
        }
    }
    $service = Get-Service -Name 'cloudflared'
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name 'cloudflared' -Force
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
    }
    $imagePath = "`"$cloudflaredExe`" --config `"$systemTunnelConfig`" tunnel run"
    Set-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\cloudflared' -Name ImagePath -Value $imagePath
    Set-Service -Name 'cloudflared' -StartupType Automatic
    & sc.exe failure cloudflared reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to configure cloudflared service recovery'
    }
    & sc.exe failureflag cloudflared 1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to enable cloudflared failure actions'
    }

    if (-not $NoStart) {
        $installPhase = 'start_services'
        Start-ScheduledTask -TaskName 'CoastalWarning-Main'
        Start-ScheduledTask -TaskName 'CoastalWarning-Gateway'
        Start-Service -Name 'cloudflared'

        $installPhase = 'health_checks'
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        $health = $null
        $mainHealth = $null
        $headers = @{
            'X-Device-Token' = [IO.File]::ReadAllText($deviceToken).Trim()
        }
        do {
            Start-Sleep -Milliseconds 500
            try {
                $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/v1/health' -Headers $headers -TimeoutSec 3
            }
            catch {
                $health = $null
            }
            try {
                $mainHealth = Invoke-RestMethod `
                    -Uri 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 3
            }
            catch {
                $mainHealth = $null
            }
        } while (($null -eq $health -or $null -eq $mainHealth) -and
            [DateTime]::UtcNow -lt $deadline)
        $headers['X-Device-Token'] = $null
        $headers = $null
        if ($null -eq $health -or $health.status -ne 'ok') {
            throw 'Gateway startup health check failed'
        }
        if ($null -eq $mainHealth -or $mainHealth.status -ne 'ok') {
            throw 'Main service startup health check failed'
        }
        $installPhase = 'admin_route_check'
        try {
            $adminLogin = Invoke-WebRequest -Uri 'http://127.0.0.1:8001/admin/login' -UseBasicParsing -TimeoutSec 5
        }
        catch {
            throw 'Gateway administrator login page check failed'
        }
        if ($adminLogin.StatusCode -ne 200) {
            throw "Gateway administrator login returned HTTP $($adminLogin.StatusCode)"
        }
        $installPhase = 'cloudflared_health'
        if ((Get-Service -Name 'cloudflared').Status -ne 'Running') {
            throw 'cloudflared service did not reach Running state'
        }
        $installPhase = 'runtime_identity_check'
        Assert-CoastalRuntimeProcesses -RuntimeServerDirectory $runtimeServer `
            -RuntimeOpsDirectory $runtimeOps -TaskNames $taskNames `
            -DeploymentId $deploymentId `
            -DeploymentIdFile $runtimeDeploymentIdFile `
            -PortIdentityFiles @{
                8000 = $mainRuntimeIdentityFile
                8001 = $gatewayRuntimeIdentityFile
            }
    }

    $installPhase = 'cleanup_previous_runtime'
    if ($hadPreviousRuntime -and
        (Test-Path -LiteralPath $previousRoot -PathType Container)) {
        if ($NoStart) {
            Write-Warning (
                'Previous runtime retained because -NoStart skipped live health checks: ' +
                $previousRoot
            )
        }
        else {
            try {
                Remove-CoastalTransientRuntime -ProgramRoot $programRoot `
                    -Path $previousRoot -Kind Previous
            }
            catch {
                Write-Warning "New runtime is healthy, but previous cleanup was retained: $($_.Exception.Message)"
            }
        }
    }

    Remove-CoastalInstallFailureLog -ProgramRoot $programRoot `
        -Path $installErrorLog
    $installPhase = 'complete'
    Write-Host 'Coastal Warning startup services installed successfully.'
    Write-Host 'Run ops\status.ps1 as administrator to verify all components.'
}
catch {
    $installError = $_
    $failedPhase = $installPhase
    $diagnosticSensitiveValues = [Collections.Generic.List[string]]::new()
    foreach ($sensitiveValue in @(
            [string]$AdminPasswordHash,
            [string]$resolvedAdminPasswordHash,
            [string]$resolvedSessionSecret,
            [string]$tokenText
        )) {
        if (-not [string]::IsNullOrEmpty($sensitiveValue)) {
            $diagnosticSensitiveValues.Add($sensitiveValue)
        }
    }
    if ($null -ne $headers -and
        $headers.ContainsKey('X-Device-Token')) {
        $headerToken = [string]$headers['X-Device-Token']
        if (-not [string]::IsNullOrEmpty($headerToken)) {
            $diagnosticSensitiveValues.Add($headerToken)
        }
        $headerToken = $null
        $headers['X-Device-Token'] = $null
        $headers = $null
    }
    $rollbackErrors = [Collections.Generic.List[string]]::new()
    $runtimeRollbackSucceeded = $false

    if ($commitStarted) {
        try {
            Stop-CoastalTasks -TaskNames $taskNames
            Wait-CoastalPortsReleased -Ports @(8000, 8001)
            Remove-CoastalRuntimeIdentityFiles -Paths @(
                $mainRuntimeIdentityFile,
                $gatewayRuntimeIdentityFile
            )
        }
        catch {
            $rollbackErrors.Add("task stop: $($_.Exception.Message)")
        }

        if ($runtimeSwitched) {
            try {
                Restore-CoastalRuntime -ProgramRoot $programRoot -RuntimePath $runtimeRoot -PreviousPath $previousRoot -FailedStagingPath $stagingRoot -HadPrevious $hadPreviousRuntime
                $runtimeRollbackSucceeded = $true
                $runtimeSwitched = $false
            }
            catch {
                $rollbackErrors.Add("runtime restore: $($_.Exception.Message)")
            }
        }

        # Stop the tunnel before replacing its credential/config snapshots.
        # cloudflared can keep the credential file open on Windows, which makes
        # even an elevated in-place write fail with AccessDenied.
        if ($null -ne $cloudflaredSnapshot) {
            try {
                $rollbackCloudService = Get-Service -Name 'cloudflared' `
                    -ErrorAction SilentlyContinue
                if ($null -ne $rollbackCloudService -and
                    $rollbackCloudService.Status -ne 'Stopped') {
                    Stop-Service -Name 'cloudflared' -Force
                    $rollbackCloudService.WaitForStatus(
                        'Stopped',
                        [TimeSpan]::FromSeconds(20)
                    )
                }
            }
            catch {
                $rollbackErrors.Add("cloudflared stop: $($_.Exception.Message)")
            }
        }

        if ($configurationMutated -and $fileSnapshots.Count -gt 0) {
            foreach ($snapshot in $fileSnapshots) {
                try {
                    Restore-CoastalFileSnapshot -Snapshot $snapshot
                }
                catch {
                    $rollbackErrors.Add("file restore $($snapshot.Path): $($_.Exception.Message)")
                }
            }
        }

        if ($taskSnapshots.Count -gt 0) {
            try {
                Restore-CoastalTaskDefinitions -Snapshots $taskSnapshots
            }
            catch {
                $rollbackErrors.Add("task definition restore: $($_.Exception.Message)")
            }
        }

        if ($null -ne $cloudflaredSnapshot) {
            try {
                Restore-CloudflaredSnapshot -Snapshot $cloudflaredSnapshot -CloudflaredExe $cloudflaredExe
            }
            catch {
                $rollbackErrors.Add("cloudflared restore: $($_.Exception.Message)")
            }
        }

        if ($taskSnapshots.Count -gt 0) {
            try {
                Start-PreviouslyRunningCoastalTasks -Snapshots $taskSnapshots
            }
            catch {
                $rollbackErrors.Add("task restart: $($_.Exception.Message)")
            }
        }
    }

    $safeToCleanStage = -not $commitStarted -or
        -not $runtimeSwitched -or $runtimeRollbackSucceeded
    if ($deploymentModuleLoaded -and $safeToCleanStage -and
        (Test-Path -LiteralPath $stagingRoot -PathType Container)) {
        try {
            Remove-CoastalTransientRuntime -ProgramRoot $programRoot -Path $stagingRoot -Kind Stage
        }
        catch {
            $rollbackErrors.Add("staging cleanup: $($_.Exception.Message)")
        }
    }

    try {
        Write-CoastalInstallFailureLog -ProgramRoot $programRoot `
            -Path $installErrorLog -ErrorRecord $installError `
            -Phase $failedPhase -RollbackErrors @($rollbackErrors) `
            -SensitiveValues $diagnosticSensitiveValues
    }
    catch {
        # Diagnostic persistence is best effort and must never replace the
        # original installation or rollback failure returned to the caller.
        $diagnosticFailure = ConvertTo-CoastalSafeDiagnosticText `
            -Text $_.Exception.Message `
            -SensitiveValues $diagnosticSensitiveValues -MaxLength 512
        Write-Warning "Could not write protected install-error.log: $diagnosticFailure"
    }
    $diagnosticSensitiveValues = $null

    if ($rollbackErrors.Count -gt 0) {
        $rollbackSummary = $rollbackErrors -join '; '
        throw [InvalidOperationException]::new(
            "Installation failed: $($installError.Exception.Message). Rollback errors: $rollbackSummary",
            $installError.Exception
        )
    }
    throw $installError
}
finally {
    $resolvedAdminPasswordHash = $null
    $resolvedSessionSecret = $null
    $tokenText = $null
    $firmwareTokenText = $null
    $firmwareTokenMatch = $null
    $headers = $null
    $sourceRiskModelHash = $null
    foreach ($snapshot in $fileSnapshots) {
        Clear-CoastalFileSnapshot -Snapshot $snapshot
    }
    if ($deploymentModuleLoaded) {
        Remove-Module runtime-deployment -ErrorAction SilentlyContinue
    }
}
