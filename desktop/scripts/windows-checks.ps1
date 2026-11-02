# Stage 1 Windows checks for the desktop client (run in the electron-client worktree).
#
# Steps: Python gate (run_checks Final) -> UI tests and build -> shell tests (lifecycle tests
# with the venv interpreter) and package -> lifecycle tests with the packaged Electron and the
# standalone interpreter -> lifecycle probe with the real service layer -> desktop probe (hotkey,
# tray, close, quit, zoom, notifications) -> shell smoke with the real core (no game needed) ->
# bootstrap smoke -> process cleanup.
# Every native step runs as its own process with stdout/stderr written to files under the
# output directory (PowerShell never wraps native stderr), and summary.json lists them all.
# Nothing here starts the game or sends input to it.
param(
    [string]$OutDir = '',
    [switch]$CreateVenv,
    [switch]$SkipFinal,
    [int]$SmokeTimeoutSeconds = 240
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $OutDir) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutDir = Join-Path $root ".local-dev\outputs\electron-p1-windows-checks\$stamp"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$python = Join-Path $root '.venv\Scripts\python.exe'
$shellDir = Join-Path $root 'desktop\shell'
$uiDir = Join-Path $root 'desktop\ui'
$unpacked = Join-Path $shellDir 'release\win-unpacked'
$results = [ordered]@{}

function Add-Result {
    param([string]$Name, [int]$Code, [datetime]$Started, [string[]]$Logs)
    $results[$Name] = [ordered]@{
        exit_code = $Code
        seconds = [math]::Round(((Get-Date) - $Started).TotalSeconds, 1)
        logs = $Logs
    }
    if ($Code -ne 0) {
        Write-Host "FAILED: $Name (exit $Code)"
    }
    return ($Code -eq 0)
}

