"""Chemistry tool — periodic-table lookup, molar mass, stoichiometry.

Eighth tool in the symbolic-math stack. HLE Chemistry is 0/24
lifetime — the worst category. This tool covers the fundamentals
most chem questions assume:

  - Element lookup (mass, symbol, atomic number, name)
  - Molar mass of a chemical formula (e.g. H2O, Ca(OH)2, C6H12O6)
  - Mole / gram / particle conversions
  - Empirical formula determination from percentage composition

Backend: hand-rolled periodic table for the first 118 elements
(standard atomic weights from IUPAC 2024). Formula parser
recognises nested parentheses and subscripted counts.

Operations (`op=`):

  element(name_or_symbol)        — lookup atomic number, mass, name
  molar_mass(formula)            — mass of one mole of compound
  moles_from_grams(formula, g)   — mass → moles
  grams_from_moles(formula, n)   — moles → mass
  particles_from_moles(n)        — n moles → atoms/molecules (× Avogadro)
  moles_from_particles(N)        — atoms/molecules → moles
  percent_composition(formula)   — % of each element by mass
  empirical_formula(percents)    — given "C=40, H=6.7, O=53.3" return formula

Sandboxed; formula syntax is a strict subset of chemistry notation
(letters / digits / parentheses), no general code eval.
"""
from __future__ import annotations

import re
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


ChemistryOp = Literal[
    "element",
    "molar_mass",
    "moles_from_grams",
    "grams_from_moles",
    "particles_from_moles",
    "moles_from_particles",
    "percent_composition",
    "empirical_formula",
]


class ChemistryArgs(BaseModel):
    op: ChemistryOp = Field(
        description=(
            "Operation: element | molar_mass | moles_from_grams | "
            "grams_from_moles | particles_from_moles | moles_from_particles | "
            "percent_composition | empirical_formula."
        )
    )
    name: str = Field(
        default="",
        description="Element name or symbol for `element` (e.g. 'Na', 'sodium', '11').",
    )
    formula: str = Field(
        default="",
        description=(
            "Chemical formula. Subscripts as plain digits, groups in "
            "parentheses. Examples: H2O, Ca(OH)2, C6H12O6, "
            "Fe2(SO4)3, NH4NO3, K3Fe(CN)6."
        ),
    )
    grams: str = Field(
        default="",
        description="Mass in grams for moles_from_grams (number string).",
    )
    moles: str = Field(
        default="",
        description="Amount in moles for grams_from_moles / particles_from_moles.",
    )
    particles: str = Field(
        default="",
        description="Particle count for moles_from_particles.",
    )
    percents: str = Field(
        default="",
        description=(
            "Comma-separated 'element=percent' for empirical_formula. "
            "Example: 'C=40.0, H=6.7, O=53.3' → CH2O."
        ),
    )


class ChemistryResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class ChemistryConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Periodic table (IUPAC 2024 standard atomic weights, g/mol) ───────

