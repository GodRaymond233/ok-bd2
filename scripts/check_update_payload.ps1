$ErrorActionPreference = 'Stop'
$deploy = Get-Item -LiteralPath 'deploy.txt'
if ([IO.File]::ReadAllBytes($deploy.FullName) -contains 13) {
    throw 'deploy.txt must use LF line endings.'
}
$entries = @(Get-Content -LiteralPath $deploy.FullName -Encoding UTF8 | Where-Object { $_ })
if ($entries -cnotcontains 'ocr_bundle/onnxocr') {
    throw 'deploy.txt is missing the bundled OCR directory.'
}
foreach ($entry in $entries) {
    if (-not (Test-Path -LiteralPath $entry)) {
        throw "Missing update payload entry: $entry"
    }
}
if ((Get-Content -LiteralPath 'requirements.txt' -Raw) -match '(?im)^\s*(ok-script|onnxocr-ppocrv6)\b') {
    throw "requirements.txt still lists an inlined package: $($Matches[1])"
}