function Invoke-NativeStep {
    param([string]$Name, [string]$File, [string[]]$Arguments, [string]$WorkingDirectory = $root)
    $out = Join-Path $OutDir "$Name.out.log"
    $err = Join-Path $OutDir "$Name.err.log"
    Write-Host "==> $Name (logs: $out, $err)"
    $started = Get-Date
    try {
        $process = Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory `
            -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        $null = $process.Handle  # keeps ExitCode readable after the wait
        $process.WaitForExit()
        $code = $process.ExitCode
    } catch {
        ($_ | Out-String) | Add-Content -Encoding UTF8 $err
        $code = 1
    }
    foreach ($log in @($out, $err)) {
        if ((Test-Path $log) -and (Get-Item $log).Length -gt 0) {
            Write-Host "--- tail of $log"
            Get-Content -Tail 25 $log | Out-Host
        }
    }
    return (Add-Result -Name $Name -Code $code -Started $started -Logs @($out, $err))
}

function Invoke-Npm {
    param([string]$Name, [string]$Directory, [string[]]$NpmArguments)
    return (Invoke-NativeStep -Name $Name -File 'cmd.exe' -Arguments (@('/d', '/c', 'npm') + $NpmArguments) -WorkingDirectory $Directory)
}

function Get-ToolVersion {
    param([string]$Command)
    try {
        return ((& $Command --version) | Out-String).Trim()
    } catch {
        return 'missing'
    }
}

function Write-Summary {
    param([bool]$Passed)
    $summary = [ordered]@{
        passed = $Passed
        commit = (& git -C $root rev-parse HEAD)
        branch = (& git -C $root branch --show-current)
        uncommitted = @(& git -C $root status --porcelain).Count
        when = (Get-Date).ToString('s')
        node = Get-ToolVersion 'node'
        npm = Get-ToolVersion 'npm.cmd'
        python = if (Test-Path $python) { Get-ToolVersion $python } else { 'missing' }
        elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        steps = $results
    }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $OutDir 'summary.json')
    Write-Host "Summary: $(Join-Path $OutDir 'summary.json')"
}

function Test-NoLeftovers {
    param([object]$Report, [string]$Label)
    $alive = @()
    foreach ($id in @($Report.shellPid, $Report.corePid)) {
        if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) {
            $alive += $id
        }
    }
    $fromShell = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($unpacked, [StringComparison]::OrdinalIgnoreCase)
    })
    $cores = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains('--service') -and
        $_.CommandLine.Contains($root)
    })
    Write-Host "$Label leftovers: registered=$($alive -join ',') shell=$($fromShell.Count) core=$($cores.Count)"
    return ($alive.Count -eq 0 -and $fromShell.Count -eq 0 -and $cores.Count -eq 0)
}

# Starts one smoke cycle (the shell writes a JSON report and quits) and checks that every
# process it started is gone afterwards. Only the process started here is ever ended.
function Invoke-SmokeStep {
    param([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$ReportFile)
    Write-Host "==> $Name (report: $ReportFile)"
    $started = Get-Date
    $code = 1
    if (Test-Path $ReportFile) { Remove-Item -Force $ReportFile }
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $root -PassThru
    $null = $process.Handle
    if (-not $process.WaitForExit($SmokeTimeoutSeconds * 1000)) {
        Write-Host "$Name did not finish within $SmokeTimeoutSeconds s; ending the process it started."
        $process.Kill()
    } elseif (-not (Test-Path $ReportFile)) {
        Write-Host "$Name wrote no report (exit $($process.ExitCode))."
    } else {
        Get-Content -Raw -Encoding UTF8 $ReportFile | Out-Host
        $report = Get-Content -Raw -Encoding UTF8 $ReportFile | ConvertFrom-Json
        Start-Sleep -Seconds 2
        $clean = Test-NoLeftovers -Report $report -Label $Name
        if ($report.ok -eq $true -and $clean -and $process.ExitCode -eq 0) {
            $code = 0
        } else {
            Write-Host "$Name report ok=$($report.ok) clean=$clean exit=$($process.ExitCode)"
        }
    }
    return (Add-Result -Name $Name -Code $code -Started $started -Logs @($ReportFile))
}

$passed = $true

if ($CreateVenv -and -not (Test-Path $python)) {
    $passed = (Invoke-NativeStep -Name 'venv' -File 'uv' -Arguments @('sync', '--frozen')) -and $passed
}
if (-not (Test-Path $python)) {
    Write-Host "Missing $python. Create the worktree's own venv first: uv sync --frozen (or rerun with -CreateVenv)."
    $results['venv'] = [ordered]@{ exit_code = 1; seconds = 0; logs = @() }
    Write-Summary -Passed $false
    exit 1
}
$passed = (Invoke-NativeStep -Name 'python-deps' -File $python -Arguments @('-c', '"import fastapi, uvicorn, wsproto; print(''service deps ok'')"')) -and $passed

if (-not $SkipFinal) {
    $runChecks = Join-Path $root 'scripts\run_checks.ps1'
    $passed = (Invoke-NativeStep -Name 'run-checks-final' -File 'powershell.exe' -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$runChecks`"", '-Mode', 'Final')) -and $passed
}

$passed = (Invoke-Npm -Name 'ui-npm-ci' -Directory $uiDir -NpmArguments @('ci', '--no-audit', '--no-fund')) -and $passed
$passed = (Invoke-Npm -Name 'ui-test' -Directory $uiDir -NpmArguments @('test')) -and $passed
$passed = (Invoke-Npm -Name 'ui-build' -Directory $uiDir -NpmArguments @('run', 'build')) -and $passed
$passed = (Invoke-Npm -Name 'shell-npm-ci' -Directory $shellDir -NpmArguments @('ci', '--no-audit', '--no-fund')) -and $passed
# The lifecycle tests use a Python stand-in core: first the venv interpreter (a redirector).
$env:OKBD2_TEST_PYTHON = $python
try {
    $passed = (Invoke-Npm -Name 'shell-test' -Directory $shellDir -NpmArguments @('test')) -and $passed
} finally {
    Remove-Item Env:OKBD2_TEST_PYTHON -ErrorAction SilentlyContinue
}
$passed = (Invoke-Npm -Name 'shell-pack' -Directory $shellDir -NpmArguments @('run', 'pack:win')) -and $passed

