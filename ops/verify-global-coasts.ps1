[CmdletBinding()]
param(
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

trap {
    if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
        [pscustomobject]@{
            error = $_.Exception.Message
        } | ConvertTo-Json | Set-Content -LiteralPath $OutputPath -Encoding utf8
    }
    exit 1
}

$tokenFile = 'C:\ProgramData\CoastalWarning\secrets\device-token.txt'
$gateway = 'https://weather.ashlxylock.uk'
$headers = @{
    'X-Device-Token' = [IO.File]::ReadAllText($tokenFile).Trim()
}
$response = Invoke-RestMethod -Uri "$gateway/api/v1/locations/presets" `
    -Headers $headers -TimeoutSec 20
$rows = if ($response -is [Array]) { $response } else { @($response) }

$invalidKinds = @($rows | Where-Object { $_.kind -ne 'coast' })
$inlandLabels = @(
    $rows | Where-Object {
        $_.display_location -match 'LONDON|CHANGCHUN'
    }
)
if ($rows.Count -ne 16) {
    throw "Expected 16 global coasts, received $($rows.Count)"
}
if ($invalidKinds.Count -ne 0 -or $inlandLabels.Count -ne 0) {
    throw 'The deployed catalogue contains a non-coast or inland preset'
}

$result = [pscustomobject]@{
    count = $rows.Count
    all_coast = $true
    first = $rows[0]
    last = $rows[-1]
}
$json = $result | ConvertTo-Json -Depth 5
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $json
}
else {
    $json | Set-Content -LiteralPath $OutputPath -Encoding utf8
}
