"""Prediction register — say what you expect BEFORE you look, then record the error.

WHY THIS EXISTS
---------------
This module is the direct product of a measurement failure in this project's own
research log, and the evidence for it is unusually clean.

Over one week, four separate claims were reported and then walked back:
  · a pawl ablation, confounded by temperature (best-of-N ran at a flat 0.2 while the
    ratchet explored 0.2-0.85) — the control was crippled and the effect over-credited;
  · a prompt scaffold reported at +6.8 points, corrected to +3.4 when measured
    end-to-end instead of unioned across two runs, then to +0.0 when the missing
    control finally ran;
  · a "climbing" training curve called on four checkpoints, reversed by the fifth.
Every one of those corrections came from tightening the method on data already in hand
— not from new data.

Exactly one conclusion that week required no correction: a training run whose stop
criterion AND falsifying prediction were written to a file before any data existed. It
landed inside its own pre-registered band and the verdict stood.

The difference was not care or effort. It was that the prediction was recorded where it
could not be quietly revised afterwards. That is all this module does.

DESIGN NOTE — why this is not a prompt
A reasoning checklist covering "predict, then test" was measured on terminal-bench-2 and
did not beat a plain retry. Telling a model to be rigorous does not make it rigorous.
Giving it a place to write a prediction, and then confronting it with the error, is a
different mechanism: it produces an artifact and a number rather than an intention.

Advisory by contract: nothing here blocks a call or raises on bad input.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

OPEN = "open"
CONFIRMED = "confirmed"      # observation matched the prediction
REFUTED = "refuted"          # it did not — the valuable case
ABANDONED = "abandoned"      # never tested (tracked: an untested prediction is a smell)


@dataclass
class Prediction:
    """What we expect to see, registered before the experiment runs."""
    id: str
    claim: str = ""              # what is being tested
    expected: str = ""           # the concrete predicted observation
    # What result would prove this WRONG. A prediction with no falsifier is a hope;
    # this field is what makes the register more than a diary.
    falsifier: str = ""
    observed: str = ""
    status: str = OPEN
    note: str = ""               # why the error occurred (filled on resolve)

    def summary(self) -> str:
        mark = {OPEN: "·", CONFIRMED: "✓", REFUTED: "✗", ABANDONED: "—"}.get(self.status, "·")
        s = f"{mark} {self.id} {self.claim[:60]}"
        if self.status == OPEN:
            s += f"  expect: {self.expected[:40]}"
        elif self.status in (CONFIRMED, REFUTED):
            s += f"  expected {self.expected[:28]!r} / saw {self.observed[:28]!r}"
        return s


class Register:
    """Predictions for one task or experiment, persisted so they cannot be edited
    after the fact without leaving a trace."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.items: list[Prediction] = []
        if self.path and self.path.exists():
            self.load()

    def register(self, claim: str, expected: str, falsifier: str = "") -> Prediction:
        """Record a prediction BEFORE running the test. Returns the row so the caller can
        quote its id in the experiment log."""
        p = Prediction(id=f"p{len(self.items) + 1}",
                       claim=(claim or "").strip(),
                       expected=(expected or "").strip(),
                       falsifier=(falsifier or "").strip())
        self.items.append(p)
        self._save()
        return p

    def resolve(self, pid: str, observed: str, *, matched: bool | None = None,
                note: str = "") -> Prediction | None:
        """Record what actually happened.

        `matched=None` leaves the verdict to the caller's own comparison rather than
        guessing from string equality — a numeric result inside a pre-registered band is
        a match even though the strings differ.
        """
        for p in self.items:
            if p.id != pid:
                continue
            p.observed = (observed or "").strip()
            p.note = (note or "").strip()
            if matched is None:
                matched = p.expected.strip().lower() == p.observed.strip().lower()
            p.status = CONFIRMED if matched else REFUTED
            self._save()
            return p
        return None

    def abandon(self, pid: str, note: str = "") -> Prediction | None:
        for p in self.items:
            if p.id == pid:
                p.status = ABANDONED
                p.note = (note or "").strip()
                self._save()
                return p
        return None

    # ── the numbers worth looking at ─────────────────────────────────────────
    def open_predictions(self) -> list[Prediction]:
        return [p for p in self.items if p.status == OPEN]

    def calibration(self) -> dict:
        """How often predictions survive contact with reality.

        A high hit-rate is NOT the goal and is usually a bad sign: it means predictions
        are being made only where the answer is already known, which is where they teach
        nothing. Refuted predictions are where the model of the problem actually changes.
        """
        done = [p for p in self.items if p.status in (CONFIRMED, REFUTED)]
        conf = sum(1 for p in done if p.status == CONFIRMED)
        return {
            "registered": len(self.items),
            "resolved": len(done),
            "confirmed": conf,
            "refuted": len(done) - conf,
            "abandoned": sum(1 for p in self.items if p.status == ABANDONED),
            "open": len(self.open_predictions()),
            "hit_rate": (conf / len(done)) if done else None,
        }

    def unfalsifiable(self) -> list[Prediction]:
        """Predictions registered with no falsifier — hopes, not tests."""
        return [p for p in self.items if not p.falsifier]

    def render(self) -> str:
        if not self.items:
            return "(no predictions registered)"
        lines = [p.summary() for p in self.items]
        c = self.calibration()
        rate = "n/a" if c["hit_rate"] is None else f"{c['hit_rate']:.0%}"
        lines.append(f"— {c['resolved']}/{c['registered']} resolved · "
                     f"{c['refuted']} refuted · hit-rate {rate}")
        weak = self.unfalsifiable()
        if weak:
            lines.append(f"⚠ {len(weak)} prediction(s) with no falsifier: "
                         f"{', '.join(p.id for p in weak)}")
        return "\n".join(lines)

    # ── persistence ──────────────────────────────────────────────────────────
    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(p) for p in self.items], indent=2), encoding="utf-8")
        except OSError:
            pass

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path else []
            self.items = [Prediction(**{k: v for k, v in d.items()
                                        if k in Prediction.__dataclass_fields__})
                          for d in raw]
        except (OSError, json.JSONDecodeError, TypeError):
            self.items = []
