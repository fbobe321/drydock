"""Checkpoint system — snapshot files before edits for rewind.

Tracks file state before each edit so users can rewind to any point.
Stores snapshots in memory (session-scoped, not persisted).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FileSnapshot:
    path: str
    content: str  # Original content before edit
    timestamp: str


@dataclass
class Checkpoint:
    id: int
    description: str
    snapshots: list[FileSnapshot] = field(default_factory=list)
    message_count: int = 0  # Number of messages at this point
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%H:%M:%S")


class CheckpointManager:
    """Manages file checkpoints for rewind capability."""

    def __init__(self) -> None:
        self._checkpoints: list[Checkpoint] = []
        self._counter = 0

    def create_checkpoint(self, description: str, message_count: int = 0) -> Checkpoint:
        """Create a new checkpoint."""
        self._counter += 1
        cp = Checkpoint(
            id=self._counter,
            description=description,
            message_count=message_count,
        )
        self._checkpoints.append(cp)
        logger.debug("Checkpoint %d: %s", cp.id, description)
        return cp

    def snapshot_file(self, file_path: str) -> None:
        """Save the current content of a file before editing it."""
        if not self._checkpoints:
            self.create_checkpoint("auto")

        path = Path(file_path)
        if not path.exists():
            return

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            snapshot = FileSnapshot(
                path=str(path.resolve()),
                content=content,
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )
            self._checkpoints[-1].snapshots.append(snapshot)
        except (OSError, PermissionError) as e:
            logger.warning("Cannot snapshot %s: %s", file_path, e)

    def list_checkpoints(self) -> list[Checkpoint]:
        """List all checkpoints."""
        return list(self._checkpoints)

    def rewind_to(self, checkpoint_id: int) -> list[str]:
        """Restore files from a checkpoint. Returns list of restored file paths."""
        target = None
        for cp in self._checkpoints:
            if cp.id == checkpoint_id:
                target = cp
                break

        if not target:
            return []

        restored: list[str] = []

        # Collect all snapshots from this checkpoint forward (to undo)
        # We restore the EARLIEST snapshot of each file
        file_originals: dict[str, str] = {}
        for cp in self._checkpoints:
            if cp.id >= checkpoint_id:
                for snap in cp.snapshots:
                    if snap.path not in file_originals:
                        file_originals[snap.path] = snap.content

        for path_str, content in file_originals.items():
            try:
                Path(path_str).write_text(content, encoding="utf-8")
                restored.append(path_str)
            except (OSError, PermissionError) as e:
                logger.warning("Cannot restore %s: %s", path_str, e)

        # Remove checkpoints from the target onward
        self._checkpoints = [cp for cp in self._checkpoints if cp.id < checkpoint_id]

        return restored

    def rewind_last(self) -> list[str]:
        """Rewind the most recent checkpoint."""
        if not self._checkpoints:
            return []
        return self.rewind_to(self._checkpoints[-1].id)

    @property
    def count(self) -> int:
        return len(self._checkpoints)
