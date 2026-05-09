"""Parse the `X-Drydock-Steering` HTTP header into structured directives.

Wire format:
    X-Drydock-Steering: <mode>@<layer>×<scale>[,<mode>@<layer>×<scale>...]

The Unicode multiplication sign `×` is the separator between layer and
scale. ASCII `x` is also accepted (for shells and clients that mangle
Unicode).

Examples:
    X-Drydock-Steering: show_work@18×0.6
    X-Drydock-Steering: show_work@18×0.6,verify@22×0.4
    X-Drydock-Steering: show_work@18x0.6   (ASCII fallback)

A missing or empty header → no steering, sidecar returns identical
output to llama.cpp on the same prompt. Malformed entries are
skipped with a logged warning rather than 400'd — the sidecar is
defensive in depth, like the existing `agent_loop.py` steering hook.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SteeringDirective:
    """One mode/layer/scale triple parsed from the header."""
    mode: str
    layer: int
    scale: float


# `mode@layer×scale` or `mode@layerxscale`. mode is restricted to
# [a-z0-9_-] to keep the wire format safe across HTTP intermediaries.
_DIRECTIVE_RE = re.compile(
    r"^(?P<mode>[a-z0-9_-]+)@(?P<layer>\d+)[×x](?P<scale>-?\d+(?:\.\d+)?)$"
)


def parse_header(value: str | None) -> list[SteeringDirective]:
    """Parse the X-Drydock-Steering header value.

    Returns the list of valid directives. Missing/empty header → []. A
    malformed entry is dropped with a warning rather than raising; we
    never want a header parse error to 5xx an otherwise-good request.
    """
    if not value:
        return []
    out: list[SteeringDirective] = []
    for raw in value.split(","):
        entry = raw.strip()
        if not entry:
            continue
        m = _DIRECTIVE_RE.match(entry)
        if not m:
            logger.warning("steering header: ignoring malformed entry %r", entry)
            continue
        try:
            out.append(
                SteeringDirective(
                    mode=m.group("mode"),
                    layer=int(m.group("layer")),
                    scale=float(m.group("scale")),
                )
            )
        except (ValueError, OverflowError) as e:
            logger.warning(
                "steering header: ignoring %r (parse error: %s)", entry, e
            )
    return out
