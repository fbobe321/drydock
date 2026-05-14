"""Stats tool — distributions, hypothesis tests, descriptive statistics.

Sixth tool in the symbolic-math stack. HLE Physics, Bio/Medicine, and
some Math questions involve distributions, confidence intervals, and
p-values. Sympy doesn't do statistical inference; scipy.stats does it
correctly.

Backend: scipy.stats + Python's stdlib statistics.

Operations (`op=`):

  describe(data)              — mean, median, stdev, min, max, n
  pdf(dist, x, params)        — probability density at x
  cdf(dist, x, params)        — cumulative probability ≤ x
  sf(dist, x, params)         — survival function (1 - cdf)
  ppf(dist, q, params)        — inverse cdf (quantile)
  mean(dist, params)          — distribution mean
  variance(dist, params)      — distribution variance
  std(dist, params)           — distribution std deviation
  z_test(x_bar, mu0, sigma, n) — one-sample z-test; returns z and two-sided p
  t_test(data, mu0)           — one-sample t-test; returns t and two-sided p
  chi2_test(observed, expected) — Pearson χ²; returns chi2 and p
  correlation(xs, ys)         — Pearson correlation r and p
  ci_mean(data, alpha=0.05)   — confidence interval for the mean
  binomial(n, k, p)           — P(X=k) for X~Bin(n,p)
  poisson(k, lam)             — P(X=k) for X~Poisson(λ)

Supported `dist`: normal, t, chi2, f, binomial, poisson, exponential,
uniform, beta, gamma. Params is a comma-separated list matching scipy:
  normal: "mean, std"     (or omit for standard normal)
  t: "df"
  chi2: "df"
  binomial: "n, p"
  poisson: "mu" (=lambda)
  exponential: "scale" (mean = scale)
  uniform: "loc, scale"   (i.e. range [loc, loc+scale])

Numeric inputs: comma-separated floats, or expressions parsed by
sympy (so "pi/2", "1/3", "factorial(5)" all work).
"""
from __future__ import annotations

import ast as _ast
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, ClassVar, Literal, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from drydock.core.tools.ui import (
    ToolCallDisplay,
    ToolResultDisplay,
    ToolUIData,
)
from drydock.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from drydock.core.types import ToolResultEvent


StatsOp = Literal[
    "describe",
    "pdf", "cdf", "sf", "ppf",
    "mean", "variance", "std",
    "z_test", "t_test", "chi2_test",
    "correlation",
    "ci_mean",
    "binomial", "poisson",
]


class StatsArgs(BaseModel):
    op: StatsOp = Field(
        description=(
            "Operation: describe | pdf | cdf | sf | ppf | mean | variance | "
            "std | z_test | t_test | chi2_test | correlation | ci_mean | "
            "binomial | poisson"
        )
    )
    data: str = Field(
        default="",
        description="Comma-separated numeric sample for describe/t_test/correlation x/ci_mean.",
    )
    data2: str = Field(
        default="",
        description="Comma-separated second sample (correlation y, chi2_test expected).",
    )
    dist: str = Field(
        default="",
        description=(
            "Distribution name for pdf/cdf/sf/ppf/mean/variance/std: "
            "normal | t | chi2 | f | binomial | poisson | exponential | "
            "uniform | beta | gamma"
        ),
    )
    params: str = Field(
        default="",
        description="Comma-separated distribution parameters. See tool docstring.",
    )
    x: str = Field(
        default="",
        description="Value for pdf/cdf/sf (continuous) or k for binomial/poisson (discrete).",
    )
    q: str = Field(
        default="0.95",
        description="Quantile for ppf (default 0.95) or 1-alpha for ci_mean.",
    )
    n: str = Field(default="", description="Sample size (z_test) or binomial trials.")
    k: str = Field(default="", description="Successes (binomial), events (poisson).")
    p: str = Field(default="", description="Probability (binomial p, optional).")
    lam: str = Field(default="", description="Lambda for poisson.")
    mu0: str = Field(default="", description="Null hypothesis mean for z_test / t_test.")
    sigma: str = Field(default="", description="Known population stdev for z_test.")
    x_bar: str = Field(default="", description="Sample mean for z_test.")
    alpha: str = Field(default="0.05", description="Significance level for ci_mean (default 0.05).")


class StatsResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class StatsConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Numeric parsing ──────────────────────────────────────────────────

_BAD_PATTERNS = (
    "__", "import", "exec", "eval", "open",
    "globals", "locals", "getattr", "setattr",
)
_NUM_GLOBALS: dict[str, Any] = {}


def _num_globals() -> dict[str, Any]:
    global _NUM_GLOBALS
    if _NUM_GLOBALS:
        return _NUM_GLOBALS
    import math as _math
    import sympy
    _NUM_GLOBALS = {
        "__builtins__": {},
        "pi": _math.pi, "e": _math.e, "inf": _math.inf, "nan": _math.nan,
        "sqrt": _math.sqrt, "log": _math.log, "log2": _math.log2,
        "exp": _math.exp, "sin": _math.sin, "cos": _math.cos,
        "tan": _math.tan, "abs": abs, "min": min, "max": max,
        "factorial": _math.factorial, "comb": _math.comb,
        "Rational": sympy.Rational,
    }
    return _NUM_GLOBALS


def _parse_num(s: str, *, name: str = "value") -> float:
    """Parse one scalar number from a string expression."""
    s = s.strip()
    if not s:
        raise ToolError(f"{name} is empty")
    if len(s) > 300:
        raise ToolError(f"{name} too long")
    lower = s.lower()
    for bad in _BAD_PATTERNS:
        if bad in lower:
            raise ToolError(f"forbidden token in {name}: {bad!r}")
    try:
        tree = _ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"{name} SyntaxError: {e.msg}") from e
    safe_g = _num_globals()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute):
            raise ToolError(f"{name}: attribute access not permitted")
        if isinstance(node, _ast.Call):
            fn = getattr(node.func, "id", None)
            if fn is None or fn not in safe_g:
                raise ToolError(f"{name}: unknown function {fn!r}")
        if isinstance(node, _ast.Name):
            if node.id not in safe_g:
                raise ToolError(f"{name}: undefined name {node.id!r}")
    try:
        value = eval(  # noqa: S307 — whitelisted
            compile(tree, "<stats>", "eval"), safe_g, {}
        )
        return float(value)
    except (ValueError, TypeError, OverflowError) as e:
        raise ToolError(f"{name}: {type(e).__name__}: {e}") from e


def _parse_list(s: str, *, name: str = "data") -> list[float]:
    if not s.strip():
        raise ToolError(f"{name} is empty")
    out: list[float] = []
    for piece in s.split(","):
        piece = piece.strip()
        if not piece:
            continue
        out.append(_parse_num(piece, name=f"{name} element"))
    if len(out) > 100000:
        raise ToolError(f"{name}: too many elements ({len(out)}); cap 100000")
    if not out:
        raise ToolError(f"{name}: no numbers parsed")
    return out


def _fmt(v: float, *, sig: int = 6) -> str:
    if isinstance(v, bool):
        return str(v)
    return f"{v:.{sig}g}"


# ── Distribution dispatch ───────────────────────────────────────────

def _dist(name: str, params: str):
    """Return a scipy.stats frozen distribution by friendly name."""
    import scipy.stats as st
    name = (name or "").strip().lower()
    p = [_parse_num(x, name=f"{name} param") for x in params.split(",") if x.strip()]
    if name in ("normal", "gauss", "gaussian"):
        if not p:
            return st.norm()
        if len(p) == 1:
            return st.norm(loc=p[0])
        return st.norm(loc=p[0], scale=p[1])
    if name == "t":
        if not p:
            raise ToolError("t needs df param")
        return st.t(df=p[0])
    if name == "chi2":
        if not p:
            raise ToolError("chi2 needs df param")
        return st.chi2(df=p[0])
    if name == "f":
        if len(p) < 2:
            raise ToolError("f needs two df params")
        return st.f(dfn=p[0], dfd=p[1])
    if name in ("binomial", "binom"):
        if len(p) < 2:
            raise ToolError("binomial needs n and p")
        return st.binom(n=int(p[0]), p=p[1])
    if name == "poisson":
        if not p:
            raise ToolError("poisson needs mu (lambda)")
        return st.poisson(mu=p[0])
    if name in ("exponential", "expon"):
        scale = p[0] if p else 1.0
        return st.expon(scale=scale)
    if name == "uniform":
        if len(p) < 2:
            raise ToolError("uniform needs loc and scale")
        return st.uniform(loc=p[0], scale=p[1])
    if name == "beta":
        if len(p) < 2:
            raise ToolError("beta needs alpha and beta")
        return st.beta(a=p[0], b=p[1])
    if name == "gamma":
        if not p:
            raise ToolError("gamma needs shape (a)")
        return st.gamma(a=p[0])
    raise ToolError(f"unknown distribution: {name!r}")


