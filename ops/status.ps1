[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$tokenFile = 'C:\ProgramData\CoastalWarning\secrets\device-token.txt'
$adminPasswordHashFile = 'C:\ProgramData\CoastalWarning\secrets\admin-password-hash.txt'
$adminSessionSecretFile = 'C:\ProgramData\CoastalWarning\secrets\admin-session-secret.txt'
$runtimeDeploymentIdFile = 'C:\ProgramData\CoastalWarning\runtime\deployment-id.txt'
$runtimeIdentityFiles = @{
    8000 = 'C:\ProgramData\CoastalWarning\run\main-runtime.json'
    8001 = 'C:\ProgramData\CoastalWarning\run\gateway-runtime.json'
}
$failed = $false

function Read-RuntimeDeploymentId {
    param([Parameter(Mandatory)] [string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -gt 128) {
        return $null
    }
    $value = [IO.File]::ReadAllText($Path).Trim()
    if ($value -cnotmatch '^[0-9a-f]{32}$') {
        return $null
    }
    return $value
}

function Test-RuntimeIdentityFile {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$DeploymentId,
        [Parameter(Mandatory)] [int]$Port,
        [Parameter(Mandatory)] [long]$ListenerPid
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -gt 4096) {
        return $false
    }
    try {
        $identity = [IO.File]::ReadAllText($Path) |
            ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return $false
    }
    $identityPid = [long]0
    $identityPort = [int]0
    return (
        [string]$identity.deployment_id -ceq $DeploymentId -and
        [long]::TryParse([string]$identity.pid, [ref]$identityPid) -and
        [int]::TryParse([string]$identity.port, [ref]$identityPort) -and
        $identityPid -eq $ListenerPid -and
        $identityPort -eq $Port
    )
}

function Test-ProtectedSecretFile {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Pattern
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $false
    }
    $value = [IO.File]::ReadAllText($Path).Trim()
    if ($value -cnotmatch $Pattern) {
        return $false
    }
    $value = $null

    $trustedSids = @('S-1-5-18', 'S-1-5-32-544')
    $fullControlSids = @{}
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) {
        return $false
    }
    try {
        $owner = [Security.Principal.NTAccount]::new($acl.Owner)
        $ownerSid = $owner.Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        return $false
    }
    if ($ownerSid -notin $trustedSids) {
        return $false
    }
    foreach ($rule in $acl.Access) {
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        }
        catch {
            return $false
        }
        if ($sid -notin $trustedSids -or
            $rule.AccessControlType -ne
                [Security.AccessControl.AccessControlType]::Allow) {
            return $false
        }
        $required = [Security.AccessControl.FileSystemRights]::FullControl
        if (($rule.FileSystemRights -band $required) -eq $required) {
            $fullControlSids[$sid] = $true
        }
    }
    return ($fullControlSids.ContainsKey('S-1-5-18') -and
        $fullControlSids.ContainsKey('S-1-5-32-544'))
}

Write-Host '=== Cloudflared service ==='
$service = Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue
if ($null -eq $service) {
    Write-Warning 'cloudflared service is missing'
    $failed = $true
}
else {
    $service | Select-Object Name, Status, StartType | Format-Table -AutoSize
    if ($service.Status -ne 'Running') {
        $failed = $true
    }
}

Write-Host '=== Scheduled tasks ==='
foreach ($taskName in @('CoastalWarning-Main', 'CoastalWarning-Gateway')) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Warning "$taskName is missing"
        $failed = $true
        continue
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    [pscustomobject]@{
        Task = $taskName
        State = $task.State
        LastResult = $info.LastTaskResult
        LastRun = $info.LastRunTime
    } | Format-Table -AutoSize
    if ($task.State -ne 'Running') {
        $failed = $true
    }
}

Write-Host '=== Listening ports ==='
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object LocalPort -In 8000, 8001)
$listeners |
    Select-Object LocalAddress, LocalPort, OwningProcess |
    Sort-Object LocalPort |
    Format-Table -AutoSize