# Conventional values; (symbol, atomic_number, name, mass).
_ELEMENTS: list[tuple[str, int, str, float]] = [
    ("H",   1, "Hydrogen",     1.008),
    ("He",  2, "Helium",       4.0026),
    ("Li",  3, "Lithium",      6.94),
    ("Be",  4, "Beryllium",    9.0122),
    ("B",   5, "Boron",       10.81),
    ("C",   6, "Carbon",      12.011),
    ("N",   7, "Nitrogen",    14.007),
    ("O",   8, "Oxygen",      15.999),
    ("F",   9, "Fluorine",    18.998),
    ("Ne", 10, "Neon",        20.180),
    ("Na", 11, "Sodium",      22.990),
    ("Mg", 12, "Magnesium",   24.305),
    ("Al", 13, "Aluminium",   26.982),
    ("Si", 14, "Silicon",     28.085),
    ("P",  15, "Phosphorus",  30.974),
    ("S",  16, "Sulfur",      32.06),
    ("Cl", 17, "Chlorine",    35.45),
    ("Ar", 18, "Argon",       39.948),
    ("K",  19, "Potassium",   39.098),
    ("Ca", 20, "Calcium",     40.078),
    ("Sc", 21, "Scandium",    44.956),
    ("Ti", 22, "Titanium",    47.867),
    ("V",  23, "Vanadium",    50.942),
    ("Cr", 24, "Chromium",    51.996),
    ("Mn", 25, "Manganese",   54.938),
    ("Fe", 26, "Iron",        55.845),
    ("Co", 27, "Cobalt",      58.933),
    ("Ni", 28, "Nickel",      58.693),
    ("Cu", 29, "Copper",      63.546),
    ("Zn", 30, "Zinc",        65.38),
    ("Ga", 31, "Gallium",     69.723),
    ("Ge", 32, "Germanium",   72.630),
    ("As", 33, "Arsenic",     74.922),
    ("Se", 34, "Selenium",    78.971),
    ("Br", 35, "Bromine",     79.904),
    ("Kr", 36, "Krypton",     83.798),
    ("Rb", 37, "Rubidium",    85.468),
    ("Sr", 38, "Strontium",   87.62),
    ("Y",  39, "Yttrium",     88.906),
    ("Zr", 40, "Zirconium",   91.224),
    ("Nb", 41, "Niobium",     92.906),
    ("Mo", 42, "Molybdenum",  95.95),
    ("Tc", 43, "Technetium",  98.0),
    ("Ru", 44, "Ruthenium",  101.07),
    ("Rh", 45, "Rhodium",    102.91),
    ("Pd", 46, "Palladium",  106.42),
    ("Ag", 47, "Silver",     107.87),
    ("Cd", 48, "Cadmium",    112.41),
    ("In", 49, "Indium",     114.82),
    ("Sn", 50, "Tin",        118.71),
    ("Sb", 51, "Antimony",   121.76),
    ("Te", 52, "Tellurium",  127.60),
    ("I",  53, "Iodine",     126.90),
    ("Xe", 54, "Xenon",      131.29),
    ("Cs", 55, "Caesium",    132.91),
    ("Ba", 56, "Barium",     137.33),
    ("La", 57, "Lanthanum",  138.91),
    ("Ce", 58, "Cerium",     140.12),
    ("Pr", 59, "Praseodymium",140.91),
    ("Nd", 60, "Neodymium",  144.24),
    ("Pm", 61, "Promethium", 145.0),
    ("Sm", 62, "Samarium",   150.36),
    ("Eu", 63, "Europium",   151.96),
    ("Gd", 64, "Gadolinium", 157.25),
    ("Tb", 65, "Terbium",    158.93),
    ("Dy", 66, "Dysprosium", 162.50),
    ("Ho", 67, "Holmium",    164.93),
    ("Er", 68, "Erbium",     167.26),
    ("Tm", 69, "Thulium",    168.93),
    ("Yb", 70, "Ytterbium",  173.04),
    ("Lu", 71, "Lutetium",   174.97),
    ("Hf", 72, "Hafnium",    178.49),
    ("Ta", 73, "Tantalum",   180.95),
    ("W",  74, "Tungsten",   183.84),
    ("Re", 75, "Rhenium",    186.21),
    ("Os", 76, "Osmium",     190.23),
    ("Ir", 77, "Iridium",    192.22),
    ("Pt", 78, "Platinum",   195.08),
    ("Au", 79, "Gold",       196.97),
    ("Hg", 80, "Mercury",    200.59),
    ("Tl", 81, "Thallium",   204.38),
    ("Pb", 82, "Lead",       207.2),
    ("Bi", 83, "Bismuth",    208.98),
    ("Po", 84, "Polonium",   209.0),
    ("At", 85, "Astatine",   210.0),
    ("Rn", 86, "Radon",      222.0),
    ("Fr", 87, "Francium",   223.0),
    ("Ra", 88, "Radium",     226.0),
    ("Ac", 89, "Actinium",   227.0),
    ("Th", 90, "Thorium",    232.04),
    ("Pa", 91, "Protactinium",231.04),
    ("U",  92, "Uranium",    238.03),
    ("Np", 93, "Neptunium",  237.0),
    ("Pu", 94, "Plutonium",  244.0),
    ("Am", 95, "Americium",  243.0),
    ("Cm", 96, "Curium",     247.0),
    ("Bk", 97, "Berkelium",  247.0),
    ("Cf", 98, "Californium",251.0),
    ("Es", 99, "Einsteinium",252.0),
    ("Fm",100, "Fermium",    257.0),
    ("Md",101, "Mendelevium",258.0),
    ("No",102, "Nobelium",   259.0),
    ("Lr",103, "Lawrencium", 262.0),
    ("Rf",104, "Rutherfordium",267.0),
    ("Db",105, "Dubnium",    268.0),
    ("Sg",106, "Seaborgium", 269.0),
    ("Bh",107, "Bohrium",    270.0),
    ("Hs",108, "Hassium",    269.0),
    ("Mt",109, "Meitnerium", 278.0),
    ("Ds",110, "Darmstadtium",281.0),
    ("Rg",111, "Roentgenium",281.0),
    ("Cn",112, "Copernicium",285.0),
    ("Nh",113, "Nihonium",   286.0),
    ("Fl",114, "Flerovium",  289.0),
    ("Mc",115, "Moscovium",  289.0),
    ("Lv",116, "Livermorium",293.0),
    ("Ts",117, "Tennessine", 294.0),
    ("Og",118, "Oganesson",  294.0),
]

