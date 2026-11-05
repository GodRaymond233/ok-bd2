"""Build a deterministic shell ZIP and the core's pinned download manifest."""

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.desktop.backups import file_hash, regular_tree  # noqa: E402
from src.desktop.bootstrap import REQUIRED_SHELL_VERSION, check_shell  # noqa: E402
from src.desktop.shell_install import install_shell  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--offline-app-base", type=Path)
    arguments = parser.parse_args()
    if arguments.local == bool(arguments.url):
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
        "urls": [archive.as_uri()] if arguments.local else arguments.url,
    }
    arguments.manifest.parent.mkdir(parents=True, exist_ok=True)
    arguments.manifest.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
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
