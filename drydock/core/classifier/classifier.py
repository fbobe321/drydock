"""Classifier engine — turn observations into FailureSignals.

`Classifier` is the public class. It holds a (rule list, source label,
session/prompt id provider) and exposes:

    classify_lines(lines)  -> list[FailureSignal]
    classify_text(text)    -> list[FailureSignal]
    classify_file(path)    -> list[FailureSignal]
    summarize(signals)     -> dict[bucket, count]

The engine is rule-first, scan-once, dedup-by-(pattern_id, evidence)
within a single classify call. That guarantees a single failure
doesn't blow up into N copies if it appears on multiple log lines.

Module-level helpers (`classify_text`, `classify_lines`) wrap a default
Classifier so callers that don't need to override anything can stay
one-liners.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

from drydock.core.classifier.rules import RULES, ClassificationRule
from drydock.core.classifier.signal import Bucket, FailureSignal


class Classifier:
    """Rule-based v0 classifier."""

    def __init__(
        self,
        rules: tuple[ClassificationRule, ...] = RULES,
        source: str = "",
        session_id: str = "",
        prompt_id: str = "",
    ):
        self.rules = rules
        self.source = source
        self.session_id = session_id
        self.prompt_id = prompt_id

    def classify_text(self, text: str) -> list[FailureSignal]:
        return self.classify_lines(text.splitlines())

    def classify_lines(self, lines: Iterable[str]) -> list[FailureSignal]:
        signals: list[FailureSignal] = []
        seen: set[tuple[str, str]] = set()  # (pattern_id, evidence)
        for line in lines:
            line = line.rstrip("\n\r")
            if not line.strip():
                continue
            for rule in self.rules:
                if rule.regex.search(line):
                    key = (rule.pattern_id, line.strip())
                    if key in seen:
                        break
                    seen.add(key)
                    signals.append(FailureSignal(
                        bucket=rule.bucket,
                        pattern_id=rule.pattern_id,
                        evidence=line.strip(),
                        suggested_action=rule.suggested_action,
                        confidence=rule.confidence,
                        source=self.source,
                        session_id=self.session_id,
                        prompt_id=self.prompt_id,
                        extra={
                            "also": ",".join(str(b) for b in rule.also),
                        } if rule.also else {},
                    ))
                    break  # first-match wins per line
        return signals

    def classify_file(self, path: str | Path) -> list[FailureSignal]:
        p = Path(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        prior_source = self.source
        if not self.source:
            self.source = str(p)
        try:
            return self.classify_text(text)
        finally:
            self.source = prior_source

    @staticmethod
    def summarize(signals: list[FailureSignal]) -> dict[str, int]:
        """Return {bucket_name: count} across the signal list."""
        counter: Counter[str] = Counter(str(s.bucket) for s in signals)
        return dict(counter)

    @staticmethod
    def top_patterns(
        signals: list[FailureSignal], n: int = 10
    ) -> list[tuple[str, int]]:
        """Return the N most-fired pattern_ids and their counts."""
        counter: Counter[str] = Counter(s.pattern_id for s in signals)
        return counter.most_common(n)


# --- Module-level convenience wrappers --------------------------------

_DEFAULT = Classifier()


def classify_text(text: str, **kwargs) -> list[FailureSignal]:
    if kwargs:
        return Classifier(**kwargs).classify_text(text)
    return _DEFAULT.classify_text(text)


def classify_lines(lines: Iterable[str], **kwargs) -> list[FailureSignal]:
    if kwargs:
        return Classifier(**kwargs).classify_lines(lines)
    return _DEFAULT.classify_lines(lines)
