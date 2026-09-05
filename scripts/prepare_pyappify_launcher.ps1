param(
    [string]$Version = "v1.2.3",
    [string]$BuildDir = "pyappify_build",
    [ValidateSet("zlib", "lzma")]
    [string]$NsisCompression = "lzma",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if ((Test-Path -LiteralPath $cargoBin) -and ($env:PATH -notlike "*$cargoBin*")) {
    $env:PATH = "$cargoBin;$env:PATH"
}

function Ensure-Command {
    param(
        [string]$Name,
        [string]$InstallDescription
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found. $InstallDescription"
    }
}

function Ensure-Pnpm {
    if (Get-Command pnpm -ErrorAction SilentlyContinue) {
        return
    }

    Ensure-Command -Name "npm" -InstallDescription "Install Node.js/npm first."
    npm install -g pnpm
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install pnpm."
    }
    Ensure-Command -Name "pnpm" -InstallDescription "npm installed pnpm, but it is still not available on PATH."
}

function Ensure-Cargo {
    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        return
    }

    $isWindows = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )
    if (-not $isWindows) {
        throw "cargo is required but was not found. Install Rust before running this script."
    }

    $rustupInitPath = Join-Path ([System.IO.Path]::GetTempPath()) "rustup-init.exe"
    Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $rustupInitPath
    & $rustupInitPath -y --no-modify-path --default-toolchain stable
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Rust."
    }

    if ((Test-Path -LiteralPath $cargoBin) -and ($env:PATH -notlike "*$cargoBin*")) {
        $env:PATH = "$cargoBin;$env:PATH"
    }
    Ensure-Command -Name "cargo" -InstallDescription "Rust was installed, but cargo is still not available on PATH."
}