$runtimeDeploymentId = Read-RuntimeDeploymentId `
    -Path $runtimeDeploymentIdFile
if ($null -eq $runtimeDeploymentId) {
    Write-Warning 'Active runtime deployment identifier is missing or invalid'
    $failed = $true
}
foreach ($requiredPort in @(8000, 8001)) {
    $listenerPids = @(
        $listeners |
            Where-Object LocalPort -EQ $requiredPort |
            ForEach-Object { [long]$_.OwningProcess } |
            Sort-Object -Unique
    )
    if ($listenerPids.Count -ne 1) {
        Write-Warning "Port $requiredPort does not have exactly one listener process"
        $failed = $true
        continue
    }
    $identityVerified = $false
    if ($null -ne $runtimeDeploymentId) {
        $identityVerified = Test-RuntimeIdentityFile `
            -Path ([string]$runtimeIdentityFiles[$requiredPort]) `
            -DeploymentId $runtimeDeploymentId -Port $requiredPort `
            -ListenerPid $listenerPids[0]
    }
    [pscustomobject]@{
        Port = $requiredPort
        ListenerPid = $listenerPids[0]
        RuntimeIdentity = $identityVerified
    } | Format-Table -AutoSize
    if (-not $identityVerified) {
        Write-Warning "Port $requiredPort did not prove the active runtime identity"
        $failed = $true
    }
}

Write-Host '=== Credential files ==='
$credentialChecks = @(
    [pscustomobject]@{
        Name = 'Device token'
        Path = $tokenFile
        Pattern = '^[A-Za-z0-9_-]{32,}$'
    },
    [pscustomobject]@{
        Name = 'Admin password hash'
        Path = $adminPasswordHashFile
        Pattern = '^pbkdf2_sha256\$310000\$[0-9a-f]{32}\$[0-9a-f]{64}$'
    },
    [pscustomobject]@{
        Name = 'Admin session secret'
        Path = $adminSessionSecretFile
        Pattern = '^[A-Za-z0-9_-]{43}$'
    }
)
foreach ($credential in $credentialChecks) {
    $isProtected = Test-ProtectedSecretFile -Path $credential.Path `
        -Pattern $credential.Pattern
    [pscustomobject]@{
        Credential = $credential.Name
        Protected = $isProtected
    } | Format-Table -AutoSize
    if (-not $isProtected) {
        $failed = $true
    }
}

if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
    Write-Warning "Device token file not found: $tokenFile"
    exit 1
}

$token = [System.IO.File]::ReadAllText($tokenFile).Trim()
$headers = @{ 'X-Device-Token' = $token }
try {
    $local = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/v1/health' `
        -Headers $headers -TimeoutSec 10
    Write-Host "Local gateway: $($local.status), database=$($local.database)"
    if ($local.status -ne 'ok') { $failed = $true }
}
catch {
    Write-Warning "Local gateway check failed: $($_.Exception.Message)"
    $failed = $true
}

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $public = Invoke-RestMethod -Uri 'https://weather.ashlxylock.uk/api/v1/health' `
        -Headers $headers -TimeoutSec 20
    Write-Host "Public gateway: $($public.status), database=$($public.database)"
    if ($public.status -ne 'ok') { $failed = $true }
}
catch {
    Write-Warning "Public gateway check failed: $($_.Exception.Message)"
    $failed = $true
}

foreach ($adminLogin in @(
        [pscustomobject]@{
            Name = 'Local admin login'
            Uri = 'http://127.0.0.1:8001/admin/login'
        },
        [pscustomobject]@{
            Name = 'Public admin login'
            Uri = 'https://weather.ashlxylock.uk/admin/login'
        }
    )) {
    try {
        $response = Invoke-WebRequest -Uri $adminLogin.Uri -UseBasicParsing `
            -TimeoutSec 20
        Write-Host "$($adminLogin.Name): HTTP $($response.StatusCode)"
        if ($response.StatusCode -ne 200) { $failed = $true }
    }
    catch {
        Write-Warning "$($adminLogin.Name) failed: $($_.Exception.Message)"
        $failed = $true
    }
}

$token = $null
if ($failed) { exit 1 }
exit 0
