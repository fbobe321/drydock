"""Session checkpointing — rewind conversation + code together.

Why
---
Long sessions (and the 2000-prompt stress harness) hit issues at step 300
that today require restarting from step 1. Checkpoints let us snapshot
both the conversation pointer AND the working-directory file state after
each user turn, then jump back to an earlier point and resume.

Storage
-------
Per-session bare git repo lives at:

    ~/.drydock/checkpoints/<session_id>/repo.git

The user's project cwd is the work-tree at runtime. We never touch the
user's own .git (if they have one) — every git invocation passes
--git-dir and --work-tree explicitly.

Metadata sidecar:

    ~/.drydock/checkpoints/<session_id>/state.json

records {index, msg_index, commit_hash, label, timestamp} for each
checkpoint.

Restore modes
-------------
- "code" — git read-tree to restore the work-tree to the snapshot
- "conversation" — caller truncates the in-memory message list to msg_index
- "both" — both of the above; the typical case

Safety
------
Before any restore that changes the work-tree, we stash whatever is
currently uncommitted to refs/drydock/safety/<timestamp> so a wrong
rewind never destroys work the user might want to recover.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Files we never include in a checkpoint. Mirrors common .gitignore.
_EXCLUDES = (
    ".git",
    ".drydock",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "dist",
    "build",
    "*.egg-info",
)


class CheckpointError(RuntimeError):
    """Anything went wrong inside the checkpoint engine."""


@dataclass
class Checkpoint:
    index: int                  # 0-based position in this session
    msg_index: int              # len(agent.messages) at record time
    commit: str                 # git SHA in the checkpoints repo
    label: str                  # short hint (typically the user prompt preview)
    timestamp: str              # ISO 8601 UTC
    # Opaque payload for the agent to round-trip its own counters
    # (circuit-breaker fires, loop flags, error-round count, etc.) so
    # rewinding back to step 100 reverts those too. Must be JSON-safe.
    agent_state: dict = field(default_factory=dict)

    def short_commit(self) -> str:
        return self.commit[:8]


@dataclass
class DiffStats:
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0

    def __bool__(self) -> bool:
        return self.files_changed > 0 or self.insertions > 0 or self.deletions > 0


class CheckpointStore:
    """One CheckpointStore per drydock session."""

    def __init__(self, work_tree: Path, session_id: str,
                 base_dir: Path | None = None) -> None:
        self.work_tree = work_tree.resolve()
        self.session_id = session_id
        base = base_dir if base_dir is not None else (
            Path.home() / ".drydock" / "checkpoints"
        )
        self.session_dir = base / session_id
        self.git_dir = self.session_dir / "repo.git"
        self.state_file = self.session_dir / "state.json"
        self.checkpoints: list[Checkpoint] = []
        self._init_repo()
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, msg_index: int, label: str = "",
               agent_state: dict | None = None) -> Checkpoint:
        """Snapshot the current work-tree under a fresh checkpoint.

        Idempotent for the no-change case: if the work-tree hasn't
        changed since the last checkpoint AND agent_state matches the
        previous checkpoint's, returns the previous one instead of
        creating a duplicate commit.

        agent_state is an opaque JSON-safe dict the caller stashes so
        a future restore() can round-trip it back. Used by AgentLoop
        to checkpoint circuit-breaker counts, loop flags, etc.
        """
        # Stage everything; -A picks up adds/modifies/deletes.
        self._git("add", "-A", check=False)
        try:
            tree_sha = self._git("write-tree").strip()
        except CheckpointError as exc:
            raise CheckpointError(
                f"checkpoint write-tree failed: {exc}"
            ) from exc

        snapshot_state = dict(agent_state or {})

        # Skip duplicate-tree commits: if the new tree matches the last
        # checkpoint's tree AND agent_state hasn't changed either,
        # reuse the previous checkpoint.
        if self.checkpoints:
            last = self.checkpoints[-1]
            try:
                last_tree = self._git(
                    "rev-parse", f"{last.commit}^{{tree}}"
                ).strip()
                if last_tree == tree_sha and last.agent_state == snapshot_state:
                    return last
            except CheckpointError:
                pass

        # Commit
        msg = f"checkpoint {len(self.checkpoints)}"
        if label:
            msg += f": {label[:80]}"
        try:
            args = ["commit-tree", tree_sha, "-m", msg]
            if self.checkpoints:
                args += ["-p", self.checkpoints[-1].commit]
            commit_sha = self._git(*args).strip()
            self._git("update-ref", "refs/heads/main", commit_sha)
        except CheckpointError as exc:
            raise CheckpointError(
                f"checkpoint commit-tree failed: {exc}"
            ) from exc

        cp = Checkpoint(
            index=len(self.checkpoints),
            msg_index=msg_index,
            commit=commit_sha,
            label=label[:200],
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_state=snapshot_state,
        )
        self.checkpoints.append(cp)
        self._save_state()
        return cp

    def restore(self, checkpoint_index: int,
                mode: str = "both") -> Checkpoint:
        """Restore code (and/or conversation) to checkpoint_index.

        mode is one of: "code", "conversation", "both".

        For "code"/"both": rewrites work-tree files to the snapshot.
        Pre-stashes any uncommitted changes to a safety ref so nothing
        is lost.

        For "conversation"/"both": this engine doesn't know about the
        agent's message list — caller is responsible for truncating
        their messages to checkpoint.msg_index.

        Returns the restored Checkpoint for caller convenience.
        """
        if mode not in ("code", "conversation", "both"):
            raise ValueError(f"unknown mode: {mode!r}")
        if not (0 <= checkpoint_index < len(self.checkpoints)):
            raise CheckpointError(
                f"checkpoint {checkpoint_index} out of range "
                f"(have {len(self.checkpoints)})"
            )

        cp = self.checkpoints[checkpoint_index]

        if mode in ("code", "both"):
            self._stash_safety()
            try:
                # read-tree --reset -u rewrites the index AND the work-tree
                # to match the target tree. Files in work-tree but not
                # in target are left alone (untracked). Files in both
                # are overwritten. Files in target but not in work-tree
                # are restored. This is the right semantics for "rewind
                # to this snapshot."
                self._git("read-tree", "--reset", "-u", cp.commit)
            except CheckpointError as exc:
                raise CheckpointError(
                    f"checkpoint restore failed: {exc}"
                ) from exc

        # Drop any checkpoints AFTER the restore point — those snapshots
        # are no longer reachable from a coherent timeline, and we want
        # subsequent record() calls to extend from the restored point.
        self.checkpoints = self.checkpoints[: checkpoint_index + 1]
        # Move the branch HEAD back too so future commits parent off
        # the restored checkpoint.
        try:
            self._git("update-ref", "refs/heads/main", cp.commit)
        except CheckpointError:
            pass
        self._save_state()
        return cp

    def diff_stats(self, checkpoint_index: int) -> DiffStats:
        """How does the current work-tree differ from this checkpoint?"""
        if not (0 <= checkpoint_index < len(self.checkpoints)):
            return DiffStats()
        cp = self.checkpoints[checkpoint_index]
        try:
            # Stage current state for an apples-to-apples compare; otherwise
            # untracked files won't show up.
            self._git("add", "-A", check=False)
            cur_tree = self._git("write-tree").strip()
            out = self._git(
                "diff", "--shortstat", cp.commit, cur_tree
            ).strip()
        except CheckpointError:
            return DiffStats()

        return _parse_shortstat(out)

    def list_checkpoints(self, limit: int | None = None) -> list[Checkpoint]:
        """Most-recent first."""
        ordered = list(reversed(self.checkpoints))
        if limit is not None:
            return ordered[:limit]
        return ordered

    def latest(self) -> Checkpoint | None:
        return self.checkpoints[-1] if self.checkpoints else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _git(self, *args: str, check: bool = True) -> str:
        """Run git scoped to OUR repo + the project work-tree.

        Always passes --git-dir and --work-tree explicitly so we never
        touch the user's own .git (if they have one).
        """
        env = {
            **os.environ,
            "GIT_DIR": str(self.git_dir),
            "GIT_WORK_TREE": str(self.work_tree),
            # Stop user-global hooks from firing inside our internal repo.
            "GIT_TERMINAL_PROMPT": "0",
        }
        try:
            result = subprocess.run(
                ["git", *args],
                env=env,
                capture_output=True,
                text=True,
                check=check,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise CheckpointError("git binary not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise CheckpointError(f"git {args[0]} timed out") from exc
        if check and result.returncode != 0:
            raise CheckpointError(
                f"git {' '.join(args)} -> {result.returncode}\n"
                f"stderr: {result.stderr.strip()}"
            )
        return result.stdout

    def _init_repo(self) -> None:
        """Create the bare repo + write our excludes file."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        if not (self.git_dir / "HEAD").is_file():
            try:
                subprocess.run(
                    ["git", "init", "--bare", str(self.git_dir)],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            except (subprocess.CalledProcessError,
                    subprocess.TimeoutExpired) as exc:
                raise CheckpointError(
                    f"failed to init checkpoint repo: {exc}"
                ) from exc

            # Identity for commits — required, doesn't matter what.
            self._git("config", "user.email", "checkpoint@drydock")
            self._git("config", "user.name", "drydock-checkpoint")

        # Excludes file — applied to every `git add`. Lives inside the
        # bare repo so it can't conflict with the user's own .gitignore.
        info_dir = self.git_dir / "info"
        info_dir.mkdir(exist_ok=True)
        excludes = info_dir / "exclude"
        excludes.write_text("\n".join(_EXCLUDES) + "\n")

    def _load_state(self) -> None:
        if not self.state_file.is_file():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.checkpoints = [
            Checkpoint(**item) for item in raw.get("checkpoints", [])
        ]

    def _save_state(self) -> None:
        payload = {
            "session_id": self.session_id,
            "work_tree": str(self.work_tree),
            "checkpoints": [asdict(c) for c in self.checkpoints],
        }
        try:
            self.state_file.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except OSError:
            # Non-fatal: in-memory list is still authoritative for this
            # process; we'll retry on the next record().
            pass

    def _stash_safety(self) -> None:
        """Stash uncommitted work to refs/drydock/safety/<timestamp>.

        Best-effort: failures here are non-fatal — the worst case is
        that the user can't recover overwritten changes via git, which
        matches the pre-checkpointing world.
        """
        try:
            self._git("add", "-A", check=False)
            tree = self._git("write-tree", check=False).strip()
            if not tree:
                return
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            commit = self._git(
                "commit-tree", tree, "-m", f"safety {stamp}", check=False
            ).strip()
            if commit:
                self._git(
                    "update-ref",
                    f"refs/drydock/safety/{stamp}",
                    commit,
                    check=False,
                )
        except CheckpointError:
            pass


def _parse_shortstat(line: str) -> DiffStats:
    """Parse 'N files changed, X insertions(+), Y deletions(-)'."""
    stats = DiffStats()
    if not line:
        return stats
    parts = [p.strip() for p in line.split(",")]
    for part in parts:
        words = part.split()
        if not words:
            continue
        try:
            n = int(words[0])
        except ValueError:
            continue
        if "file" in part:
            stats.files_changed = n
        elif "insertion" in part:
            stats.insertions = n
        elif "deletion" in part:
            stats.deletions = n
    return stats
