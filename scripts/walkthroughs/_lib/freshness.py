"""Deploy-freshness guard for the walkthrough recorders.

The problem this solves: after a labs deploy, ECS workers keep serving the
OLD code for a few minutes, and the synthetic seed self-heals a workflow
definition's ``render_code`` from whatever template the *running* worker
has. So a freshly-seeded run can serve stale JSX even though the deploy
"succeeded" — and the recorder then drives a UI that doesn't match the
scene script (missing buttons, renamed labels), failing in confusing ways.

Rather than remember to wait, the recorders call ``assert_page_current``
right after loading their first run page. It compares the ``render_code``
the labs server actually shipped to the browser (read out of the
``#workflow-data`` json_script blob) against the template in the local
checkout (AST-extracted, no Django needed). If they differ, it raises with
a clear message instead of recording stale UI.

Assumption: your local checkout == the revision you intend to be running
on labs. If you have uncommitted template edits, the guard will (correctly)
flag that the deployed UI doesn't match what you're about to record.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Map a workflow definition's template_type → the template source file whose
# RENDER_CODE constant should match what the server serves. Extend as new
# templates get walkthrough coverage.
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_SOURCES = {
    "chc_nutrition_analysis": REPO_ROOT / "commcare_connect/workflow/templates/chc_nutrition_analysis.py",
    "program_admin_report": REPO_ROOT / "commcare_connect/workflow/templates/program_admin_report.py",
}


def extract_local_render_code(template_path: Path) -> str:
    """Return the RENDER_CODE string constant from a template .py.

    AST-based so it works without importing the module (no Django setup,
    no settings, no GDAL). Raises if the constant isn't found.
    """
    tree = ast.parse(Path(template_path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "RENDER_CODE":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise ValueError(f"RENDER_CODE string constant not found in {template_path}")


def served_render_code(page) -> str | None:
    """Read the render_code the labs server shipped to this page.

    The run view injects ``workflow_data`` (which includes ``render_code``)
    via Django's ``json_script`` as ``<script id="workflow-data">``. Returns
    None if the blob isn't present (e.g. an error page or the run picker).
    """
    return page.evaluate(
        """() => {
            const el = document.getElementById('workflow-data');
            if (!el) return null;
            try {
                const data = JSON.parse(el.textContent);
                return data.render_code || null;
            } catch (e) {
                return null;
            }
        }"""
    )


def assert_page_current(page, template_type: str, *, label: str = "") -> None:
    """Raise unless the page is serving the local checkout's render_code.

    Call right after loading a run page. ``template_type`` selects which
    local template to compare against (see ``TEMPLATE_SOURCES``). A mismatch
    almost always means the labs deploy hasn't finished rolling out (workers
    still on old code) — or your local checkout is ahead of what's deployed.
    """
    src = TEMPLATE_SOURCES.get(template_type)
    if not src:
        # No known mapping — can't verify, so don't block. (Better to record
        # than to hard-fail on a template we simply don't track yet.)
        print(f"  ! freshness: no local template mapping for {template_type!r}; skipping check")
        return

    local = extract_local_render_code(src)
    served = served_render_code(page)
    if served is None:
        print(f"  ! freshness: no #workflow-data on page ({label}); skipping check")
        return

    if served.strip() != local.strip():
        raise RuntimeError(
            "DEPLOY FRESHNESS CHECK FAILED"
            + (f" [{label}]" if label else "")
            + f"\n  The labs server is serving a {template_type} render_code that does NOT match"
            + f"\n  your local checkout ({src.relative_to(REPO_ROOT)})."
            + f"\n  served={len(served)} bytes, local={len(local)} bytes."
            + "\n  Most likely the deploy is still rolling out (ECS workers cut over 2-4 min"
            + "\n  after the deploy job reports success). Re-seed + retry once it lands, or"
            + "\n  redeploy if your local checkout is ahead of what's on labs."
        )
    print(f"  ✓ freshness: {template_type} render_code matches local checkout ({label})")


def diff_summary(template_type: str, page) -> str:
    """Best-effort human-readable hint about where served vs local differ.

    Used in error reporting; finds the first line that differs so the
    operator can eyeball whether it's the change they expect to be live.
    """
    src = TEMPLATE_SOURCES.get(template_type)
    if not src:
        return "(no local template mapping)"
    local = extract_local_render_code(src).splitlines()
    served = (served_render_code(page) or "").splitlines()
    for i, (a, b) in enumerate(zip(local, served)):
        if a != b:
            return f"first diff at line {i}: local={a.strip()[:80]!r} served={b.strip()[:80]!r}"
    if len(local) != len(served):
        return f"length differs: local={len(local)} lines, served={len(served)} lines"
    return "(no line-level diff found)"


__all__ = ["assert_page_current", "extract_local_render_code", "served_render_code", "diff_summary"]
