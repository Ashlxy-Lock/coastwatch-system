[CmdletBinding()]
param(
    [string]$LocationId = 'geo_2643743',
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$tokenFile = 'C:\ProgramData\CoastalWarning\secrets\device-token.txt'
$gateway = 'https://weather.ashlxylock.uk'
$headers = @{
    'X-Device-Token' = [IO.File]::ReadAllText($tokenFile).Trim()
}
$body = @{
    device_id = 'COAST_01'
    location_id = $LocationId
} | ConvertTo-Json -Compress

$stopwatch = [Diagnostics.Stopwatch]::StartNew()
$response = Invoke-RestMethod -Uri "$gateway/api/v1/device-location" `
    -Method Put -Headers $headers -ContentType 'application/json' `
    -Body $body -TimeoutSec 20
$stopwatch.Stop()

$result = [pscustomobject]@{
    id = $response.id
    display_location = $response.display_location
    elapsed_ms = $stopwatch.ElapsedMilliseconds
}
$json = $result | ConvertTo-Json
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $json
}
else {
    $json | Set-Content -LiteralPath $OutputPath -Encoding utf8
}

