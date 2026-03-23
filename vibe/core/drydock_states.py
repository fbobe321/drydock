"""Drydock nautical state engine.

Maps agent workflow categories to nautical terminology, providing
thematic status messages for the TUI and CLI output.
"""

import random
from collections import deque
from typing import Dict, List

# ---------------------------------------------------------------------------
# State categories – each list is drawn from drydock_terms.md
# ---------------------------------------------------------------------------

STATE_CATEGORIES: Dict[str, List[str]] = {
    "plan": [
        "Planning",
        "Drafting",
        "Organizing",
        "Allocating",
        "Coordinating",
        "Preparing",
        "Structuring",
        "Assembling",
        "Sequencing",
        "Assigning",
        "Orchestrating",
    ],
    "search": [
        "Scanning",
        "Inspecting",
        "Searching",
        "Reviewing",
        "Checking",
        "Observing",
        "Sweeping",
        "Parsing",
        "Examining",
        "Indexing",
        "Cataloging",
        "Tracing",
    ],
    "reason": [
        "Charting",
        "Navigating",
        "Plotting",
        "Bearing",
        "Steering",
        "Aligning",
        "Fixing",
        "Triangulating",
        "Surveying",
        "Mapping",
        "Sounding",
        "Reconnoitering",
    ],
    "execute": [
        "Sailing",
        "Advancing",
        "Propelling",
        "Engaging",
        "Driving",
        "Launching",
        "Operating",
        "Running",
        "Deploying",
        "Maneuvering",
        "Thrusting",
        "Accelerating",
    ],
    "debug": [
        "Patching",
        "Sealing",
        "Repairing",
        "Reinforcing",
        "Refitting",
        "Calibrating",
        "Stabilizing",
        "Tightening",
        "Rebalancing",
        "Restoring",
        "Welding",
        "Securing",
    ],
    "retry": [
        "Adjusting",
        "Refining",
        "Recalibrating",
        "Replotting",
        "Rechecking",
        "Revising",
        "Cycling",
        "Iterating",
        "Correcting",
        "Reworking",
        "Reattempting",
    ],
    "error": [
        "Drifting",
        "Stalling",
        "Faltering",
        "Deviating",
        "Misaligning",
        "Obscuring",
        "Clouding",
        "Turbulencing",
        "Destabilizing",
        "Conflicting",
        "Failing",
    ],
    "complete": [
        "Docking",
        "Anchoring",
        "Mooring",
        "Completing",
        "Finishing",
        "Delivering",
        "Finalizing",
        "Securing",
        "Concluding",
        "Settling",
    ],
    "reflect": [
        "Reassessing",
        "Reevaluating",
        "Verifying",
        "Validating",
        "Confirming",
        "Crosschecking",
        "Auditing",
        "Reviewing",
        "Reflecting",
        "Reconciling",
    ],
}

# ---------------------------------------------------------------------------
# Recent-term tracking (avoid repeating the last 2 terms used)
# ---------------------------------------------------------------------------

_recent: deque = deque(maxlen=2)


def get_state_term(category: str) -> str:
    """Return a random term for *category*, avoiding the last 2 used terms.

    Parameters
    ----------
    category:
        One of the keys in ``STATE_CATEGORIES``.

    Returns
    -------
    str
        A nautical term appropriate for the given workflow phase.

    Raises
    ------
    KeyError
        If *category* is not a recognised state category.
    """
    terms = STATE_CATEGORIES[category]
    available = [t for t in terms if t not in _recent]
    # If every term has been used recently (tiny list edge-case), allow all.
    if not available:
        available = terms
    chosen = random.choice(available)
    _recent.append(chosen)
    return chosen


def format_state(term: str) -> str:
    """Format a nautical term as a user-visible status string.

    Parameters
    ----------
    term:
        A single nautical verb (e.g. ``"Charting"``).

    Returns
    -------
    str
        The formatted state string, e.g. ``"⚓ Charting..."``.
    """
    return f"\u2693 {term}..."
