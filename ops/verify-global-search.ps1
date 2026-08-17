[CmdletBinding()]
param(
    [ValidateLength(2, 80)]
    [string]$Query = 'London',

    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$tokenFile = 'C:\ProgramData\CoastalWarning\secrets\device-token.txt'
$gateway = 'https://weather.ashlxylock.uk'
if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
    throw "Device token file is unavailable: $tokenFile"
}

$token = [IO.File]::ReadAllText($tokenFile).Trim()
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'Device token is blank'
}
$headers = @{ 'X-Device-Token' = $token }
$health = Invoke-RestMethod -Uri "$gateway/api/v1/health" `
    -Headers $headers -TimeoutSec 10
$encodedQuery = [Uri]::EscapeDataString($Query)
$response = Invoke-RestMethod `
    -Uri "$gateway/api/v1/locations/search?q=$encodedQuery&count=8" `
    -Headers $headers -TimeoutSec 20
if ($null -eq $response) {
    $rowCount = 0
    $firstRow = $null
}
elseif ($response -is [Array]) {
    $rowCount = $response.Length
    $firstRow = $response[0]
}
else {
    $rowCount = 1
    $firstRow = $response
}
if ($health.status -ne 'ok') {
    throw "Gateway health check failed: $($health.status)"
}
if ($rowCount -eq 0) {
    throw "Global location search returned no rows for '$Query'"
}

$result = [pscustomobject]@{
    health = $health.status
    query = $Query
    count = $rowCount
    first = $firstRow
}
$json = $result | ConvertTo-Json -Depth 5
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $json
}
else {
    $json | Set-Content -LiteralPath $OutputPath -Encoding utf8
}
