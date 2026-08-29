param(
    [Parameter(Mandatory = $true)]
    [string]$StartTag,
    [Parameter(Mandatory = $true)]
    [string]$EndTag,
    [Parameter(Mandatory = $true)]
    [string]$Changelog,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseTag,
    [string]$OutputPath = "release-notes.md"
)

$ErrorActionPreference = "Stop"

$releaseCommit = git rev-list -n 1 $ReleaseTag
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($releaseCommit)) {
    throw "Could not resolve release tag $ReleaseTag."
}
$releaseSubject = git log -1 --format=%s $releaseCommit
$expectedSubject = "release: $ReleaseTag"
if ($releaseSubject -ne $expectedSubject) {
    throw "Tag $ReleaseTag must point to '$expectedSubject', got '$releaseSubject'."
}

$releaseAuthor = git log -1 --format=%an $releaseCommit
$mainEntries = [System.Collections.Generic.List[string]]::new()
$releaseDetails = @(git log -1 --format=%b $releaseCommit)
$nonEmptyDetails = @(
    foreach ($line in $releaseDetails) {
        $entry = ($line -replace '^\s*[-*]\s+', '').Trim()
        if (-not [string]::IsNullOrWhiteSpace($entry)) {
            $entry
        }
    }
)
$allConventional = $nonEmptyDetails.Count -gt 0
foreach ($entry in $nonEmptyDetails) {
    if ($entry -notmatch '^(feat|fix|refactor|perf|docs|test|build|ci|chore|style|revert)(\([^)]+\))?:\s+.+$') {
        $allConventional = $false
        break
    }
}

if (-not $allConventional -and $nonEmptyDetails.Count -gt 0) {
    $freeformEntry = ($nonEmptyDetails -join ' ') -replace '\s+', ' '
    $mainEntries.Add("- $freeformEntry ($releaseAuthor)")
} else {
    foreach ($entry in $nonEmptyDetails) {
        if ([string]::IsNullOrWhiteSpace($entry)) {
            continue
        }
        if ($entry -notmatch '^(feat|fix|refactor|perf|docs|test|build|ci|chore|style|revert)(\([^)]+\))?:\s+.+$') {
            throw "Release detail must use Conventional Commits format: $entry"
        }
        if ($entry -notmatch '\s+\([^)]+\)$') {
            $entry = "$entry ($releaseAuthor)"
        }
        $mainEntries.Add("- $entry")
    }
}
if ($mainEntries.Count -eq 0) {
    throw "Release commit $releaseCommit has no version details."
}

$normalizedChangelog = $Changelog.Trim()
if ([string]::IsNullOrWhiteSpace($normalizedChangelog)) {
    throw "The synchronized changelog is empty."
}

$releaseNotes = @(
    "### 更新日志 $StartTag -> $EndTag"
    ""
    $normalizedChangelog
    ""
    "### 版本主要内容 ${ReleaseTag}："
    ""
    ($mainEntries -join "`n")
    ""
    "### 下载包说明"
    ""
    "- [ok-bd2-win32-China-setup.exe](https://github.com/GodRaymond233/ok-bd2/releases/download/$ReleaseTag/ok-bd2-win32-China-setup.exe) 完整安装包，更新使用 CNB。"
    "- [ok-bd2-win32-online-setup.exe](https://github.com/GodRaymond233/ok-bd2/releases/download/$ReleaseTag/ok-bd2-win32-online-setup.exe) 在线安装包，首次安装时需要联网下载依赖。"
    "- [ok-bd2-win32-Global-setup.exe](https://github.com/GodRaymond233/ok-bd2/releases/download/$ReleaseTag/ok-bd2-win32-Global-setup.exe) full package with dependencies, using GitHub and PyPI as update source."
    "- 不要下载 ok-bd2-win32.zip 或 Source code 压缩包。"
) -join "`n"

$workspace = (Resolve-Path -LiteralPath ".").Path
$absoluteOutput = [System.IO.Path]::GetFullPath((Join-Path $workspace $OutputPath))
$relativeOutput = [System.IO.Path]::GetRelativePath($workspace, $absoluteOutput)
if ([System.IO.Path]::IsPathRooted($relativeOutput) -or
    $relativeOutput -eq ".." -or
    $relativeOutput.StartsWith("..\") -or
    $relativeOutput.StartsWith("../")) {
    throw "Refusing to write release notes outside the workspace: $absoluteOutput"
}
Set-Content -LiteralPath $absoluteOutput -Value $releaseNotes -Encoding utf8
Get-Content -LiteralPath $absoluteOutput
