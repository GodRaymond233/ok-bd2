param(
    [Parameter(Mandatory)][string]$Launcher,
    [Parameter(Mandatory)][string]$Working,
    [Parameter(Mandatory)][ValidatePattern('^v?\d+\.\d+\.\d+$')][string]$Target,
    [Parameter(Mandatory)][ValidatePattern('^v?\d+\.\d+\.\d+$')][string]$Previous
)
$ErrorActionPreference = 'Stop'
$response = Join-Path $PSScriptRoot 'response.json'
try {
    Start-Process -FilePath $Launcher -WorkingDirectory (Split-Path $Launcher) `
        -ArgumentList @('--update-to-version', $Target, '--response-file', "`"$response`"") -WindowStyle Normal | Out-Null
    $deadline = [DateTime]::UtcNow.AddMinutes(30)
    $result = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $response) {
            try { $result = Get-Content -LiteralPath $response -Raw | ConvertFrom-Json } catch { $result = $null }
            if ($null -ne $result) { break }
        }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $result) { throw '启动器未在时限内返回更新结果，请查看启动器。' }
    if ($result.updated -ne $true -or $result.version -ne $Target -or $result.error) {
        throw '启动器未确认更新成功，请查看启动器中的错误信息。'
    }
    $env:PYAPPIFY_APP_VERSION = $Target
    $env:PYAPPIFY_APP_STARTING_VERSION = $Previous
    $env:PYAPPIFY_EXECUTABLE = $Launcher
    $env:PYAPPIFY_UPDATE_NOTE = ''
    $runningLauncher = Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($Launcher)) `
        -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Launcher } | Select-Object -First 1
    if ($runningLauncher) { $env:PYAPPIFY_PID = [string]$runningLauncher.Id }
    $python = Join-Path (Split-Path $Working) 'python/pythonw.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '更新完成，但找不到 Python，请在启动器中重新启动客户端。' }
    Start-Process -FilePath $python -WorkingDirectory $Working `
        -ArgumentList @("`"$(Join-Path $Working 'main.py')`"") -WindowStyle Hidden | Out-Null
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'ok-bd2 更新', 'OK', 'Error') | Out-Null
} finally {
    Remove-Item -LiteralPath $response -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSScriptRoot -ErrorAction SilentlyContinue
}