function Update-InstallerNsiTemplate {
    param(
        [string]$BuildDirPath
    )

    # BUG-20260905-06: the upstream pyappify/Tauri NSIS template aborts with a bare
    # "unable to uninstall" message when the previous install's registered
    # uninstaller no longer exists (deleted install dir, moved app, ...), which
    # hard-blocks upgrading users. Patch the template right after cloning so the
    # generated installer tolerates stale uninstall entries. Every patch must hit
    # exactly once or the build fails loudly - a silently skipped patch would ship
    # an unfixed installer.

    $nsiPath = Join-Path $BuildDirPath "src-tauri\nsis\installer.nsi"
    if (-not (Test-Path -LiteralPath $nsiPath)) {
        throw "installer.nsi template not found at $nsiPath"
    }

    # -Encoding UTF8 is required: the template is UTF-8 (it already contains
    # Chinese messages) and Windows PowerShell would otherwise decode it as ANSI.
    $content = (Get-Content -LiteralPath $nsiPath -Raw -Encoding UTF8) -replace "`r`n", "`n"

    $uninstallBranchSearch = @'
    ${Else}
      ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
      ReadRegStr $R1 SHCTX "${UNINSTKEY}" "UninstallString"
      ${IfThen} $UpdateMode = 1 ${|} StrCpy $R1 "$R1 /UPDATE" ${|} ; append /UPDATE
      ${IfThen} $PassiveMode = 1 ${|} StrCpy $R1 "$R1 /P" ${|} ; append /P
      StrCpy $R1 "$R1 _?=$4" ; append uninstall directory
      ExecWait '$R1' $0
    ${EndIf}
'@
    $uninstallBranchReplacement = @'
    ${Else}
      ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
      ReadRegStr $R1 SHCTX "${UNINSTKEY}" "UninstallString"

      ; BUG-20260905-06: tolerate a stale/malformed previous-uninstall entry.
      ; $R3 = UninstallString with surrounding quotes stripped ($R1 keeps the
      ; registered form for the ExecWait command below).
      StrCpy $R3 $R1
      StrCpy $R2 $R3 1
      ${If} $R2 == "$\""
        StrCpy $R3 $R3 "" 1
        StrCpy $R3 $R3 -1
      ${EndIf}

      ; When the previous install dir is unknown, derive it from the registered
      ; uninstaller path instead of appending an empty _?= which makes the old
      ; uninstaller delete nothing.
      ${If} $4 == ""
      ${AndIf} $R3 != ""
        ${GetParent} "$R3" $4
      ${EndIf}

      ; If the registered uninstaller no longer exists, drop the stale keys and
      ; continue installing without uninstalling; ExecWait would otherwise fail
      ; with "unable to uninstall" and hard-block the upgrade.
      ${IfNot} ${FileExists} "$R3"
        DetailPrint "Previous uninstaller not found ($R3); cleaning stale uninstall entries and installing without uninstalling."
        DeleteRegKey SHCTX "${UNINSTKEY}"
        DeleteRegKey SHCTX "${MANUPRODUCTKEY}"
        Goto reinst_uninstall_skipped
      ${EndIf}

      ${IfThen} $UpdateMode = 1 ${|} StrCpy $R1 "$R1 /UPDATE" ${|} ; append /UPDATE
      ${IfThen} $PassiveMode = 1 ${|} StrCpy $R1 "$R1 /P" ${|} ; append /P
      StrCpy $R1 "$R1 _?=$4" ; append uninstall directory
      ExecWait '$R1' $0
    ${EndIf}
'@

    $failureMessageSearch = @'
      ; Other erros? show generic error message and return to select un/reinstall page
      MessageBox MB_ICONEXCLAMATION "$(unableToUninstall)"
      Abort
    ${EndIf}
  reinst_done:
'@
    $failureMessageReplacement = @'
      ; Other erros? show generic error message and return to select un/reinstall page
      ; BUG-20260905-06: actionable guidance instead of the bare unableToUninstall message
      ${If} $LANGUAGE == 2052
        MessageBox MB_ICONEXCLAMATION "无法自动卸载旧版本。$\r$\n$\r$\n请先关闭正在运行的 ${PRODUCTNAME}，点击「上一步」重试；或返回后改选「请勿卸载」直接覆盖安装；也可手动卸载旧版后重新运行本安装包。"
      ${Else}
        MessageBox MB_ICONEXCLAMATION "Unable to uninstall the previous version.$\r$\n$\r$\nClose any running ${PRODUCTNAME}, click Back and retry; or go back, choose not to uninstall and install over it; or uninstall the old version manually and run this installer again."
      ${EndIf}
      Abort
    ${EndIf}

  reinst_uninstall_skipped:
  reinst_done:
'@

    $patches = @(
        @{ Description = "dangling uninstaller guard"; Search = $uninstallBranchSearch; Replacement = $uninstallBranchReplacement },
        @{ Description = "actionable failure message"; Search = $failureMessageSearch; Replacement = $failureMessageReplacement }
    )

    foreach ($patch in $patches) {
        # Normalize line endings on both sides: the .ps1 file may be CRLF while the
        # cloned template is LF, and here-string literals inherit the script's endings.
        $search = $patch.Search -replace "`r`n", "`n"
        $replacement = $patch.Replacement -replace "`r`n", "`n"
        $matchCount = [regex]::Matches($content, [regex]::Escape($search)).Count
        if ($matchCount -ne 1) {
            $firstLine = ($search -split "`n")[0]
            throw "installer.nsi patch '$($patch.Description)' expected exactly 1 match but found $matchCount (content=$($content.Length) chars, search=$($search.Length) chars, firstLineIndex=$($content.IndexOf($firstLine))). The pyappify template changed; update the embedded patch text."
        }
        $content = $content.Replace($search, $replacement)
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($nsiPath, ($content -replace "`n", "`r`n"), $utf8NoBom)
    Write-Host "Patched installer.nsi template for stale uninstaller tolerance (BUG-20260905-06)."
}

function Assert-UnderWorkspace {
    param(
        [string]$PathToCheck,
        [string]$Workspace
    )

    $resolvedWorkspace = (Resolve-Path -LiteralPath $Workspace).Path
    $parent = Split-Path -Parent $PathToCheck
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $resolvedParent = (Resolve-Path -LiteralPath $parent).Path
    if (-not $resolvedParent.StartsWith($resolvedWorkspace, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write outside workspace: $PathToCheck"
    }
}

$workspace = (Resolve-Path -LiteralPath ".").Path
$buildPath = Join-Path $workspace $BuildDir
Assert-UnderWorkspace -PathToCheck $buildPath -Workspace $workspace

$configPath = Join-Path $workspace "pyappify.yml"
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "pyappify.yml not found at $configPath"
}

$configText = Get-Content -LiteralPath $configPath -Raw
$appNameMatch = [regex]::Match($configText, '(?m)^\s*name:\s*["'']?([^"''\r\n]+)["'']?\s*$')
if (-not $appNameMatch.Success) {
    throw "Could not read application name from pyappify.yml"
}
$appName = $appNameMatch.Groups[1].Value.Trim()
$requiresUac = [regex]::IsMatch($configText, '(?m)^\s*uac:\s*true\s*$')

Ensure-Pnpm
Ensure-Cargo

if (Test-Path -LiteralPath $buildPath) {
    Remove-Item -LiteralPath $buildPath -Recurse -Force
}

git clone https://github.com/ok-oldking/pyappify.git $buildPath
git -C $buildPath checkout "tags/$Version"

$assetConfigPath = Join-Path $buildPath "src-tauri\assets\pyappify.yml"
Copy-Item -LiteralPath $configPath -Destination $assetConfigPath -Force

$iconsSource = Join-Path $workspace "icons"
if (Test-Path -LiteralPath $iconsSource) {
    $iconsTarget = Join-Path $buildPath "src-tauri\icons"
    if (Test-Path -LiteralPath $iconsTarget) {
        Remove-Item -LiteralPath $iconsTarget -Recurse -Force
    }
    Copy-Item -LiteralPath $iconsSource -Destination $iconsTarget -Recurse -Force
}

$tauriConfPath = Join-Path $buildPath "src-tauri\tauri.conf.json"
$tauriConf = Get-Content -LiteralPath $tauriConfPath -Raw
$tauriConf = $tauriConf.Replace('"pyappify"', '"' + $appName + '"')
$tauriConf = $tauriConf.Replace('"0.0.1"', '"' + $Version.TrimStart("v") + '"')
$tauriConfObject = $tauriConf | ConvertFrom-Json
$tauriConfObject.bundle.windows.nsis | Add-Member `
    -MemberType NoteProperty `
    -Name compression `
    -Value $NsisCompression `
    -Force
$tauriConf = $tauriConfObject | ConvertTo-Json -Depth 100
Set-Content -LiteralPath $tauriConfPath -Value $tauriConf -Encoding UTF8

$cargoTomlPath = Join-Path $buildPath "src-tauri\Cargo.toml"
$cargoToml = Get-Content -LiteralPath $cargoTomlPath -Raw
$cargoToml = $cargoToml.Replace('name = "pyappify"', 'name = "' + $appName + '"')
Set-Content -LiteralPath $cargoTomlPath -Value $cargoToml -Encoding UTF8

$packageJsonPath = Join-Path $buildPath "package.json"
$packageJson = Get-Content -LiteralPath $packageJsonPath -Raw | ConvertFrom-Json
if (-not $packageJson.pnpm) {
    $packageJson | Add-Member -MemberType NoteProperty -Name pnpm -Value ([pscustomobject]@{})
}
$onlyBuiltDependencies = @()
if ($packageJson.pnpm.onlyBuiltDependencies) {
    $onlyBuiltDependencies = @($packageJson.pnpm.onlyBuiltDependencies)
}
if ($onlyBuiltDependencies -notcontains "esbuild") {
    $onlyBuiltDependencies += "esbuild"
}
$packageJson.pnpm | Add-Member -MemberType NoteProperty -Name onlyBuiltDependencies -Value @($onlyBuiltDependencies | Sort-Object -Unique) -Force
$packageJson | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $packageJsonPath -Encoding UTF8

if ($requiresUac) {
    $buildRsPath = Join-Path $buildPath "src-tauri\build.rs"
    $buildRs = Get-Content -LiteralPath $buildRsPath -Raw
    $buildRs = $buildRs.Replace("const UAC: bool = false;", "const UAC: bool = true;")
    Set-Content -LiteralPath $buildRsPath -Value $buildRs -Encoding UTF8
}

$viteConfigPath = Join-Path $buildPath "vite.config.ts"
$viteConfig = Get-Content -LiteralPath $viteConfigPath -Raw
if ($viteConfig -notmatch 'base:\s*["'']\./["'']') {
    $viteConfig = $viteConfig -replace 'plugins:\s*\[react\(\)\],', "plugins: [react()],`r`n  base: `"./`","
    Set-Content -LiteralPath $viteConfigPath -Value $viteConfig -Encoding UTF8
}

$appServicePath = Join-Path $buildPath "src-tauri\src\app_service.rs"
$appService = Get-Content -LiteralPath $appServicePath -Raw
$appService = $appService.Replace(
    "async fn check_running_on_start(app_name: &str, working_dir: &Path) -> Result<()> {",
    "async fn check_running_on_start(`r`n    app_handle: &AppHandle,`r`n    app_name: &str,`r`n    working_dir: &Path,`r`n) -> Result<()> {"
)
$appService = $appService.Replace(
    "            emit_apps().await;`r`n            return Ok(());",
    "            emit_apps().await;`r`n            if let Some(window) = app_handle.get_webview_window(`"main`") {`r`n                if let Err(e) = window.hide() {`r`n                    warn!(`r`n                        `"Failed to hide main window after app '{}' started: {:?}`",`r`n                        app_name, e`r`n                    );`r`n                }`r`n            }`r`n            return Ok(());"
)
$appService = $appService.Replace(
    "    check_running_on_start(&app_name, &working_dir).await?;",
    "    check_running_on_start(&app_handle, &app_name, &working_dir).await?;"
)
Set-Content -LiteralPath $appServicePath -Value $appService -Encoding UTF8

Update-InstallerNsiTemplate -BuildDirPath $buildPath

pnpm install --dir $buildPath
if ($SkipBuild) {
    Write-Host "PyAppify source prepared without compiling the launcher."
} else {
    pnpm --dir $buildPath exec cargo fmt --manifest-path (Join-Path $buildPath "src-tauri\Cargo.toml")
    pnpm --dir $buildPath tauri build
}