# ── Op implementations ───────────────────────────────────────────────

def _op_describe(args: StatsArgs) -> tuple[str, str]:
    import statistics as st
    xs = _parse_list(args.data)
    n = len(xs)
    mean = st.mean(xs)
    median = st.median(xs)
    stdev = st.stdev(xs) if n > 1 else 0.0
    return (
        f"n={n} mean={_fmt(mean)} median={_fmt(median)} "
        f"stdev={_fmt(stdev)} min={_fmt(min(xs))} max={_fmt(max(xs))}",
        "summary",
    )


def _op_pdf(args: StatsArgs) -> tuple[str, str]:
    d = _dist(args.dist, args.params)
    x = _parse_num(args.x, name="x")
    pdf_fn = getattr(d, "pmf", None) or d.pdf
    return (_fmt(pdf_fn(x)), "float")


def _op_cdf(args: StatsArgs) -> tuple[str, str]:
    return (_fmt(_dist(args.dist, args.params).cdf(_parse_num(args.x, name="x"))), "float")


def _op_sf(args: StatsArgs) -> tuple[str, str]:
    return (_fmt(_dist(args.dist, args.params).sf(_parse_num(args.x, name="x"))), "float")


def _op_ppf(args: StatsArgs) -> tuple[str, str]:
    return (_fmt(_dist(args.dist, args.params).ppf(_parse_num(args.q, name="q"))), "float")


def _op_mean(args: StatsArgs) -> tuple[str, str]:
    return (_fmt(_dist(args.dist, args.params).mean()), "float")


def _op_variance(args: StatsArgs) -> tuple[str, str]:
    return (_fmt(_dist(args.dist, args.params).var()), "float")


def _op_std(args: StatsArgs) -> tuple[str, str]:
    return (_fmt(_dist(args.dist, args.params).std()), "float")


def _op_z_test(args: StatsArgs) -> tuple[str, str]:
    import scipy.stats as st
    x_bar = _parse_num(args.x_bar or args.x, name="x_bar")
    mu0 = _parse_num(args.mu0, name="mu0")
    sigma = _parse_num(args.sigma, name="sigma")
    n = _parse_num(args.n, name="n")
    if sigma <= 0 or n <= 0:
        raise ToolError("sigma and n must be positive")
    z = (x_bar - mu0) / (sigma / (n ** 0.5))
    p = 2 * st.norm.sf(abs(z))  # two-sided
    return (f"z={_fmt(z)}  p_two_sided={_fmt(p)}", "summary")


def _op_t_test(args: StatsArgs) -> tuple[str, str]:
    import scipy.stats as st
    xs = _parse_list(args.data)
    mu0 = _parse_num(args.mu0, name="mu0")
    res = st.ttest_1samp(xs, popmean=mu0)
    return (f"t={_fmt(res.statistic)}  p_two_sided={_fmt(res.pvalue)}  df={len(xs)-1}", "summary")


def _op_chi2_test(args: StatsArgs) -> tuple[str, str]:
    import scipy.stats as st
    observed = _parse_list(args.data, name="observed")
    expected = _parse_list(args.data2, name="expected") if args.data2 else None
    if expected is not None and len(observed) != len(expected):
        raise ToolError(f"length mismatch: |obs|={len(observed)} vs |exp|={len(expected)}")
    res = st.chisquare(f_obs=observed, f_exp=expected) if expected else st.chisquare(observed)
    df = len(observed) - 1
    return (f"chi2={_fmt(res.statistic)}  p={_fmt(res.pvalue)}  df={df}", "summary")


