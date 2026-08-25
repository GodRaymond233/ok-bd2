from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROHIBITED_IDENTITY = re.compile(r"(?:\bclaude\b|@anthropic\.com\b)", re.IGNORECASE)
COAUTHOR_TRAILER = re.compile(r"^\s*co-authored-by:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _message_violations(message: str) -> list[str]:
    return [
        trailer.group(0).strip()
        for trailer in COAUTHOR_TRAILER.finditer(message)
        if PROHIBITED_IDENTITY.search(trailer.group(1))
    ]


def _check_pending_commit(message_file: Path) -> list[str]:
    violations = _message_violations(message_file.read_text(encoding="utf-8"))
    for identity_kind in ("AUTHOR", "COMMITTER"):
        result = subprocess.run(
            ["git", "var", f"GIT_{identity_kind}_IDENT"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        identity = result.stdout.strip()
        if PROHIBITED_IDENTITY.search(identity):
            violations.append(f"{identity_kind.title()}: {identity}")
    return violations


def _check_history(revision: str) -> list[str]:
    violations: list[str] = []
    for commit in _git("rev-list", revision).splitlines():
        record = _git(
            "show",
            "-s",
            "--format=%an <%ae>%n%cn <%ce>%n%B",
            commit,
        )
        lines = record.splitlines()
        identities = lines[:2]
        message = "\n".join(lines[2:])
        reasons = [identity for identity in identities if PROHIBITED_IDENTITY.search(identity)]
        reasons.extend(_message_violations(message))
        for reason in reasons:
            violations.append(f"{commit}: {reason}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject Claude or Anthropic commit attribution.",
    )
    parser.add_argument("--message-file", type=Path)
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()

    if args.message_file is not None:
        violations = _check_history("HEAD")
        violations.extend(_check_pending_commit(args.message_file))
    else:
        violations = _check_history(args.revision)

    if not violations:
        print("Commit attribution check passed.")
        return 0

    print("Prohibited commit attribution found:", file=sys.stderr)
    for violation in violations:
        print(f"- {violation}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
