"""Tests for drydock.comms — the attention policy is a PURE function, so 'did it
correctly stay silent?' is directly assertable (PRD §7/§24/§25)."""
from drydock.comms import (
    AttentionPolicy, AttentionConfig, Decision, Event, Presence, Severity,
)


def P(**kw):
    return AttentionPolicy(cfg=AttentionConfig(**kw)) if kw else AttentionPolicy()


def ev(t, **kw):
    return Event(type=t, **kw)


# ── routine noise stays quiet ────────────────────────────────────────────────
def test_routine_activity_logs_or_ignores():
    pol = P()
    assert pol.decide(ev("task.progress", task_id="a")) <= Decision.LOG
    assert pol.decide(ev("test.passed", task_id="a")) <= Decision.LOG
    assert pol.decide(ev("task.started", severity=Severity.INFORMATIONAL)) <= Decision.DISPLAY


def test_completion_surfaces_but_is_not_loud_when_away():
    # task.completed is worth a message when the user is away, a display when present
    pol = P()
    assert pol.decide(ev("task.completed", task_id="a"), Presence.ACTIVE) == Decision.DISPLAY


# ── INVARIANT: non-suppressible severities always reach the human ────────────
def test_approval_required_is_non_suppressible():
    pol = P()
    for presence in (Presence.UNKNOWN, Presence.AWAY, Presence.ACTIVE, Presence.DO_NOT_DISTURB):
        d = pol.decide(ev("decision.required", severity=Severity.APPROVAL_REQUIRED,
                          requires_response=True, task_id="x"), presence)
        assert d >= Decision.MESSAGE, (presence, d)


def test_blocking_and_security_never_ignored_even_under_cooldown():
    pol = P(cooldown_secs=9999)
    for _ in range(5):  # would be cooled down if suppressible
        d = pol.decide(ev("security.warning", severity=Severity.CRITICAL,
                          summary="credentials found in repo", task_id="x"))
        assert d >= Decision.MESSAGE
    d = pol.decide(ev("error.blocking", severity=Severity.BLOCKING, task_id="y"))
    assert d >= Decision.MESSAGE


def test_hourly_breaker_never_gags_a_critical_event():
    pol = P(max_notifies_per_hour=1)
    pol.decide(ev("task.completed", severity=Severity.ADVISORY, requires_response=True))
    # breaker is now tripped for loud channels, but a CRITICAL must still get through
    d = pol.decide(ev("security.warning", severity=Severity.CRITICAL, summary="leak"))
    assert d >= Decision.MESSAGE


# ── presence routing ─────────────────────────────────────────────────────────
def test_presence_routes_significant_events():
    pol = P()
    e = ev("decision.required", severity=Severity.APPROVAL_REQUIRED, requires_response=True, task_id="x")
    assert pol.decide(e, Presence.VOICE) == Decision.SPEAK
    assert P().decide(e, Presence.AWAY) == Decision.MESSAGE


# ── anti-annoyance: cooldown + dedup + aggregation ──────────────────────────
def test_single_test_failure_stays_quiet():
    # a lone test.failed during the agent's own work is routine — it must NOT
    # interrupt (the agent fixes it); only a stuck loop escalates.
    pol = P()
    assert pol.decide(ev("test.failed", severity=Severity.ADVISORY,
                         summary="auth tests failing", task_id="a"), Presence.AWAY) <= Decision.LOG


def test_repeated_notify_worthy_event_is_deduped_then_aggregated_once():
    # agent.stuck IS notify-worthy (score 3). Repeated within cooldown → the first
    # notifies, the rest are suppressed to LOG, and exactly one aggregated notice
    # fires when repetition crosses the escalation threshold (PRD §25).
    pol = P(cooldown_secs=9999, escalate_after=4)
    e = ev("agent.stuck", severity=Severity.ADVISORY, summary="stuck on dep", task_id="a")
    first = pol.decide(e, Presence.AWAY)
    assert first >= Decision.MESSAGE               # first one gets through
    seen = [pol.decide(e, Presence.AWAY) for _ in range(5)]
    assert Decision.LOG in seen                    # dups suppressed inside cooldown
    assert sum(1 for d in seen if d >= Decision.MESSAGE) == 1, "escalate exactly once"


def test_non_suppressible_is_not_deduped_away():
    pol = P(cooldown_secs=9999)
    e = ev("permission.required", severity=Severity.APPROVAL_REQUIRED,
           summary="delete credentials from git history?", requires_response=True, task_id="x")
    decisions = [pol.decide(e) for _ in range(3)]
    assert all(d >= Decision.MESSAGE for d in decisions), decisions


# ── faithful wording for approvals ──────────────────────────────────────────
def test_approval_summary_delivered_verbatim():
    from drydock.comms.channels import format_for_human
    e = ev("permission.required", severity=Severity.APPROVAL_REQUIRED,
           summary="delete these credentials from git history?", requires_response=True,
           task_id="repo")
    out = format_for_human(e)
    assert "delete these credentials from git history?" in out
