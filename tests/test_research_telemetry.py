"""Phase-1 research telemetry (drydock/research_telemetry.py): the AttemptRecord
schema, JSONL sink, fitness/delta helpers, and lineage reconstruction. Pure/stdlib.
See docs/compute_optimal_agent_scaling_prd.md §20/§21."""
from drydock.research_telemetry import (
    AttemptRecord,
    TelemetryLog,
    deltas,
    fitness,
)


def test_fitness_graded_and_zero_safe():
    assert fitness(14, 22) == 14 / 22
    assert fitness(0, 0) == 0.0            # nothing gradeable yet → 0, no ZeroDivision


def test_deltas_new_and_lost_by_check_identity():
    # parent passed checks {1,2,3}; child passes {1,2,4} → gained 4, lost 3
    new, lost = deltas([1, 2, 3], [1, 2, 4])
    assert new == [4] and lost == [3]


def test_deltas_handle_none_descriptors():
    assert deltas(None, ["a", "b"]) == (["a", "b"], [])
    assert deltas(["a"], None) == ([], ["a"])


def test_record_roundtrip_and_read(tmp_path):
    log = TelemetryLog("exp-1", root=str(tmp_path))
    log.record(AttemptRecord(experiment_id="exp-1", strategy="eratchet",
                             generation=1, agent=0, attempt_id="g1v0",
                             passed=12, total=22, descriptor=[1, 2, 3]))
    log.record(AttemptRecord(experiment_id="exp-1", generation=2, agent=1,
                             attempt_id="g2v1", parent="g1v0",
                             passed=14, total=22, descriptor=[1, 2, 3, 4, 5]))
    rows = log.read()
    assert len(rows) == 2
    assert rows[0]["attempt_id"] == "g1v0" and rows[0]["passed"] == 12
    assert rows[1]["parent"] == "g1v0" and rows[1]["schema"] == 1


def test_lineage_children_from_parent_links(tmp_path):
    log = TelemetryLog("exp-2", root=str(tmp_path))
    log.record(AttemptRecord(experiment_id="exp-2", attempt_id="g0v0"))
    log.record(AttemptRecord(experiment_id="exp-2", attempt_id="g1v0", parent="g0v0"))
    log.record(AttemptRecord(experiment_id="exp-2", attempt_id="g1v1", parent="g0v0"))
    kids = log.children()
    assert sorted(kids["g0v0"]) == ["g1v0", "g1v1"]


def test_read_tolerates_corrupt_trailing_line(tmp_path):
    log = TelemetryLog("exp-3", root=str(tmp_path))
    log.record(AttemptRecord(experiment_id="exp-3", attempt_id="g0v0"))
    with log.path.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    rows = log.read()
    assert len(rows) == 1 and rows[0]["attempt_id"] == "g0v0"


def test_record_never_raises_on_bad_root():
    # a path that cannot be created (a file where the dir should be) must not raise
    log = TelemetryLog("exp-4", root="/dev/null/nope")
    assert log.record(AttemptRecord(experiment_id="exp-4", attempt_id="x")) is not None
    assert log.read() == []


def test_plain_dict_record_accepted(tmp_path):
    log = TelemetryLog("exp-5", root=str(tmp_path))
    log.record({"experiment_id": "exp-5", "attempt_id": "d0", "passed": 3, "total": 3})
    assert log.read()[0]["attempt_id"] == "d0"
