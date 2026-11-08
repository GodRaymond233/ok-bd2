"""Install locked requirements, retrying only transient download failures."""

import re
import subprocess
import sys
import time

_PERMANENT_ERROR = re.compile(
    r"ResolutionImpossible|conflicting dependencies|Invalid requirement|"
    r"DO NOT MATCH THE HASHES|Requires-Python|not a supported wheel",
    re.IGNORECASE,
)
_TRANSIENT_ERROR = re.compile(
    r"ConnectionError|ConnectionResetError|Connection refused|Connection reset|"
    r"ReadTimeout|ConnectTimeout|timed out|Temporary failure in name resolution|"
    r"getaddrinfo failed|NameResolutionError|RemoteDisconnected|"
    r"(?:HTTP(?: error)?|status(?: code)?)[ :]+(?:429|500|502|503|504)\b",
    re.IGNORECASE,
)


def install_requirements(requirements, *, attempts=3, retry_delay=15):
    command = [
        sys.executable, "-m", "pip", "install", "--no-cache-dir",
        "--disable-pip-version-check", "--retries", "0", "--timeout", "30",
        "-r", str(requirements),
    ]
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("Dependency installation exceeded 15 minutes.", file=sys.stderr)
            return 124
        output = result.stdout or ""
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
        if result.returncode == 0:
            return 0
        if (attempt == attempts or _PERMANENT_ERROR.search(output)
                or not _TRANSIENT_ERROR.search(output)):
            return result.returncode
        print(f"Transient download failure; retrying in {retry_delay}s "
              f"({attempt}/{attempts}).", flush=True)
        time.sleep(retry_delay)
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/install_dependencies.py REQUIREMENTS_FILE")
    sys.exit(install_requirements(sys.argv[1]))
