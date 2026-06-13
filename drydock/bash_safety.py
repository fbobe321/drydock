"""Bash command safety denylist.

`tool_bash` runs `shell=True`, so a single hallucinated or careless command
could wipe the user's disk. This module screens a command *before* it runs and
refuses only the small set of patterns that are catastrophic and irreversible
*and* have no legitimate place in a coding task: wiping the filesystem root or
home, writing raw to a block device, reformatting a disk, or a classic fork
bomb.

The bar is deliberately high. Ordinary destructive-but-scoped work
(`rm -rf build/`, `rm -rf node_modules`, `git reset --hard`) is NOT blocked —
that is real coding work and blocking it would be the kind of net-negative
circuit breaker we have learned to avoid. When something *is* blocked, the
tool returns a plain refusal string (it never raises), so the model gets clear
feedback and can choose a safer command instead of looping on an exception.

All logic original to Drydock.
"""
from __future__ import annotations

import re

# Each entry: (compiled pattern, human reason). A reason of None marks the
# special rm rule, which needs a second check on the delete target (below).
# Patterns match against the whitespace-normalized command string. Keep them
# narrow — a false positive blocks legitimate work and erodes trust.
_RULES: list[tuple[re.Pattern[str], str | None]] = [
    # rm -rf targeting the filesystem root, home, or a root-level wildcard.
    # Matches: rm -rf /, rm -fr /*, rm -rf ~, rm -rf $HOME, rm -rf /  (trailing).
    (
        re.compile(
            r"\brm\b[^|;&]*\s-[a-z]*r[a-z]*f|\brm\b[^|;&]*\s-[a-z]*f[a-z]*r",
            re.IGNORECASE,
        ),
        None,  # placeholder; rm needs a second check on the target (below)
    ),
    # Raw write to a whole block device (dd of=/dev/sda, > /dev/nvme0n1).
    (
        re.compile(r"\bof=/dev/(?:sd|nvme|hd|vd|xvd|mmcblk)", re.IGNORECASE),
        "writes a raw image directly onto a block device, destroying the disk",
    ),
    (
        re.compile(r">\s*/dev/(?:sd|nvme|hd|vd|xvd|mmcblk)", re.IGNORECASE),
        "redirects output onto a raw block device, corrupting the disk",
    ),
    # Reformat a filesystem.
    (
        re.compile(r"\bmkfs(?:\.\w+)?\b", re.IGNORECASE),
        "reformats a filesystem, erasing everything on it",
    ),
    # Repartition / zero a disk.
    (
        re.compile(r"\bdd\b[^|;&]*\bof=/dev/", re.IGNORECASE),
        "uses dd to overwrite a device node",
    ),
    # Classic fork bomb.
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "is a fork bomb that will exhaust the system's process table",
    ),
    # Recursively strip all permissions / ownership from the root.
    (
        re.compile(r"\b(?:chmod|chown)\b[^|;&]*\s-[a-z]*R[a-z]*\s[^|;&]*\s/(?:\s|$)", re.IGNORECASE),
        "recursively changes permissions/ownership of the filesystem root",
    ),
]

# rm targets that mean "the whole world." Checked only when the command is an
# `rm -rf`-style recursive force delete.
_RM_CATASTROPHIC_TARGET = re.compile(
    r"\brm\b[^|;&]*\s"
    r"(?:/|~|\$HOME|/\*|/\s|\.\s*/?\s*\*|--no-preserve-root)"
    r"(?:\s|$|/\*)",
    re.IGNORECASE,
)


def dangerous_command(command: str) -> str | None:
    """Return a refusal reason if the command is catastrophic, else None.

    Conservative by design: only the patterns above trip it. Everything
    else — including scoped `rm -rf subdir/` — passes through untouched.
    """
    if not command or not command.strip():
        return None
    cmd = " ".join(command.split())  # normalize whitespace

    for pattern, reason in _RULES:
        if reason is None:
            # The rm rule: recursive-force flag present *and* a world target.
            if pattern.search(cmd) and _rm_hits_world(cmd):
                return (
                    "deletes the filesystem root or home directory recursively"
                )
            continue
        if pattern.search(cmd):
            return reason
    return None


def _rm_hits_world(cmd: str) -> bool:
    """True if an `rm -rf` command targets / ~ $HOME or a root-level wildcard."""
    if _RM_CATASTROPHIC_TARGET.search(cmd):
        return True
    # `--no-preserve-root` is never used for legitimate scoped deletes.
    if "--no-preserve-root" in cmd.lower():
        return True
    return False


def refusal_message(command: str, reason: str) -> str:
    """The string returned to the model in place of running the command."""
    return (
        f"REFUSED: this command {reason}. Drydock will not run it.\n"
        f"Command: {command.strip()}\n"
        f"If this was a mistake, run a narrower command scoped to the specific "
        f"files or directory you intend to change."
    )


# ── Approval tier ───────────────────────────────────────────────────────────
# Commands that are legitimate but consequential: they should RUN, but only
# after the user okays them. This is distinct from the catastrophic denylist
# above (which is never run). Each entry: (pattern, human reason).
_APPROVAL_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(?:sudo|doas)\b", re.IGNORECASE),
        "runs with elevated privileges (sudo)",
    ),
    (
        re.compile(
            r"\b(?:apt|apt-get|dnf|yum|pacman|zypper|apk|brew)\b\s+"
            r"(?:install|remove|purge|upgrade|update|add)\b",
            re.IGNORECASE,
        ),
        "installs or removes system packages",
    ),
    (
        re.compile(
            r"\b(?:pip|pip3|pipx|npm|pnpm|yarn|cargo|gem|go)\b\s+"
            r"(?:install|add|i)\b",
            re.IGNORECASE,
        ),
        "installs packages from a network index",
    ),
    (
        re.compile(r"\b(?:curl|wget)\b", re.IGNORECASE),
        "fetches content from the network",
    ),
    (
        re.compile(r"\bgit\s+push\b", re.IGNORECASE),
        "pushes commits to a remote repository",
    ),
]


def requires_approval(command: str) -> str | None:
    """Return a reason if the command is sensitive enough to need user
    approval before running, else None. Assumes the catastrophic denylist
    (dangerous_command) has already passed."""
    if not command or not command.strip():
        return None
    cmd = " ".join(command.split())
    for pattern, reason in _APPROVAL_RULES:
        if pattern.search(cmd):
            return reason
    return None
