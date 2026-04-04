import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional

@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str

class LogAnalyzer:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.entries: List[LogEntry] = []
        # Pattern: TIMESTAMP LEVEL MESSAGE (e.g., 2023-10-27 10:00:00 INFO Something happened)
        self.pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\w+)\s+(.*)$')

    def parse(self) -> List[LogEntry]:
        self.entries = []
        if not self.file_path.exists():
            raise FileNotFoundError(f"Log file not found: {self.file_path}")

        with self.file_path.open('r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                match = self.pattern.match(line)
                if match:
                    self.entries.append(LogEntry(
                        timestamp=match.group(1),
                        level=match.group(2).upper(),
                        message=match.group(3)
                    ))
        return self.entries

    def filter_by_level(self, level: str) -> List[LogEntry]:
        return [e for e in self.entries if e.level == level.upper()]

    def search(self, pattern: str) -> List[LogEntry]:
        return [e for e in self.entries if re.search(pattern, e.message)]

    def get_summary(self) -> Dict[str, int]:
        summary = {}
        for e in self.entries:
            summary[e.level] = summary.get(e.level, 0) + 1
        return summary