$exe = Join-Path $unpacked 'ok-bd2-desktop.exe'
if (Test-Path $exe) {
    # Again with the packaged Electron as the shell and the standalone interpreter behind the
    # venv, which is how PyAppify starts the core.
    $standalone = ((& $python -c 'import sys; print(sys._base_executable)') | Out-String).Trim()
    $results['standalone-python'] = [ordered]@{ exit_code = 0; seconds = 0; logs = @(); path = $standalone }
    $env:OKBD2_TEST_PYTHON = $standalone
    $env:OKBD2_TEST_HOST = $exe
    try {
        $passed = (Invoke-NativeStep -Name 'shell-lifecycle-electron' -File 'cmd.exe' -Arguments @('/d', '/c', 'node', '--test', 'test/lifecycle.test.mjs') -WorkingDirectory $shellDir) -and $passed
    } finally {
        Remove-Item Env:OKBD2_TEST_PYTHON, Env:OKBD2_TEST_HOST -ErrorAction SilentlyContinue
    }
    # Real service layer, both interpreters: normal stop, shell crash, core crash, ready
    # timeout, shell crash during startup, same-directory mutex (lifecycle-probe.json).
    $probe = Join-Path $root 'desktop\scripts\lifecycle_probe.py'
    $probeOut = Join-Path $OutDir 'lifecycle-probe'
    $passed = (Invoke-NativeStep -Name 'lifecycle-probe' -File $python -Arguments @("`"$probe`"", '--out', "`"$probeOut`"", '--host', "`"$exe`"")) -and $passed

    # Desktop behaviour on real Electron and the real service layer, with a stand-in game start
    # and throwaway configs (desktop-probe.json). It needs two free keys among F9-F12.
    $desktopProbe = Join-Path $root 'desktop\scripts\desktop_probe.cjs'
    $desktopProbeOut = Join-Path $OutDir 'desktop-probe'
    $passed = (Invoke-NativeStep -Name 'desktop-probe' -File 'cmd.exe' -Arguments @('/d', '/c', 'node', "`"$desktopProbe`"", '--python', "`"$python`"", '--out', "`"$desktopProbeOut`"")) -and $passed

    $smoke = Join-Path $OutDir 'shell-smoke.json'
    $arguments = @(
        '--core-python', "`"$python`"",
        '--core-script', 'main.py',
        '--core-cwd', "`"$root`"",
        '--user-data', "`"$(Join-Path $OutDir 'shell-data')`"",
        '--smoke-test', "`"$smoke`""
    )
    $passed = (Invoke-SmokeStep -Name 'shell-smoke' -Executable $exe -Arguments $arguments -ReportFile $smoke) -and $passed

    $bootSmoke = Join-Path $OutDir 'bootstrap-smoke.json'
    $arguments = @('main_desktop.py', '--smoke-test', "`"$bootSmoke`"")
    $passed = (Invoke-SmokeStep -Name 'bootstrap-smoke' -Executable $python -Arguments $arguments -ReportFile $bootSmoke) -and $passed

    $size = (Get-ChildItem -Recurse -File $unpacked | Measure-Object -Property Length -Sum).Sum
    $results['shell-size'] = [ordered]@{ exit_code = 0; seconds = 0; logs = @(); bytes = $size }
    Write-Host ("Packaged shell: {0:N1} MB unpacked" -f ($size / 1MB))
} else {
    Write-Host "No packaged shell at $exe; smoke steps skipped."
    $passed = $false
}

Write-Summary -Passed $passed
if ($passed) {
    Write-Host 'ALL STAGE 1 WINDOWS CHECKS PASSED'
    exit 0
}
Write-Host 'SOME STAGE 1 WINDOWS CHECKS FAILED (see summary.json and the step logs)'
exit 1
