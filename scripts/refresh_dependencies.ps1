[CmdletBinding()]
param(
    [string]$Python = "3.12"
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/."
}

Push-Location $workspace
try {
    uv lock --quiet --python $Python
    if ($LASTEXITCODE -ne 0) {
        throw "uv lock failed."
    }

    uv export `
        --quiet `
        --locked `
        --format requirements-txt `
        --no-dev `
        --no-hashes `
        --no-header `
        --output-file requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime dependency export failed."
    }

    uv export `
        --quiet `
        --locked `
        --format requirements-txt `
        --no-hashes `
        --no-header `
        --output-file requirements-dev.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Development dependency export failed."
    }
}
finally {
    Pop-Location
}
