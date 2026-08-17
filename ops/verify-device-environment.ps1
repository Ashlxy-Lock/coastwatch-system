[CmdletBinding()]
param(
    [ValidateSet('coast', 'place')]
    [string]$ExpectedKind = 'coast',
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$tokenFile = 'C:\ProgramData\CoastalWarning\secrets\device-token.txt'
$gateway = 'https://weather.ashlxylock.uk'
$headers = @{
    'X-Device-Token' = [IO.File]::ReadAllText($tokenFile).Trim()
}
$environment = Invoke-RestMethod `
    -Uri "$gateway/api/v1/environment?device_id=COAST_01" `
    -Headers $headers -TimeoutSec 20
if ($environment.kind -ne $ExpectedKind) {
    throw "Expected environment kind '$ExpectedKind', got '$($environment.kind)'"
}
if ($ExpectedKind -eq 'coast' -and $null -eq $environment.wave_height_m) {
    throw 'Coastal environment has no wave data'
}
if ($ExpectedKind -eq 'place' -and $null -ne $environment.wave_height_m) {
    throw 'Plain location unexpectedly contains wave data'
}

$result = [pscustomobject]@{
    location = $environment.location
    display_location = $environment.display_location
    kind = $environment.kind
    source = $environment.source
    stale = $environment.stale
    air_temperature_c = $environment.air_temperature_c
    wave_height_m = $environment.wave_height_m
    water_temperature_c = $environment.water_temperature_c
}
$json = $result | ConvertTo-Json
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $json
}
else {
    $json | Set-Content -LiteralPath $OutputPath -Encoding utf8
}
