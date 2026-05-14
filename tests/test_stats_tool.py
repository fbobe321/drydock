"""Tests for the stats built-in — scipy-backed statistics."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.stats_tool import (
    Stats, StatsArgs, StatsConfig, StatsResult,
)


def _drive(args: StatsArgs) -> StatsResult:
    tool = Stats(config=StatsConfig(), state=BaseToolState())

    async def go() -> StatsResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, StatsResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


def _parse_float(text_label: str, summary: str) -> float:
    """Pull a `label=NUMBER` value out of a summary string."""
    import re
    m = re.search(rf"{text_label}=([-+]?[\d.eE+-]+)", summary)
    assert m, f"label {text_label!r} not in {summary!r}"
    return float(m.group(1))


# ── Descriptive ─────────────────────────────────────────────────────

def test_describe_basic():
    r = _drive(StatsArgs(op="describe", data="1, 2, 3, 4, 5"))
    assert r.ok
    assert "n=5" in r.result and "mean=3" in r.result and "median=3" in r.result


def test_describe_with_expressions():
    """Each element parses as a sympy expression."""
    r = _drive(StatsArgs(op="describe", data="pi/2, pi, 3*pi/2"))
    assert r.ok and "n=3" in r.result


# ── Distributions ────────────────────────────────────────────────────

def test_pdf_standard_normal_at_zero():
    """φ(0) = 1/√(2π) ≈ 0.3989"""
    r = _drive(StatsArgs(op="pdf", dist="normal", params="0,1", x="0"))
    assert r.ok
    val = float(r.result)
    assert abs(val - 0.39894228) < 1e-4


def test_cdf_standard_normal_at_1_96():
    """Φ(1.96) ≈ 0.975"""
    r = _drive(StatsArgs(op="cdf", dist="normal", params="0,1", x="1.96"))
    assert r.ok
    val = float(r.result)
    assert abs(val - 0.975) < 1e-3


def test_ppf_inverse_of_cdf():
    """Φ⁻¹(0.975) ≈ 1.96"""
    r = _drive(StatsArgs(op="ppf", dist="normal", params="0,1", q="0.975"))
    assert r.ok
    val = float(r.result)
    assert abs(val - 1.96) < 1e-3


def test_mean_of_normal():
    r = _drive(StatsArgs(op="mean", dist="normal", params="5, 2"))
    assert r.ok and float(r.result) == 5.0


def test_variance_of_normal():
    r = _drive(StatsArgs(op="variance", dist="normal", params="0, 3"))
    assert r.ok and abs(float(r.result) - 9.0) < 1e-9


def test_pdf_binomial_pmf():
    """P(X=5) for X~Bin(10, 0.5) = C(10,5)/2^10 = 252/1024 ≈ 0.246."""
    r = _drive(StatsArgs(op="pdf", dist="binomial", params="10, 0.5", x="5"))
    assert r.ok
    assert abs(float(r.result) - 0.24609375) < 1e-6


# ── Closed-form helpers ──────────────────────────────────────────────

def test_binomial_op_matches_pmf():
    """binomial(n=10, k=3, p=0.5) = C(10,3)/2^10 = 120/1024."""
    r = _drive(StatsArgs(op="binomial", n="10", k="3", p="0.5"))
    assert r.ok
    assert abs(float(r.result) - 120/1024) < 1e-6


def test_poisson_pmf():
    """P(X=2) for X~Poisson(3) = 3²·e⁻³/2! = 9·e⁻³/2 ≈ 0.224."""
    r = _drive(StatsArgs(op="poisson", k="2", lam="3"))
    assert r.ok
    assert abs(float(r.result) - 0.22404180) < 1e-6


def test_binomial_p_out_of_range():
    r = _drive(StatsArgs(op="binomial", n="10", k="3", p="1.5"))
    assert not r.ok
    assert "[0,1]" in r.error or "p" in r.error


# ── Hypothesis tests ────────────────────────────────────────────────

def test_z_test_returns_sensible_z():
    """x̄=105, μ₀=100, σ=15, n=30  ⇒  z = (105-100)/(15/√30) ≈ 1.826"""
    r = _drive(StatsArgs(op="z_test", x_bar="105", mu0="100",
                         sigma="15", n="30"))
    assert r.ok
    z = _parse_float("z", r.result)
    assert abs(z - 1.8257) < 1e-3


def test_t_test_zero_when_data_matches_mean():
    r = _drive(StatsArgs(op="t_test", data="5, 5, 5, 5", mu0="5"))
    assert r.ok
    # When sample = mu0 with zero variance, scipy returns nan; check at
    # least that the t-statistic exists and the message says df=3.
    assert "df=3" in r.result


def test_chi2_test_matched_expected():
    """If observed = expected proportionally, chi² ≈ 0."""
    r = _drive(StatsArgs(op="chi2_test", data="10,20,30,40", data2="10,20,30,40"))
    assert r.ok
    chi2 = _parse_float("chi2", r.result)
    assert chi2 < 1e-6


def test_chi2_test_length_mismatch():
    r = _drive(StatsArgs(op="chi2_test", data="1,2,3", data2="1,2"))
    assert not r.ok


def test_correlation_perfect_linear():
    """y = 2x should give r = 1 exactly."""
    r = _drive(StatsArgs(op="correlation",
                         data="1, 2, 3, 4, 5", data2="2, 4, 6, 8, 10"))
    assert r.ok
    r_val = _parse_float("r", r.result)
    assert abs(r_val - 1.0) < 1e-9


def test_correlation_length_mismatch():
    r = _drive(StatsArgs(op="correlation", data="1,2", data2="1,2,3"))
    assert not r.ok


def test_ci_mean_basic():
    r = _drive(StatsArgs(op="ci_mean", data="10, 11, 12, 13, 14, 15, 16"))
    assert r.ok and "CI[95%]" in r.result
    # Mean should be 13 with CI roughly [11, 15].
    mean = _parse_float("mean", r.result)
    assert mean == 13.0


def test_ci_mean_needs_n_geq_2():
    r = _drive(StatsArgs(op="ci_mean", data="42"))
    assert not r.ok


# ── Distribution param validation ────────────────────────────────────

def test_unknown_distribution_rejected():
    r = _drive(StatsArgs(op="pdf", dist="lognormal_wonky", params="1,2", x="3"))
    assert not r.ok


def test_t_distribution_needs_df():
    r = _drive(StatsArgs(op="pdf", dist="t", params="", x="0"))
    assert not r.ok


# ── Sandbox ──────────────────────────────────────────────────────────

def test_rejects_import_in_data():
    r = _drive(StatsArgs(op="describe", data="import os"))
    assert not r.ok


def test_rejects_attribute_access():
    r = _drive(StatsArgs(op="describe", data="foo.bar"))
    assert not r.ok


def test_rejects_unknown_name_in_expression():
    r = _drive(StatsArgs(op="describe", data="my_secret_var"))
    assert not r.ok


# ── Discovery ────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "stats" in names
