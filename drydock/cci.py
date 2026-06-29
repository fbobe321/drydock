"""CCI → NIST 800-53 control mapping.

DISA's Control Correlation Identifiers (CCIs) are the bridge between a STIG rule
and the NIST control it enforces: every STIG rule cites a CCI, and the CCI maps
to an 800-53 control. This builds that map from DISA's `U_CCI_List.xml` so
`/stig graph` can auto-create `Control —SATISFIED_BY→ STIG-Rule` edges.

The mappings are U.S. Government public-domain data (facts, not copyrightable).
The list is fetched once and cached locally; offline-safe. Stdlib only.

All logic original to Drydock.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# DISA U_CCI_List.xml (official list, mirrored). Government public-domain data.
_CCI_URL = ("https://raw.githubusercontent.com/CyberSecDef/Cyber.Trackr.Live/master/"
            "cyber.trackr.live/resources/data/cci/U_CCI_List.xml")
_UA = "drydock-cli"
_RE_CONTROL = re.compile(r"\s*([A-Za-z]{2})-(\d+)(?:\s*\(?(\d+)\)?|\.(\d+))?")


def cache_path(cwd: str) -> Path:
    return Path(cwd) / ".drydock" / "rmf" / "cci_map.json"


def _ln(el) -> str:
    return el.tag.split("}")[-1]


def _norm_control(index: str) -> str | None:
    """'AC-10' -> 'ac-10'; 'AC-2 (1)' / 'AC-2.1' -> 'ac-2.1'; junk -> None."""
    m = _RE_CONTROL.match(index or "")
    if not m:
        return None
    fam, num = m.group(1).lower(), m.group(2)
    enh = m.group(3) or m.group(4)
    return f"{fam}-{num}.{enh}" if enh else f"{fam}-{num}"


def parse_cci_list(path: str | Path) -> dict[str, str]:
    """Parse U_CCI_List.xml -> {CCI-id: control-id}. Picks the highest 800-53
    revision reference (excludes 800-53A, which lists assessment procedures)."""
    root = ET.parse(path).getroot()
    out: dict[str, str] = {}
    for item in (e for e in root.iter() if _ln(e) == "cci_item"):
        cid = item.get("id")
        best: str | None = None
        best_ver = -1
        for ref in item.iter():
            if _ln(ref) != "reference":
                continue
            title = ref.get("title", "")
            if not title.startswith("NIST SP 800-53") or "800-53A" in title:
                continue
            try:
                ver = int(ref.get("version") or 0)
            except ValueError:
                ver = 0
            ctl = _norm_control(ref.get("index", ""))
            if ctl and ver >= best_ver:
                best, best_ver = ctl, ver
        if cid and best:
            out[cid] = best
    return out


def fetch_to(path: str | Path, *, timeout: float = 60.0) -> None:
    req = urllib.request.Request(_CCI_URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted host)
        data = r.read()
    Path(path).write_bytes(data)


def load_map(cwd: str, *, refresh: bool = False) -> dict[str, str]:
    """Return the cached {CCI: control} map, building it from U_CCI_List.xml on
    first use (or --refresh). Offline-safe: returns the cache if the fetch fails,
    or {} if there's no cache and no network."""
    cp = cache_path(cwd)
    if cp.exists() and not refresh:
        try:
            return json.loads(cp.read_text("utf-8"))
        except (OSError, ValueError):
            pass
    cp.parent.mkdir(parents=True, exist_ok=True)  # ensure .drydock/rmf/ exists before writing
    raw = cp.with_suffix(".xml")
    try:
        fetch_to(raw)
    except (urllib.error.URLError, OSError):
        if cp.exists():
            try:
                return json.loads(cp.read_text("utf-8"))
            except (OSError, ValueError):
                return {}
        return {}
    mapping = parse_cci_list(raw)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(mapping), "utf-8")
    try:
        raw.unlink()
    except OSError:
        pass
    return mapping