def _op_correlation(args: StatsArgs) -> tuple[str, str]:
    import scipy.stats as st
    xs = _parse_list(args.data, name="x")
    ys = _parse_list(args.data2, name="y")
    if len(xs) != len(ys):
        raise ToolError(f"length mismatch: |x|={len(xs)} vs |y|={len(ys)}")
    res = st.pearsonr(xs, ys)
    return (f"r={_fmt(res.statistic)}  p_two_sided={_fmt(res.pvalue)}", "summary")


def _op_ci_mean(args: StatsArgs) -> tuple[str, str]:
    import statistics
    import scipy.stats as st
    xs = _parse_list(args.data)
    n = len(xs)
    if n < 2:
        raise ToolError("ci_mean needs n ≥ 2")
    alpha = _parse_num(args.alpha or "0.05", name="alpha")
    if not (0 < alpha < 1):
        raise ToolError(f"alpha must be in (0,1), got {alpha}")
    mean = statistics.mean(xs)
    se = statistics.stdev(xs) / (n ** 0.5)
    t_crit = st.t.ppf(1 - alpha / 2, df=n - 1)
    return (
        f"mean={_fmt(mean)}  CI[{(1-alpha)*100:.0f}%]=[{_fmt(mean - t_crit*se)}, {_fmt(mean + t_crit*se)}]",
        "summary",
    )


def _op_binomial(args: StatsArgs) -> tuple[str, str]:
    import scipy.stats as st
    n = int(_parse_num(args.n, name="n"))
    k = int(_parse_num(args.k, name="k"))
    p = _parse_num(args.p, name="p")
    if not (0 <= p <= 1):
        raise ToolError(f"p must be in [0,1], got {p}")
    if k < 0 or k > n:
        raise ToolError(f"need 0 ≤ k ≤ n; got k={k}, n={n}")
    return (_fmt(st.binom.pmf(k, n, p)), "float")


def _op_poisson(args: StatsArgs) -> tuple[str, str]:
    import scipy.stats as st
    k = int(_parse_num(args.k, name="k"))
    lam = _parse_num(args.lam, name="lam")
    if lam <= 0 or k < 0:
        raise ToolError("need lam > 0 and k ≥ 0")
    return (_fmt(st.poisson.pmf(k, lam)), "float")


_DISPATCH = {
    "describe":    _op_describe,
    "pdf":         _op_pdf,
    "cdf":         _op_cdf,
    "sf":          _op_sf,
    "ppf":         _op_ppf,
    "mean":        _op_mean,
    "variance":    _op_variance,
    "std":         _op_std,
    "z_test":      _op_z_test,
    "t_test":      _op_t_test,
    "chi2_test":   _op_chi2_test,
    "correlation": _op_correlation,
    "ci_mean":     _op_ci_mean,
    "binomial":    _op_binomial,
    "poisson":     _op_poisson,
}


class Stats(
    BaseTool[StatsArgs, StatsResult, StatsConfig, BaseToolState],
    ToolUIData[StatsArgs, StatsResult],
):
    description: ClassVar[str] = (
        "Statistical operations via scipy.stats — descriptive stats, "
        "distributions (normal/t/chi2/f/binomial/poisson/exp/uniform/"
        "beta/gamma) with pdf/cdf/sf/ppf/mean/variance/std, hypothesis "
        "tests (z/t/chi²/Pearson correlation), confidence intervals. "
        "Use INSTEAD of computing binomial coefficients or normal-CDF "
        "by hand. Data inputs are comma-separated; numbers can be "
        "expressions like 'pi/2' or 'factorial(5)'."
    )

    @classmethod
    def format_call_display(cls, args: StatsArgs) -> ToolCallDisplay:
        pieces = []
        if args.dist:
            pieces.append(f"dist={args.dist}")
        for fld in ("data", "x", "n", "k", "p"):
            v = getattr(args, fld)
            if v:
                pieces.append(f"{fld}={v[:15]}")
        return ToolCallDisplay(
            summary=f"stats[{args.op}]: {' '.join(pieces)[:50]}"
        )

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, StatsResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"stats: {event.result.error[:80]}"
                )
            preview = event.result.result
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="stats complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing"

    def resolve_permission(self, args: StatsArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: StatsArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | StatsResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield StatsResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield StatsResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001
            yield StatsResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield StatsResult(ok=True, op=args.op, result=value, result_type=rtype)
