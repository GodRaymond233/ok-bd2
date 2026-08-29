[CmdletBinding()]
param(
    [ValidateSet("Focused", "Final", "Release")]
    [string]$Mode = "Final",
    [string[]]$Tests = @(),
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot

function Resolve-PythonExecutable {
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Python)) {
        $candidates += $Python
    }
    if (-not [string]::IsNullOrWhiteSpace($env:OK_BD2_PYTHON)) {
        $candidates += $env:OK_BD2_PYTHON
    }
    $candidates += Join-Path $workspace ".venv\Scripts\python.exe"

    $commonDir = (& git -C $workspace rev-parse --git-common-dir 2>$null)
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($commonDir)) {
        $commonPath = $commonDir.Trim()
        if (-not [System.IO.Path]::IsPathRooted($commonPath)) {
            $commonPath = Join-Path $workspace $commonPath
        }
        $commonPath = [System.IO.Path]::GetFullPath($commonPath)
        $candidates += Join-Path (Split-Path -Parent $commonPath) ".venv\Scripts\python.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }

    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $systemPython) {
        return $systemPython.Source
    }
    throw "Python was not found. Pass -Python or set OK_BD2_PYTHON."
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    Write-Host "==> $Description"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Assert-NoKeyboardAutomation {
    $patterns = @(
        "send_key",
        "send_key_down",
        "send_key_up",
        "register_hotkey",
        "KEYBDINPUT",
        "keyboard_mapping",
        "keyboard_map",
        "key_mapping"
    )
    $matches = Get-ChildItem -LiteralPath (Join-Path $workspace "src") -Recurse -File `
        -Filter "*.py" | Select-String -Pattern $patterns -CaseSensitive:$false
    if ($matches) {
        $matches | ForEach-Object { Write-Host $_ }
        throw "Prohibited keyboard automation implementation was found under src."
    }
}

$pythonExecutable = Resolve-PythonExecutable
$previousRuffCache = $env:RUFF_CACHE_DIR
$previousPycachePrefix = $env:PYTHONPYCACHEPREFIX
$assignedRuffCache = [string]::IsNullOrWhiteSpace($previousRuffCache)
$assignedPycachePrefix = [string]::IsNullOrWhiteSpace($previousPycachePrefix)

if ($assignedRuffCache) {
    $env:RUFF_CACHE_DIR = Join-Path $env:TEMP "ok-bd2-ruff-cache"
}
if ($assignedPycachePrefix) {
    $env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP "ok-bd2-pycache"
}

Push-Location $workspace
try {
    if ($Mode -eq "Focused") {
        if ($Tests.Count -eq 0) {
            throw "Focused mode requires at least one unittest target through -Tests."
        }
        $focusedArguments = @("-m", "unittest") + $Tests + @("-q")
        Invoke-NativeCommand `
            -FilePath $pythonExecutable `
            -Arguments $focusedArguments `
            -Description "Focused unit tests"
        Write-Host "Focused checks passed."
        return
    }

    Invoke-NativeCommand `
        -FilePath $pythonExecutable `
        -Arguments @("-m", "ruff", "check", ".") `
        -Description "Ruff"
    Invoke-NativeCommand `
        -FilePath $pythonExecutable `
        -Arguments @("-m", "compileall", "-q", "src", "tests", "main.py", "main_debug.py") `
        -Description "Python compileall"
    Invoke-NativeCommand `
        -FilePath $pythonExecutable `
        -Arguments @("-m", "unittest", "discover", "-s", "tests", "-q") `
        -Description "Full unit test suite"
    Invoke-NativeCommand `
        -FilePath "git" `
        -Arguments @("diff", "--check") `
        -Description "Git whitespace check"

    Write-Host "==> Keyboard automation restriction scan"
    Assert-NoKeyboardAutomation

    if ($Mode -eq "Release") {
        Write-Host "==> Dependency lock and export validation"
        & (Join-Path $PSScriptRoot "check_dependency_exports.ps1")
        Invoke-NativeCommand `
            -FilePath "uv" `
            -Arguments @("pip", "check", "--python", $pythonExecutable) `
            -Description "Installed dependency consistency"
    }

    Write-Host "$Mode checks passed."
}
finally {
    Pop-Location
    if ($assignedRuffCache) {
        if ($null -eq $previousRuffCache) {
            Remove-Item Env:RUFF_CACHE_DIR -ErrorAction SilentlyContinue
        }
        else {
            $env:RUFF_CACHE_DIR = $previousRuffCache
        }
    }
    if ($assignedPycachePrefix) {
        if ($null -eq $previousPycachePrefix) {
            Remove-Item Env:PYTHONPYCACHEPREFIX -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONPYCACHEPREFIX = $previousPycachePrefix
        }
    }
}
