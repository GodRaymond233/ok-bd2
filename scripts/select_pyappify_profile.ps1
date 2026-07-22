param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("China", "Global")]
    [string]$Profile,
    [string]$ConfigPath = "pyappify.yml"
)

$ErrorActionPreference = "Stop"

$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$lines = @(Get-Content -LiteralPath $resolvedConfig)
$profilesIndex = [Array]::IndexOf($lines, "profiles:")
if ($profilesIndex -lt 0) {
    throw "profiles section was not found in $resolvedConfig"
}

$header = @($lines[0..$profilesIndex])
$selectedBlock = [System.Collections.Generic.List[string]]::new()
$currentProfile = ""
$selectedCount = 0

foreach ($line in $lines[($profilesIndex + 1)..($lines.Count - 1)]) {
    if ($line -match '^  - name:\s*["'']?([^"'']+)["'']?\s*$') {
        $currentProfile = $Matches[1].Trim()
        if ($currentProfile -eq $Profile) {
            $selectedCount += 1
        }
    }
    if ($currentProfile -eq $Profile) {
        $selectedBlock.Add($line)
    }
}
if ($selectedCount -ne 1) {
    throw "Expected exactly one '$Profile' profile in $resolvedConfig, found $selectedCount."
}

$output = @($header + $selectedBlock)
Set-Content -LiteralPath $resolvedConfig -Value $output -Encoding UTF8
Write-Host "Selected PyAppify profile: $Profile"
