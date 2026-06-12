#!/usr/bin/env python3
"""Pre-release credential-exfiltration scanner for drydock v3.

Permanent provenance gate. v2 (a mistral-vibe fork) shipped inherited code
that mailed a GitHub token to a hardcoded third-party host and an API key to
an analytics endpoint; PyPI quarantined the account. v3 is clean-room, and
this scanner exists so that shape can never ship again.

It scans a built wheel (`*.whl`) or a source tree, reading every `.py` file,
and reports findings by severity:

  HIGH  — blocks the release (exit 2):
            * a non-allowlisted host in a file that reads a credential,
              builds an auth header, or makes a network call (exfil shape)
            * decode-then-exec obfuscation (base64/hex/marshal/pickle -> exec)

  MEDIUM — reported, does not block (exit 0):
            * a credential transmitted to an allowlisted host (confirm intent)
            * a lone non-allowlisted host (string only)
            * lone exec/eval

v3 is provider-agnostic: the LLM endpoint is a config value, never a baked-in
host, so legitimate provider traffic carries no hardcoded host to flag.

Usage:
    python3 scripts/security_scan.py path/to/drydock-X.Y.Z.whl
    python3 scripts/security_scan.py drydock/
    python3 scripts/security_scan.py            # defaults to ./drydock
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

# Hosts the package may legitimately reference as literals. Provider endpoints
# come from user config (variables), so they are not — and need not be — here.
ALLOWED_HOST_SUFFIXES = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "host.docker.internal",
    "api.openai.com",
    "api.anthropic.com",
    "googleapis.com",
    "pypi.org",
    "github.com",
    "api.github.com",
    "json-schema.org",
    "example.com",
    "drydock.pages.dev",  # the project's own landing page (string-only refs)
)

RE_SECRET_READ = re.compile(
    r"""(
        os\.environ\b.*?(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CRED)
      | os\.getenv\s*\(\s*['"][^'"]*(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CRED)
      | \.ssh/|\bid_rsa\b|\.aws/|\.netrc\b
      | pypi_token|github_token|dockerhub_password|cloudflare_token
    )""",
    re.IGNORECASE | re.VERBOSE,
)
RE_NETWORK_SEND = re.compile(
    r"""(
        \brequests\.(post|get|put|patch|delete)\s*\(
      | httpx\.(AsyncClient|Client)\b | \.(post|get|put|patch)\s*\(
      | urllib\.request|urlopen
      | socket\.socket|\.connect\s*\(
      | aiohttp\.
    )""",
    re.VERBOSE,
)
RE_AUTH_HEADER = re.compile(r"""Authorization|Bearer\s|X-Api-Key""", re.IGNORECASE)
RE_DECODE = re.compile(
    r"base64\.(b64decode|decodebytes)|bytes\.fromhex|\.fromhex\(|"
    r"marshal\.loads|pickle\.loads|codecs\.decode"
)
RE_DYNEXEC = re.compile(r"\bexec\s*\(|\beval\s*\(")
RE_URL = re.compile(r"https?://([A-Za-z0-9._-]+)")


def _host_allowed(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) or host.endswith(s)
               for s in ALLOWED_HOST_SUFFIXES)


def iter_py_files(target: Path):
    if target.suffix == ".whl" or (target.is_file() and zipfile.is_zipfile(target)):
        with zipfile.ZipFile(target) as zf:
            for name in zf.namelist():
                if name.endswith(".py"):
                    yield name, zf.read(name).decode("utf-8", "ignore")
    elif target.is_dir():
        for p in sorted(target.rglob("*.py")):
            yield str(p), p.read_text("utf-8", "ignore")
    elif target.suffix == ".py":
        yield str(target), target.read_text("utf-8", "ignore")
    else:
        raise SystemExit(f"Unsupported scan target: {target}")


def scan(target: Path) -> tuple[list[str], list[str]]:
    high: list[str] = []
    medium: list[str] = []
    for name, src in iter_py_files(target):
        if name.endswith("security_scan.py"):
            continue
        reads_secret = bool(RE_SECRET_READ.search(src))
        sends_net = bool(RE_NETWORK_SEND.search(src))
        auth_header = bool(RE_AUTH_HEADER.search(src))
        decodes = bool(RE_DECODE.search(src))
        dynexecs = bool(RE_DYNEXEC.search(src))
        unknown_hosts = sorted({h for h in RE_URL.findall(src) if not _host_allowed(h)})

        if unknown_hosts and (sends_net or reads_secret or auth_header):
            why = []
            if reads_secret:
                why.append("reads a credential")
            if auth_header:
                why.append("builds an auth header")
            if sends_net:
                why.append("makes a network call")
            high.append(
                f"{name}: non-allowlisted host(s) {unknown_hosts} in a file that "
                f"{' + '.join(why)} (exfiltration shape)"
            )
        if decodes and dynexecs:
            high.append(
                f"{name}: decodes data (base64/hex/marshal/pickle) and feeds "
                f"exec/eval (obfuscation)"
            )
        elif dynexecs:
            medium.append(f"{name}: uses exec()/eval()")
        if reads_secret and auth_header and sends_net and not unknown_hosts:
            medium.append(
                f"{name}: transmits a credential via an auth header to an "
                f"allowlisted host (confirm this is intended)"
            )
        if unknown_hosts and not (sends_net or reads_secret or auth_header):
            medium.append(
                f"{name}: hardcoded non-allowlisted host(s) {unknown_hosts} "
                f"(string only, no send detected)"
            )
    return high, medium


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("drydock")
    if not target.exists():
        print(f"[security_scan] target not found: {target}", file=sys.stderr)
        return 1
    high, medium = scan(target)
    if medium:
        print(f"[security_scan] {len(medium)} MEDIUM finding(s) (review, non-blocking):")
        for m in sorted(set(medium)):
            print(f"  - {m}")
    if high:
        print(f"\n[security_scan] {len(high)} HIGH finding(s) — REFUSING TO PUBLISH:")
        for h in sorted(set(high)):
            print(f"  !! {h}")
        print("\nResolve the HIGH findings before releasing.")
        return 2
    print(f"[security_scan] clean: 0 HIGH findings in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
