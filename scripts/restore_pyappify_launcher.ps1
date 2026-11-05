param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseTag,
    [string]$BuildDir = ""
)

$ErrorActionPreference = "Stop"

function Assert-UnderWorkspace {
    param(
        [string]$PathToCheck,
        [string]$Workspace
    )

    $resolvedWorkspace = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Workspace).Path)
    $resolvedWorkspace = $resolvedWorkspace.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $resolvedPath = if ([System.IO.Path]::IsPathRooted($PathToCheck)) {
        [System.IO.Path]::GetFullPath($PathToCheck)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $resolvedWorkspace $PathToCheck))
    }
    $workspacePrefix = $resolvedWorkspace + [System.IO.Path]::DirectorySeparatorChar
    if (
        [string]::Equals($resolvedPath, $resolvedWorkspace, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $resolvedPath.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing to write outside workspace: $PathToCheck"
    }
    return $resolvedPath
}

if ([string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
    throw "GH_TOKEN is required to restore a launcher from a GitHub Release."
}
if ([string]::IsNullOrWhiteSpace($env:GITHUB_REPOSITORY)) {
    throw "GITHUB_REPOSITORY is required to restore a launcher."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required to restore a launcher."
}

$workspace = (Resolve-Path -LiteralPath ".").Path
$buildRoot = $workspace
if ([string]::IsNullOrWhiteSpace($BuildDir)) {
    if (-not [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
        $buildRoot = (Resolve-Path -LiteralPath $env:RUNNER_TEMP).Path
    }
    $BuildDir = "p"
}
$buildPath = Assert-UnderWorkspace -PathToCheck $BuildDir -Workspace $buildRoot
$configPath = Join-Path $workspace "pyappify.yml"
$configText = Get-Content -LiteralPath $configPath -Raw
$appNameMatch = [regex]::Match($configText, '(?m)^\s*name:\s*["'']?([^"''\r\n]+)["'']?\s*$')
if (-not $appNameMatch.Success) {
    throw "Could not read application name from $configPath"
}
$appName = $appNameMatch.Groups[1].Value.Trim()
$assetName = "$appName-win32.zip"

$runnerTemp = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    [System.IO.Path]::GetTempPath()
} else {
    $env:RUNNER_TEMP
}
$resolvedRunnerTemp = (Resolve-Path -LiteralPath $runnerTemp).Path
$downloadRoot = Join-Path $resolvedRunnerTemp "pyappify-launcher-$([guid]::NewGuid().ToString('N'))"
$extractRoot = Join-Path $downloadRoot "extract"

try {
    New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
    gh release download $ReleaseTag `
        --repo $env:GITHUB_REPOSITORY `
        --pattern $assetName `
        --dir $downloadRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download $assetName from release $ReleaseTag."
    }

    $archivePath = Join-Path $downloadRoot $assetName
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot -Force
    $launcherSource = Join-Path $extractRoot "$appName\$appName.exe"
    if (-not (Test-Path -LiteralPath $launcherSource)) {
        throw "Launcher was not found in $assetName at $launcherSource"
    }
    $versionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($launcherSource)
    $launcherVersion = [version]::new(
        $versionInfo.FileMajorPart, $versionInfo.FileMinorPart,
        $versionInfo.FileBuildPart, $versionInfo.FilePrivatePart
    )
    if ($launcherVersion -lt [version]"1.2.4.0") {
        throw "Launcher $launcherVersion cannot prepare an exact application tag; rebuild with PyAppify 1.2.4 or newer."
    }

    $launcherTarget = Join-Path $buildPath "src-tauri\target\release\$appName.exe"
    New-Item -ItemType Directory -Path (Split-Path -Parent $launcherTarget) -Force | Out-Null
    Copy-Item -LiteralPath $launcherSource -Destination $launcherTarget -Force
    Write-Host "Restored launcher from $ReleaseTag to $launcherTarget"
} finally {
    $resolvedDownloadRoot = [System.IO.Path]::GetFullPath($downloadRoot)
    if ($resolvedDownloadRoot.StartsWith(
        $resolvedRunnerTemp,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -and (Test-Path -LiteralPath $resolvedDownloadRoot)) {
        Remove-Item -LiteralPath $resolvedDownloadRoot -Recurse -Force
    }
}