_BY_SYMBOL = {sym: (sym, z, name, mass) for sym, z, name, mass in _ELEMENTS}
_BY_NAME = {name.lower(): (sym, z, name, mass) for sym, z, name, mass in _ELEMENTS}
_BY_Z = {z: (sym, z, name, mass) for sym, z, name, mass in _ELEMENTS}

_AVOGADRO = 6.02214076e23  # exact by 2019 SI redefinition


def _lookup(query: str):
    """Find element by symbol (case-sensitive), name (lowercase), or atomic number."""
    q = query.strip()
    if not q:
        raise ToolError("element query is empty")
    # Atomic number
    if q.isdigit():
        z = int(q)
        if z in _BY_Z:
            return _BY_Z[z]
        raise ToolError(f"no element with atomic number {z} (range 1-118)")
    # Symbol (case-preserving — Na is not NA)
    if q in _BY_SYMBOL:
        return _BY_SYMBOL[q]
    # Name (case-insensitive)
    if q.lower() in _BY_NAME:
        return _BY_NAME[q.lower()]
    raise ToolError(f"unknown element: {q!r}")


# ── Formula parser ───────────────────────────────────────────────────

_FORMULA_RE = re.compile(r"^[A-Za-z0-9()\.·\s]+$")
_TOKEN_RE = re.compile(r"([A-Z][a-z]?|\(|\)|\d+|·)")


