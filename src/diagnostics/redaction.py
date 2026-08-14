from __future__ import annotations

import getpass
import platform
import re
from pathlib import Path

_AUTH_SCHEME_RE = re.compile(
    r"(?i)\b(?:Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]+"
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET_RE = re.compile(
    r"(?i)(?P<quote>[\"']?)(?P<key>[A-Z0-9_-]{0,64}(?:authorization|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|"
    r"private[_-]?key|token|secret|password|passwd|cookie|session))(?P=quote)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URL_QUERY_RE = re.compile(r"(?i)(https?://[^\s?#]+)\?[^\s#]+")
_QUOTED_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?P<quote>[\"'])(?P<path>(?:[A-Z]:[\\/]|\\\\).*?)(?P=quote)"
)
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![A-Z0-9_])(?:[A-Z]:[\\/]|\\\\)[^\s,;\"'<>|]+"
)
_PLACEHOLDER_PATH_RE = re.compile(r"<PATH>(?:[\\/][^\s,;\"'<>|]+)+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRAILING_PATH_PUNCTUATION = ".:)]}"


class DiagnosticRedactor:
    """Remove common credentials and local identity/path details from text."""

    def __init__(self, *, known_roots: list[Path] | None = None):
        roots = [Path.home(), Path.cwd()]
        roots.extend(known_roots or [])
        self._known_roots = sorted(
            {str(path.resolve()) for path in roots if str(path)},
            key=len,
            reverse=True,
        )
        self._known_values = sorted(
            {
                value
                for value in (getpass.getuser(), platform.node())
                if value and len(value) >= 3
            },
            key=len,
            reverse=True,
        )

    def redact(self, value: object) -> str:
        text = _CONTROL_RE.sub("", str(value))
        for root in self._known_roots:
            text = re.sub(re.escape(root), "<PATH>", text, flags=re.IGNORECASE)
            text = re.sub(
                re.escape(root.replace("\\", "/")),
                "<PATH>",
                text,
                flags=re.IGNORECASE,
            )
        text = _PLACEHOLDER_PATH_RE.sub(
            lambda match: _path_placeholder(match.group(0)),
            text,
        )
        for value in self._known_values:
            text = re.sub(re.escape(value), "<LOCAL_ID>", text, flags=re.IGNORECASE)

        text = _AUTH_SCHEME_RE.sub("<REDACTED>", text)
        text = _SECRET_RE.sub(
            lambda match: f"{match.group('quote')}{match.group('key')}"
            f"{match.group('quote')}{match.group('sep')}<REDACTED>",
            text,
        )
        text = _URL_QUERY_RE.sub(r"\1?<REDACTED_QUERY>", text)
        text = _EMAIL_RE.sub("<EMAIL>", text)
        text = _QUOTED_WINDOWS_PATH_RE.sub(
            lambda match: f"{match.group('quote')}{_path_placeholder(match.group('path'))}"
            f"{match.group('quote')}",
            text,
        )
        return _WINDOWS_PATH_RE.sub(
            lambda match: _path_placeholder(match.group(0)),
            text,
        )


def _path_placeholder(raw_path: str) -> str:
    path = raw_path
    trailing = ""
    while path and path[-1] in _TRAILING_PATH_PUNCTUATION:
        trailing = path[-1] + trailing
        path = path[:-1]
    parts = [part for part in re.split(r"[\\/]", path) if part]
    basename = parts[-1] if parts else ""
    safe_name = basename if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", basename) else ""
    suffix = f"/{safe_name}" if safe_name else ""
    return f"<PATH>{suffix}{trailing}"
