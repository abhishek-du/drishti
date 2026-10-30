"""Guards owned by Track A (docs/TRACKS.md)."""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "drishti"


def _imports(p: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in n.names}
    return out


def test_chart_stubs_never_leave_the_api_package():
    """Track C's temporary stubs (drishti/api/chart_stubs.py) must never reach strategies,
    replay, ml, features or any other non-API code: one implementation of every indicator."""
    bad = sorted(str(p.relative_to(PKG)) for p in PKG.rglob("*.py")
                 if "api" not in p.relative_to(PKG).parts[:1]
                 and any("chart_stubs" in i for i in _imports(p)))
    assert bad == [], f"chart_stubs imported outside drishti/api: {bad}"


def test_nothing_outside_the_api_imports_the_api():
    """drishti/api holds display-only code that may use later bars (chart_api.hindsight) and
    interim stubs; features, strategies, replay, ml, judge, data, run and scout must never
    import it."""
    bad = sorted(str(p.relative_to(PKG)) for p in PKG.rglob("*.py")
                 if "api" not in p.relative_to(PKG).parts[:1]
                 and any(i == "drishti.api" or i.startswith("drishti.api.") for i in _imports(p)))
    assert bad == [], f"drishti.api imported outside drishti/api: {bad}"