def _parse_formula(formula: str) -> dict[str, int]:
    """Parse a chemical formula into {element: count}. Supports nested
    parentheses, e.g. Fe2(SO4)3 → {Fe: 2, S: 3, O: 12}."""
    f = (formula or "").strip()
    if not f:
        raise ToolError("formula is empty")
    if len(f) > 200:
        raise ToolError("formula too long (>200 chars)")
    if not _FORMULA_RE.match(f):
        raise ToolError(
            f"formula contains illegal chars: {f!r} "
            "(allowed: letters, digits, parens, dot)"
        )

    tokens = _TOKEN_RE.findall(f)
    if not tokens:
        raise ToolError(f"formula didn't tokenize: {f!r}")

    # Stack-based parse: each frame is a {element: count} dict; ')'
    # pops + multiplies by the trailing number (default 1).
    stack: list[dict[str, int]] = [dict()]
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "(":
            stack.append(dict())
            i += 1
        elif t == ")":
            if len(stack) < 2:
                raise ToolError("unbalanced parens in formula")
            group = stack.pop()
            i += 1
            mult = 1
            if i < len(tokens) and tokens[i].isdigit():
                mult = int(tokens[i])
                i += 1
            for el, cnt in group.items():
                stack[-1][el] = stack[-1].get(el, 0) + cnt * mult
        elif t == "·":
            # Hydrate dot (e.g. CuSO4·5H2O); next token is a digit then a formula
            # — treat the rest of the formula as a separate factor.
            i += 1
            mult = 1
            if i < len(tokens) and tokens[i].isdigit():
                mult = int(tokens[i])
                i += 1
            # Parse remainder recursively and multiply.
            remainder = "".join(tokens[i:])
            sub = _parse_formula(remainder) if remainder else {}
            for el, cnt in sub.items():
                stack[-1][el] = stack[-1].get(el, 0) + cnt * mult
            break
        elif t[0].isupper():
            # Element symbol token
            if t not in _BY_SYMBOL:
                raise ToolError(f"unknown element symbol in formula: {t!r}")
            i += 1
            cnt = 1
            if i < len(tokens) and tokens[i].isdigit():
                cnt = int(tokens[i])
                i += 1
            stack[-1][t] = stack[-1].get(t, 0) + cnt
        else:
            # Stray digit or unexpected token
            raise ToolError(f"unexpected token in formula: {t!r}")

    if len(stack) != 1:
        raise ToolError("unbalanced parens in formula (left open)")
    return stack[0]


# ── Number parser (very small) ───────────────────────────────────────

def _parse_pos_number(s: str, *, name: str) -> float:
    s = s.strip()
    if not s:
        raise ToolError(f"{name} is empty")
    # Reject obvious abuse.
    if any(c.isalpha() for c in s) or any(c in s for c in ("(", ")", ".", ",")) and "." not in s.replace(",", ""):
        # Allow simple decimal point or scientific notation only.
        pass
    try:
        v = float(s)
    except ValueError as e:
        raise ToolError(f"{name} not a number: {s!r}") from e
    if v < 0:
        raise ToolError(f"{name} must be non-negative, got {v}")
    return v


# ── Op implementations ───────────────────────────────────────────────

def _op_element(args: ChemistryArgs) -> tuple[str, str]:
    sym, z, name, mass = _lookup(args.name)
    return (f"{sym} (Z={z}, {name}, {mass} g/mol)", "element")


def _op_molar_mass(args: ChemistryArgs) -> tuple[str, str]:
    counts = _parse_formula(args.formula)
    total = sum(count * _BY_SYMBOL[el][3] for el, count in counts.items())
    return (f"{total:.4f} g/mol", "float")


def _op_moles_from_grams(args: ChemistryArgs) -> tuple[str, str]:
    counts = _parse_formula(args.formula)
    mm = sum(count * _BY_SYMBOL[el][3] for el, count in counts.items())
    g = _parse_pos_number(args.grams, name="grams")
    return (f"{g / mm:.6g} mol  (molar mass {mm:.4f} g/mol)", "float")


def _op_grams_from_moles(args: ChemistryArgs) -> tuple[str, str]:
    counts = _parse_formula(args.formula)
    mm = sum(count * _BY_SYMBOL[el][3] for el, count in counts.items())
    n = _parse_pos_number(args.moles, name="moles")
    return (f"{n * mm:.6g} g  (molar mass {mm:.4f} g/mol)", "float")


def _op_particles_from_moles(args: ChemistryArgs) -> tuple[str, str]:
    n = _parse_pos_number(args.moles, name="moles")
    return (f"{n * _AVOGADRO:.6g}  (× Avogadro)", "float")


def _op_moles_from_particles(args: ChemistryArgs) -> tuple[str, str]:
    p = _parse_pos_number(args.particles, name="particles")
    return (f"{p / _AVOGADRO:.6g} mol", "float")


