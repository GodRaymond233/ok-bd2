[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required to validate dependency exports."
}

function Read-NormalizedText([string]$Path) {
    return [System.IO.File]::ReadAllText($Path).Replace("`r`n", "`n")
}

$runtimeTemp = [System.IO.Path]::GetTempFileName()
$devTemp = [System.IO.Path]::GetTempFileName()

Push-Location $workspace
try {
    uv lock --quiet --check
    if ($LASTEXITCODE -ne 0) {
        throw "uv.lock is missing or stale. Run .\scripts\refresh_dependencies.ps1."
    }

    uv export `
        --quiet `
        --locked `
        --format requirements-txt `
        --no-dev `
        --no-hashes `
        --no-header `
        --output-file $runtimeTemp
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime dependency export validation failed."
    }

    uv export `
        --quiet `
        --locked `
        --format requirements-txt `
        --no-hashes `
        --no-header `
        --output-file $devTemp
    if ($LASTEXITCODE -ne 0) {
        throw "Development dependency export validation failed."
    }

    if (
        (Read-NormalizedText $runtimeTemp) -cne
        (Read-NormalizedText (Join-Path $workspace "requirements.txt"))
    ) {
        throw "requirements.txt is stale. Run .\scripts\refresh_dependencies.ps1."
    }

    if (
        (Read-NormalizedText $devTemp) -cne
        (Read-NormalizedText (Join-Path $workspace "requirements-dev.txt"))
    ) {
        throw "requirements-dev.txt is stale. Run .\scripts\refresh_dependencies.ps1."
    }
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $runtimeTemp, $devTemp -Force -ErrorAction SilentlyContinue
}
