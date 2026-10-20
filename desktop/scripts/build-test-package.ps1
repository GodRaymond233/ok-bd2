# Assembles the stage 1 desktop-client test package (PyAppify launcher + local update repo).
#
# The package never talks to the official update sources: the pyappify.yml next to the
# launcher and the one inside the local update repo both point git_url at
# <Output>\update-repo.git. The update repo is built like the release one: the deploy.txt
# set, ok-script and pyappify inlined from the worktree venv, the version set to the tag.
# Run desktop\scripts\windows-checks.ps1 first so the UI bundle and the packaged shell exist.
#
#   New package:  build-test-package.ps1 -Output D:\ok-bd2-desktop-test
#   U10 update:   build-test-package.ps1 -Output D:\ok-bd2-desktop-test -AddUpdateTag
param(
    [string]$Output = 'D:\ok-bd2-desktop-test',
    [string]$LauncherZip = '',
    [ValidateSet('China', 'Global')]
    [string]$AppProfile = 'China',
    [string]$ConfigCopy = '',
    [string]$Tag = 'v0.9.0',
    [switch]$AddUpdateTag,
    [string]$UpdateTag = 'v0.9.1',
    [switch]$SkipSetup
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$launcherUrl = 'https://github.com/GodRaymond233/ok-bd2/releases/download/v1.3.19/ok-bd2-win32.zip'
# Pinned PyAppify v1.2.3 launcher from the v1.3.19 release: the zip and the ok-bd2.exe in it.
$launcherZipSha256 = '8fd5997b52f5e2bb1472d8c99c53d8f39d76efff8a763444464ad399ef52954c'
$launcherSha256 = '9b2acdb0d78122ca8919310ec9c6b57f323d9b83f7c086efc4a72624dcef576a'
if (-not [IO.Path]::IsPathRooted($Output)) {
    $Output = Join-Path (Get-Location).Path $Output
}
$repoPath = Join-Path $Output 'update-repo.git'
$repoUrl = 'file:///' + ($repoPath -replace '\\', '/')
$appBase = Join-Path $Output 'data\apps\ok-bd2'
$identity = @('-c', 'user.name=ok-bd2 test package', '-c', 'user.email=test-package@localhost')

function Invoke-Native {
    param([string]$File, [string[]]$Arguments, [string]$WorkingDirectory = $root)
    Push-Location $WorkingDirectory
    try {
        & $File @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$File $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-SourceLabel {
    $head = (& git -C $root rev-parse --short HEAD).Trim()
    $changes = @(& git -C $root status --porcelain)
    if ($changes.Count -gt 0) {
        return "$head with $($changes.Count) uncommitted change(s)"
    }
    return $head
}

function Write-TestYml {
    param([string]$Destination)
    $text = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'pyappify.yml')
    $text = $text -replace 'git_url:\s*"[^"]*"', "git_url: `"$repoUrl`""
    $text = $text -replace 'main_script:\s*"main\.py"', 'main_script: "main_desktop.py"'
    if ($text -notmatch [regex]::Escape($repoUrl) -or $text -match 'cnb\.cool|github\.com' -or
        $text -notmatch 'main_desktop\.py') {
        throw 'Could not point every profile at the local update repo and main_desktop.py.'
    }
    [IO.File]::WriteAllText($Destination, $text, (New-Object Text.UTF8Encoding($false)))
}

# Fills $Destination (empty apart from .git) the way the release workflow fills the update
# repo: deploy.txt entries, then ok.update.inline_ok_requirements --tag <tag>.
function Write-UpdateTree {
    param([string]$Destination, [string]$Version)
    $entries = Get-Content -Encoding UTF8 (Join-Path $root 'deploy.txt') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') }
    foreach ($entry in $entries) {
        $source = Join-Path $root $entry
        if (-not (Test-Path -LiteralPath $source)) {
            Write-Host "deploy entry not in the source tree (added by inlining or by CI): $entry"
            continue
        }
        $target = Join-Path $Destination $entry
        New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
        Copy-Item -Recurse -Force -LiteralPath $source -Destination $target
    }
    Get-ChildItem -Recurse -Directory -Filter '__pycache__' $Destination |
        Where-Object { $_.FullName -notlike '*\.git\*' } | Remove-Item -Recurse -Force
    Copy-Item (Join-Path $root 'deploy.txt') (Join-Path $Destination 'deploy.txt')
    Invoke-Native $python @('-m', 'ok.update.inline_ok_requirements', '--tag', $Version) $Destination
    Remove-Item -Force (Join-Path $Destination 'deploy.txt')
    foreach ($inlined in @('ok', 'pyappify')) {
        if (-not (Test-Path (Join-Path $Destination $inlined))) {
            throw "Inlining did not produce the $inlined folder."
        }
    }
    if (Select-String -Quiet -Pattern '^\s*(ok-script|pyappify)\b' (Join-Path $Destination 'requirements.txt')) {
        throw 'requirements.txt still lists an inlined package.'
    }
    if (-not (Test-Path (Join-Path $Destination 'src\desktop\ui_dist\index.html'))) {
        throw 'The update tree has no UI bundle (src\desktop\ui_dist).'
    }
    Copy-Item -Force (Join-Path $root '.update_repo_gitignore') (Join-Path $Destination '.gitignore')
    Write-TestYml (Join-Path $Destination 'pyappify.yml')
}

if (-not (Test-Path $python)) {
    throw "Missing $python (the worktree venv inlines ok-script). Run windows-checks.ps1 -CreateVenv first."
}
if (-not (Test-Path (Join-Path $root 'src\desktop\ui_dist\index.html'))) {
    throw 'Missing src\desktop\ui_dist. Run desktop\scripts\windows-checks.ps1 first.'
}

if ($AddUpdateTag) {
    if (-not (Test-Path $repoPath)) {
        throw "No local update repo at $repoPath. Build the package first."
    }
    if (& git -C $repoPath tag --list $UpdateTag) {
        throw "$repoPath already has $UpdateTag. Pass another -UpdateTag."
    }
    $work = Join-Path $Output 'update-staging'
    if (Test-Path $work) {
        throw "$work is left over from an earlier run; remove it first."
    }
    Invoke-Native git @('clone', '--quiet', $repoPath, $work)
    Get-ChildItem -Force $work | Where-Object { $_.Name -ne '.git' } | Remove-Item -Recurse -Force
    Write-UpdateTree -Destination $work -Version $UpdateTag
    Invoke-Native git @('-c', 'core.autocrlf=false', 'add', '--all') $work
    Invoke-Native git ($identity + @('commit', '--quiet', '--allow-empty', '-m', "test package update $UpdateTag from $(Get-SourceLabel)")) $work
    Invoke-Native git @('tag', $UpdateTag) $work
    Invoke-Native git @('push', '--quiet', 'origin', 'HEAD', $UpdateTag) $work
    Remove-Item -Recurse -Force $work
    Write-Host "Added $UpdateTag to $repoPath. Follow U10 in test-package-notes.md to update."
    exit 0
}

if (Test-Path $Output) {
    throw "$Output already exists. Choose another -Output or remove it yourself."
}
$shellBuild = Join-Path $root 'desktop\shell\release\win-unpacked'
$manifestFile = Join-Path $shellBuild 'shell-manifest.json'
foreach ($required in @((Join-Path $shellBuild 'ok-bd2-desktop.exe'), $manifestFile)) {
    if (-not (Test-Path $required)) {
        throw "Missing $required. Run desktop\scripts\windows-checks.ps1 first."
    }
}
$manifest = Get-Content -Raw -Encoding UTF8 $manifestFile | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $Output | Out-Null
Write-Host "Building the test package in $Output (remove the folder if a step fails)."

# 1. Launcher (pinned PyAppify build from the v1.3.19 release).
$download = Join-Path $Output 'launcher-download'
New-Item -ItemType Directory -Force -Path $download | Out-Null
if (-not $LauncherZip) {
    $LauncherZip = Join-Path $download 'ok-bd2-win32.zip'
    Invoke-WebRequest -UseBasicParsing -Uri $launcherUrl -OutFile $LauncherZip
}
if ((Get-Sha256 $LauncherZip) -ne $launcherZipSha256) {
    throw "$LauncherZip is not the pinned v1.3.19 launcher zip ($launcherZipSha256)."
}
Expand-Archive -LiteralPath $LauncherZip -DestinationPath (Join-Path $download 'zip') -Force
$launcher = Get-ChildItem -Recurse -Filter 'ok-bd2.exe' (Join-Path $download 'zip') | Select-Object -First 1
if (-not $launcher) {
    throw 'ok-bd2.exe not found in the launcher zip.'
}
$hash = Get-Sha256 $launcher.FullName
if ($hash -ne $launcherSha256) {
    throw "Launcher hash $hash does not match the pinned $launcherSha256."
}
Copy-Item $launcher.FullName (Join-Path $Output 'ok-bd2.exe')
Remove-Item -Recurse -Force $download

# 2. Local update repository.
$staging = Join-Path $Output 'update-staging'
New-Item -ItemType Directory -Force -Path $staging | Out-Null
Invoke-Native git @('init', '--quiet') $staging
Write-UpdateTree -Destination $staging -Version $Tag
Invoke-Native git @('-c', 'core.autocrlf=false', 'add', '--all') $staging
Invoke-Native git ($identity + @('commit', '--quiet', '-m', "test package $Tag from $(Get-SourceLabel)")) $staging
Invoke-Native git @('tag', $Tag) $staging
Invoke-Native git @('clone', '--quiet', '--bare', $staging, $repoPath)
Remove-Item -Recurse -Force $staging

# 3. Launcher configuration next to the executable (read before the embedded copy).
Write-TestYml (Join-Path $Output 'pyappify.yml')

# 4. Desktop shell, outside the working directory so code syncs never touch it.
$shellTarget = Join-Path $appBase "shell\$($manifest.version)"
New-Item -ItemType Directory -Force -Path $shellTarget | Out-Null
Copy-Item -Recurse -Force (Join-Path $shellBuild '*') $shellTarget

# 5. First-run setup: clone from the local repo, install Python and the locked requirements.
if (-not $SkipSetup) {
    Write-Host 'Running the launcher setup (UAC prompt; downloads Python and the requirements)...'
    $setup = Start-Process -FilePath (Join-Path $Output 'ok-bd2.exe') -ArgumentList @('-c', 'setup', '-p', $AppProfile) -WorkingDirectory $Output -Wait -PassThru
    Write-Host "setup exit code: $($setup.ExitCode)"
    $working = Join-Path $appBase 'working'
    if ($setup.ExitCode -ne 0 -or -not (Test-Path (Join-Path $working 'main_desktop.py'))) {
        throw "Setup did not finish (exit $($setup.ExitCode)); see $Output\data\logs."
    }
    $remote = (& git -C (Join-Path $appBase 'repo') remote get-url origin)
    if ($remote -ne $repoUrl) {
        throw "The installed repo points at $remote instead of the local test repo."
    }
    if ($ConfigCopy) {
        $configs = Join-Path $working 'configs'
        New-Item -ItemType Directory -Force -Path $configs | Out-Null
        Copy-Item -Recurse -Force (Join-Path $ConfigCopy '*') $configs
        Write-Host "Seeded working\configs from the copy at $ConfigCopy"
    }
}

Copy-Item (Join-Path $PSScriptRoot 'test-package-notes.md') (Join-Path $Output 'test-package-notes.md')
Write-Host "Test package ready: $Output"
Write-Host "  launcher : $Output\ok-bd2.exe (sha256 $launcherSha256)"
Write-Host "  updates  : $repoUrl (tag $Tag)"
Write-Host "  shell    : $shellTarget"
Write-Host "  notes    : $Output\test-package-notes.md"