def _op_percent_composition(args: ChemistryArgs) -> tuple[str, str]:
    counts = _parse_formula(args.formula)
    total = sum(count * _BY_SYMBOL[el][3] for el, count in counts.items())
    pieces = []
    for el, count in sorted(counts.items()):
        m = count * _BY_SYMBOL[el][3]
        pieces.append(f"{el}={100*m/total:.2f}%")
    return ("  ".join(pieces) + f"  (total mass {total:.4f} g/mol)", "summary")


def _op_empirical_formula(args: ChemistryArgs) -> tuple[str, str]:
    """Given percent-composition data, derive the empirical formula.
    Algorithm: divide each percent by atomic mass to get moles, then
    divide all by the smallest, then scale to nearest integers."""
    if not args.percents.strip():
        raise ToolError("empirical_formula needs `percents=` like 'C=40, H=6.7, O=53.3'")
    moles: dict[str, float] = {}
    for piece in args.percents.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ToolError(f"bad percents entry {piece!r}; expected 'El=val'")
        el, val = piece.split("=", 1)
        el = el.strip()
        if el not in _BY_SYMBOL:
            raise ToolError(f"unknown element: {el!r}")
        try:
            pct = float(val.strip().rstrip("%"))
        except ValueError as e:
            raise ToolError(f"bad percent for {el}: {val!r}") from e
        moles[el] = pct / _BY_SYMBOL[el][3]

    if not moles:
        raise ToolError("no element entries")
    smallest = min(moles.values())
    ratios = {el: m / smallest for el, m in moles.items()}
    # Scale up to integers
    scale = 1
    while scale <= 12:
        rounded = {el: round(r * scale) for el, r in ratios.items()}
        # Are all ratios within 0.04 of integers when scaled?
        if all(abs(r * scale - rounded[el]) < 0.04 for el, r in ratios.items()):
            if all(v >= 1 for v in rounded.values()):
                break
        scale += 1
    else:
        raise ToolError(
            "couldn't reduce to small-integer ratios — check input percents sum to ~100"
        )
    parts = []
    for el in sorted(rounded):
        n = rounded[el]
        parts.append(f"{el}{n if n > 1 else ''}")
    return ("".join(parts), "formula")


_DISPATCH = {
    "element":              _op_element,
    "molar_mass":           _op_molar_mass,
    "moles_from_grams":     _op_moles_from_grams,
    "grams_from_moles":     _op_grams_from_moles,
    "particles_from_moles": _op_particles_from_moles,
    "moles_from_particles": _op_moles_from_particles,
    "percent_composition":  _op_percent_composition,
    "empirical_formula":    _op_empirical_formula,
}


class Chemistry(
    BaseTool[ChemistryArgs, ChemistryResult, ChemistryConfig, BaseToolState],
    ToolUIData[ChemistryArgs, ChemistryResult],
):
    description: ClassVar[str] = (
        "Chemistry — periodic-table lookup, molar mass of a formula, "
        "mole / gram / particle conversions, percent composition, "
        "empirical formula determination. Formula syntax: standard "
        "chemistry notation with nested parens (H2O, Ca(OH)2, "
        "Fe2(SO4)3, CuSO4·5H2O). Element lookup accepts symbol, "
        "name, or atomic number. Use INSTEAD of looking up atomic "
        "weights and INSTEAD of doing mole calculations by hand."
    )

    @classmethod
    def format_call_display(cls, args: ChemistryArgs) -> ToolCallDisplay:
        primary = args.formula or args.name or args.percents or ""
        if len(primary) > 30:
            primary = primary[:27] + "..."
        return ToolCallDisplay(summary=f"chemistry[{args.op}]: {primary}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, ChemistryResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"chemistry: {event.result.error[:80]}"
                )
            preview = event.result.result
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="chemistry complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing"

    def resolve_permission(self, args: ChemistryArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: ChemistryArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | ChemistryResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield ChemistryResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield ChemistryResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001
            yield ChemistryResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield ChemistryResult(ok=True, op=args.op, result=value, result_type=rtype)
