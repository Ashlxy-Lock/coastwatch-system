[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$serverDir = Join-Path $projectRoot 'server'
$pythonExe = Join-Path $serverDir '.venv\Scripts\python.exe'
$uvicornBootstrap = Join-Path $projectRoot 'ops\run-uvicorn.py'
$deploymentIdFile = Join-Path $projectRoot 'deployment-id.txt'
$runtimeIdentityFile = 'C:\ProgramData\CoastalWarning\run\gateway-runtime.json'
$tokenFile = 'C:\ProgramData\CoastalWarning\secrets\device-token.txt'
$adminPasswordHashFile = 'C:\ProgramData\CoastalWarning\secrets\admin-password-hash.txt'
$adminSessionSecretFile = 'C:\ProgramData\CoastalWarning\secrets\admin-session-secret.txt'
$logDir = 'C:\ProgramData\CoastalWarning\logs'
$logFile = Join-Path $logDir 'gateway.log'
$databaseFile = 'C:\ProgramData\CoastalWarning\data\coastal_warning.db'
$customModelFile = 'C:\ProgramData\CoastalWarning\models\custom_water_v1.json'
$officialDatasetRoot = 'C:\ProgramData\CoastalWarning\data\official_datasets'
$officialRegistryDir = 'C:\ProgramData\CoastalWarning\data\official_registry'
$officialArtifactDir = 'C:\ProgramData\CoastalWarning\models\official_runs'

function Write-SafeFailure {
    param([Parameter(Mandatory)] [System.Management.Automation.ErrorRecord]$Record)

    try {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $timestamp = [DateTime]::UtcNow.ToString('o')
        $errorType = $Record.Exception.GetType().FullName
        $errorMessage = $Record.Exception.Message.Replace("`r", ' ').Replace("`n", ' ')
        $stack = [string]$Record.ScriptStackTrace
        [IO.File]::AppendAllText(
            $logFile,
            "[$timestamp] startup_error type=$errorType message=$errorMessage`r`nstack=$stack`r`n"
        )
    }
    catch {
        # Scheduled Task History still retains the non-zero exit code when even
        # the protected log path is unavailable.
    }
}

function Read-ValidatedSecretFile {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Pattern,
        [Parameter(Mandatory)] [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description file not found: $Path"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing reparse-point $Description file: $Path"
    }
    $value = [IO.File]::ReadAllText($Path).Trim()
    if ($value -cnotmatch $Pattern) {
        throw "$Description file is invalid"
    }
    return $value
}

$deviceToken = $null
$adminPasswordHash = $null
$adminSessionSecret = $null
try {
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        throw "Python virtual environment not found: $pythonExe"
    }
    foreach ($requiredFile in @($uvicornBootstrap, $deploymentIdFile)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Runtime bootstrap file not found: $requiredFile"
        }
        $item = Get-Item -LiteralPath $requiredFile -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing reparse-point runtime bootstrap file: $requiredFile"
        }
    }
    if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
        throw "Device token file not found: $tokenFile"
    }

    $deviceToken = [System.IO.File]::ReadAllText($tokenFile).Trim()
    if ([string]::IsNullOrWhiteSpace($deviceToken) -or $deviceToken.Length -lt 32) {
        throw 'Device token file is empty or invalid'
    }
    $adminPasswordHash = Read-ValidatedSecretFile `
        -Path $adminPasswordHashFile `
        -Pattern '^pbkdf2_sha256\$310000\$[0-9a-f]{32}\$[0-9a-f]{64}$' `
        -Description 'Administrator password hash'
    $adminSessionSecret = Read-ValidatedSecretFile `
        -Path $adminSessionSecretFile `
        -Pattern '^[A-Za-z0-9_-]{43}$' `
        -Description 'Administrator session secret'

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    if ((Test-Path -LiteralPath $logFile) -and
        (Get-Item -LiteralPath $logFile).Length -gt 5MB) {
        Move-Item -LiteralPath $logFile -Destination "$logFile.1" -Force
    }

    $env:COAST_DEVICE_TOKEN = $deviceToken
    $env:COAST_ADMIN_PASSWORD_HASH_FILE = $adminPasswordHashFile
    $env:COAST_ADMIN_SESSION_SECRET_FILE = $adminSessionSecretFile
    $env:COASTAL_DB_PATH = $databaseFile
    $env:COAST_CUSTOM_MODEL_PATH = $customModelFile
    $env:COAST_OFFICIAL_DATASET_ROOT = $officialDatasetRoot
    $env:COAST_OFFICIAL_REGISTRY_DIR = $officialRegistryDir
    $env:COAST_OFFICIAL_ARTIFACT_DIR = $officialArtifactDir
    $deviceToken = $null
    $adminPasswordHash = $null
    $adminSessionSecret = $null
    Set-Location -LiteralPath $serverDir
    # Uvicorn writes normal lifecycle messages to stderr. Windows PowerShell
    # 5.1 converts redirected native stderr into ErrorRecord objects, which
    # would otherwise be terminated by the script-wide Stop preference.
    $savedErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $pythonExe $uvicornBootstrap --app app.gateway:app `
            --host 127.0.0.1 --port 8001 `
            --app-dir $serverDir `
            --deployment-id-file $deploymentIdFile `
            --identity-file $runtimeIdentityFile *>> $logFile
        $uvicornExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorPreference
    }
    if ($uvicornExitCode -ne 0) {
        throw "Gateway uvicorn process exited with code $uvicornExitCode"
    }
}
catch {
    Write-SafeFailure -Record $_
    exit 1
}
finally {
    $deviceToken = $null
    $adminPasswordHash = $null
    $adminSessionSecret = $null
    Remove-Item Env:COAST_DEVICE_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:COAST_ADMIN_PASSWORD_HASH_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:COAST_ADMIN_SESSION_SECRET_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:COASTAL_DB_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:COAST_CUSTOM_MODEL_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:COAST_OFFICIAL_DATASET_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:COAST_OFFICIAL_REGISTRY_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:COAST_OFFICIAL_ARTIFACT_DIR -ErrorAction SilentlyContinue
}
exit 0
