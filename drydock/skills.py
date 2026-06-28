"""Skills — user-defined reusable slash commands.

A skill is a markdown file with simple frontmatter; its body is a prompt the
user invokes as ``/<name>`` in the TUI (or --cli). It saves retyping common
instructions ("/review", "/explain", "/testgen") and ships project conventions
alongside the code.

Discovery (project overrides user on a name clash):
  • ~/.drydock/skills/*.md          (personal, all projects)
  • <cwd>/.drydock/skills/*.md      (this project)

File format (frontmatter optional; name defaults to the filename):
    ---
    name: review
    description: Review the current diff for bugs
    ---
    Run GitDiff, then review the changes for bugs and suggest fixes.

Invocation:
  • ``/review``                 → the body becomes the user turn.
  • ``/review focus on perf``   → trailing text replaces ``$ARGS`` in the body,
    or is appended if the body has no ``$ARGS`` placeholder.

All logic original to Drydock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.I)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    source: str  # file path, for /skills listing

    def render(self, args: str = "") -> str:
        """Expand the skill into a prompt, substituting/append args."""
        args = (args or "").strip()
        if "$ARGS" in self.body:
            return self.body.replace("$ARGS", args)
        return f"{self.body}\n\n{args}".rstrip() if args else self.body


def _parse(path: Path) -> Skill | None:
    try:
        text = path.read_text("utf-8", "ignore")
    except OSError:
        return None
    name = path.stem.lower()
    description = ""
    body = text
    m = _FRONTMATTER.match(text)
    if m:
        meta, body = m.group(1), m.group(2)
        for line in meta.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key, val = key.strip().lower(), val.strip()
                if key == "name" and val:
                    name = val.lower()
                elif key == "description":
                    description = val
    body = body.strip()
    if not body or not _NAME_RE.match(name):
        return None
    return Skill(name=name, description=description, body=body, source=str(path))


def skills_dirs(cwd: str) -> list[Path]:
    return [Path.home() / ".drydock" / "skills", Path(cwd) / ".drydock" / "skills"]


def load_skills(cwd: str) -> dict[str, Skill]:
    """Load all skills; later dirs (project) override earlier (user) by name."""
    skills: dict[str, Skill] = {}
    for d in skills_dirs(cwd):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            sk = _parse(f)
            if sk:
                skills[sk.name] = sk
    return skills
