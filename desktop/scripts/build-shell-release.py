"""Build a deterministic shell ZIP and the core's pinned download manifest."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.desktop.backups import file_hash, regular_tree  # noqa: E402
from src.desktop.bootstrap import REQUIRED_SHELL_VERSION, check_shell  # noqa: E402
from src.desktop.shell_install import install_shell  # noqa: E402


def source_hash():
    digest = hashlib.sha256()
    shell = ROOT / "desktop/shell"
    files = [path for folder in ("src", "static", "scripts")
             for path in regular_tree(shell / folder)]
    files += [shell / name for name in ("package.json", "package-lock.json", "tsconfig.json")]
    files += regular_tree(ROOT / "icons")
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(bytes.fromhex(file_hash(path)))
    return digest.hexdigest()


def prepare_core_payload(root=ROOT):
    import re

    requirements = (root / "requirements.txt").read_text("utf-8")
    forbidden = (r"(?im)^\s*(pyside\d*|pyqt\d*|shiboken\d*|"
                 r"pyside\d*-fluent-widgets|pyqt-fluent-widgets|qtpy)\b")
    if re.search(forbidden, requirements):
        raise ValueError("The Electron payload cannot include Qt dependencies")
    framework = root / "ok"
    regular_tree(framework)
    for path in (framework / "gui", framework / "ui/qt"):
        if path.is_dir():
            shutil.rmtree(path)
    for path in regular_tree(framework):
        if re.match(r"(?i)(qt[56].*\.dll|pyside\d*|shiboken\d*)", path.name):
            raise ValueError("The Electron payload contains a Qt runtime file")


def api(url, token, data=None, *, missing=False):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
        "Accept": ("application/vnd.cnb.api+json" if "api.cnb.cool/" in url
                   else "application/vnd.github+json"),
        "Content-Type": "application/json", "User-Agent": "ok-bd2-release"},
        data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        if missing and error.code == 404:
            return None
        raise RuntimeError(f"Release API failed (HTTP {error.code})") from None


def download(url, target):
    with urllib.request.urlopen(url, timeout=30) as source, Path(target).open("wb") as output:
        shutil.copyfileobj(source, output)


def verify_urls(manifest):
    with tempfile.TemporaryDirectory() as temporary:
        for url in manifest["urls"]:
            target = Path(temporary) / "shell.zip"
            download(url, target)
            if file_hash(target) != manifest["sha256"]:
                raise ValueError("Published shell download failed SHA256 verification")


def publish(archive, manifest, repository):
    github_token, cnb_token = os.environ["GH_TOKEN"], os.environ["CNB_GH"]
    tag = "desktop-shell-" + manifest["version"]
    github_api = f"https://api.github.com/repos/{repository}/releases"
    release = api(f"{github_api}/tags/{tag}", github_token, missing=True)
    if release:
        assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
        metadata_url = assets.get("shell-release.json") or assets["shell-build.json"]
        with urllib.request.urlopen(metadata_url, timeout=30) as response:
            published = json.load(response)
        if published.get("source_sha256") != manifest["source_sha256"]:
            raise ValueError("Shell sources changed: bump package.json and REQUIRED_SHELL_VERSION")
        download(assets[archive.name], archive)
        if file_hash(archive) != published["sha256"]:
            raise ValueError("Released shell archive does not match its manifest")
        manifest["sha256"] = published["sha256"]
        if "shell-release.json" in assets:
            manifest.update(published)
            verify_urls(manifest)
            return manifest
    else:
        # A component tag must not point at the application release's sync commit.
        parent = subprocess.check_output(["git", "rev-parse", "HEAD^"], cwd=ROOT, text=True).strip()
        release = api(github_api, github_token, {"tag_name": tag, "target_commitish": parent,
            "name": tag, "prerelease": True, "make_latest": "false"})
        build_manifest = archive.parent / "shell-build.json"
        build_manifest.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
        subprocess.run(["gh", "release", "upload", tag, str(archive), str(build_manifest),
                        "--repo", repository], check=True)

    cnb_repo = "GodRaymond233/ok-bd2-update"
    cnb_api = f"https://api.cnb.cool/{cnb_repo}/-/releases"
    application_tags = subprocess.check_output(
        ["git", "tag", "--list", "v*", "--sort=-version:refname"], cwd=ROOT, text=True).splitlines()
    cnb_tag = next(item for item in application_tags if item != os.environ.get("GITHUB_REF_NAME"))
    domestic = api(f"{cnb_api}/tags/{cnb_tag}", cnb_token, missing=True)
    if domestic is None:
        domestic = api(cnb_api, cnb_token, {"tag_name": cnb_tag, "target_commitish": cnb_tag,
            "name": cnb_tag, "prerelease": True, "make_latest": "false"})
    assets = {asset["name"]: asset for asset in (domestic.get("assets") or [])}
    if archive.name not in assets:
        upload = api(f"{cnb_api}/{domestic['id']}/asset-upload-url", cnb_token,
                     {"asset_name": archive.name, "size": archive.stat().st_size,
                      "overwrite": False, "ttl": 0})
        # The signed storage URL must never receive the CNB bearer token.
        try:
            request = urllib.request.Request(upload["upload_url"],
                                             data=archive.read_bytes(), method="PUT")
            with urllib.request.urlopen(request, timeout=300):
                pass
        except OSError:
            raise RuntimeError("CNB release storage upload failed") from None
        verify_url = upload["verify_url"]
        if verify_url.startswith("/"):
            verify_url = "https://api.cnb.cool" + verify_url
        if not verify_url.startswith(f"{cnb_api}/{domestic['id']}/asset-upload-confirmation/"):
            raise ValueError("Unexpected CNB upload confirmation endpoint")
        api(verify_url, cnb_token, {})
        domestic = api(f"{cnb_api}/tags/{cnb_tag}", cnb_token)
        assets = {asset["name"]: asset for asset in domestic["assets"]}
    manifest["urls"] = [assets[archive.name]["browser_download_url"],
                        f"https://github.com/{repository}/releases/download/{tag}/{archive.name}"]
    verify_urls(manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--prepare-core-payload", action="store_true")
    parser.add_argument("--core-root", type=Path, default=ROOT)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--offline-app-base", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--repository", default=os.environ.get(
        "GITHUB_REPOSITORY", "GodRaymond233/ok-bd2"))
    arguments = parser.parse_args()
    if arguments.prepare_core_payload:
        prepare_core_payload(arguments.core_root)
        return
    if arguments.output is None or arguments.manifest is None:
        parser.error("--output and --manifest are required when building a shell")
    if not arguments.publish and arguments.local == bool(arguments.url):
        parser.error("choose --local for a local test, or pass public --url values")
    if any(not url.startswith("https://") for url in arguments.url):
        parser.error("public shell URLs must use HTTPS")
    source = ROOT / "desktop/shell/release/win-unpacked"
    check_shell(source)
    arguments.output.mkdir(parents=True, exist_ok=True)
    archive = arguments.output.resolve() / f"ok-bd2-shell-{REQUIRED_SHELL_VERSION}-win-x64.zip"
    temporary = archive.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for path in regular_tree(source):
                info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                with target.open(info, "w") as output, path.open("rb") as data:
                    while chunk := data.read(1024 * 1024):
                        output.write(chunk)
        if archive.exists():
            if file_hash(archive) != file_hash(temporary):
                raise ValueError(
                    "An existing shell version has different contents; bump its version"
                )
        else:
            temporary.rename(archive)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "version": REQUIRED_SHELL_VERSION,
        "sha256": file_hash(archive),
        "source_sha256": source_hash(),
        "urls": [archive.as_uri()] if arguments.local else arguments.url,
    }
    if arguments.publish:
        publish(archive, manifest, arguments.repository)
    arguments.manifest.parent.mkdir(parents=True, exist_ok=True)
    arguments.manifest.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    if arguments.publish:
        published_manifest = arguments.output / "shell-release.json"
        published_manifest.write_bytes(arguments.manifest.read_bytes())
        existing = api(f"https://api.github.com/repos/{arguments.repository}/releases/tags/desktop-shell-{REQUIRED_SHELL_VERSION}",
                       os.environ["GH_TOKEN"])
        if not any(item["name"] == "shell-release.json" for item in existing["assets"]):
            subprocess.run(["gh", "release", "upload", "desktop-shell-" + REQUIRED_SHELL_VERSION,
                            str(published_manifest), "--repo", arguments.repository], check=True)
        tags = subprocess.check_output(["git", "tag", "--points-at", "HEAD"], cwd=ROOT, text=True)
        if any(tag.startswith("desktop-shell-") for tag in tags.splitlines()):
            raise ValueError("Component tags must not point at the application sync commit")
    if arguments.offline_app_base:
        with tempfile.TemporaryDirectory(dir=arguments.output) as directory:
            local_manifest = Path(directory) / "shell-release.json"
            local_manifest.write_text(json.dumps(dict(manifest, urls=[archive.as_uri()])), "utf-8")
            install_shell(
                arguments.offline_app_base / "shell" / REQUIRED_SHELL_VERSION,
                REQUIRED_SHELL_VERSION, check_shell, release_file=local_manifest,
            )
    print(
        json.dumps(
            {
                "archive": str(archive),
                "sha256": manifest["sha256"],
                "bytes": archive.stat().st_size,
                "manifest": str(arguments.manifest),
            }
        )
    )


if __name__ == "__main__":
    main()
