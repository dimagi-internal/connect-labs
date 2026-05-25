"""v4 ↔ v5 parity tests.

Runs v4's Python job handler and v5's JS compute helpers against the same
fixture, asserts byte-equal per-FLW summary output. Catches algorithmic
drift between the two implementations.

Skips silently if node isn't on PATH (CI without node won't run these but
won't fail either — local dev and the parity-CI workflow handle the actual
gate).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
RUN_V5_JS = REPO_ROOT / "commcare_connect" / "workflow" / "tests" / "mbw_v4_v5_parity" / "run_v5.js"
MOUNT_JS = REPO_ROOT / "commcare_connect" / "workflow" / "tests" / "mbw_v4_v5_parity" / "mount_v4_v5.js"
V4_RENDER = REPO_ROOT / "commcare_connect" / "workflow" / "templates" / "mbw_auditing_v4_render.js"
V5_RENDER = REPO_ROOT / "commcare_connect" / "workflow" / "templates" / "mbw_auditing_v5_render.js"
MOUNT_DEPS_DIR = Path("/tmp/v5-mount-test")


def _run_v4(fixture: dict, *, with_task_filters: bool) -> dict:
    from commcare_connect.workflow.job_handlers.mbw_auditing_v4 import handle_mbw_auditing_v4_job

    job_config = {
        "pipeline_data": {
            "visits": {"rows": fixture["visits"]},
            "visits_agg": {"rows": fixture["visits_agg"]},
            "registrations": {"rows": fixture["registrations"]},
            "gs_forms": {"rows": fixture["gs_forms"]},
        },
        "active_usernames": fixture["active_usernames"],
        "flw_names": fixture["flw_names"],
        "current_date": fixture["current_date"],
    }
    if with_task_filters and "task_filters" in fixture:
        job_config["task_filters"] = fixture["task_filters"]

    def _progress(msg, **_):
        pass

    result = handle_mbw_auditing_v4_job(job_config, access_token="", progress_callback=_progress)
    # Drop keys not part of the SQL-compute parity surface (v5 fetches these
    # from REST endpoints, not the compute helpers).
    result.pop("open_tasks", None)
    result.pop("open_tasks_debug", None)
    result.pop("prev_categories", None)
    return result


def _run_v5(fixture_dict: dict, *, with_task_filters: bool, tmp_path: Path) -> dict:
    # NaN is invalid JSON — encode as null so the v5 JS sees a missing value
    # (same semantics as v4's Python NaN handling, which short-circuits to None).
    def _scrub(obj):
        if isinstance(obj, list):
            return [_scrub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items()}
        if isinstance(obj, float) and obj != obj:  # NaN check
            return None
        return obj

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(_scrub(fixture_dict), default=str))
    args = ["node", str(RUN_V5_JS)]
    if with_task_filters:
        args.append("tab2")
    args.append(str(fixture_path))
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"v5 node runner failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def _assert_parity(v4_out: dict, v5_out: dict, label: str) -> None:
    v4_by_user = {s["username"]: s for s in v4_out.get("flw_summaries", [])}
    v5_by_user = {s["username"]: s for s in v5_out.get("flw_summaries", [])}

    diffs = []
    all_users = sorted(set(v4_by_user) | set(v5_by_user))
    for u in all_users:
        a = v4_by_user.get(u, {})
        b = v5_by_user.get(u, {})
        all_keys = sorted(set(a) | set(b))
        for k in all_keys:
            if a.get(k) != b.get(k):
                diffs.append(f"  {u}.{k}: v4={a.get(k)!r} v5={b.get(k)!r}")

    assert not diffs, f"v4↔v5 parity FAIL ({label}): {len(diffs)} diff(s):\n" + "\n".join(diffs)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_tab1_parity(tmp_path):
    """Tab 1: live follow-up rate, no task_filters."""
    from commcare_connect.workflow.tests.mbw_v4_v5_parity.fixture import build_fixture

    fx = build_fixture()
    v4_out = _run_v4(fx, with_task_filters=False)
    v5_out = _run_v5(fx, with_task_filters=False, tmp_path=tmp_path)
    _assert_parity(v4_out, v5_out, label="Tab 1")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_tab2_parity(tmp_path):
    """Tab 2: per-FLW baseline rate at trigger date (with task_filters)."""
    from commcare_connect.workflow.tests.mbw_v4_v5_parity.fixture import fixture_for_tab2

    fx = fixture_for_tab2()
    v4_out = _run_v4(fx, with_task_filters=True)
    v5_out = _run_v5(fx, with_task_filters=True, tmp_path=tmp_path)
    _assert_parity(v4_out, v5_out, label="Tab 2")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_edge_cases_parity(tmp_path):
    """Edge cases: zero-visit FLW, empty schedules, NaN GPS, bf_status
    tokenization, GS user_connect_id vs username fallback."""
    from commcare_connect.workflow.tests.mbw_v4_v5_parity.fixture import fixture_edge_cases

    fx = fixture_edge_cases()
    v4_out = _run_v4(fx, with_task_filters=False)
    v5_out = _run_v5(fx, with_task_filters=False, tmp_path=tmp_path)
    _assert_parity(v4_out, v5_out, label="edge cases")


def _mount_deps_available() -> bool:
    return (
        shutil.which("node") is not None
        and (MOUNT_DEPS_DIR / "node_modules" / "@babel" / "standalone").is_dir()
        and (MOUNT_DEPS_DIR / "node_modules" / "react").is_dir()
        and (MOUNT_DEPS_DIR / "node_modules" / "react-dom").is_dir()
    )


@pytest.mark.skipif(
    not _mount_deps_available(),
    reason=(
        "mount-test deps not installed; run `mkdir -p /tmp/v5-mount-test && cd "
        "/tmp/v5-mount-test && npm install --silent react@18 react-dom@18 "
        "@babel/standalone` to enable"
    ),
)
def test_v4_v5_mount_html_identical(tmp_path):
    """JSDOM-equivalent SSR mount of both v4 and v5 templates against the
    same fixture. Confirms the JSX layout, React mount flow, and view-helper
    plumbing all produce byte-identical initial HTML.

    Catches:
      - prop shape regressions (v5 destructures `view`)
      - React mount errors
      - JSX structural drift

    Companion to test_tab1/2_parity which verify the compute layer.
    """
    from commcare_connect.workflow.tests.mbw_v4_v5_parity.fixture import build_fixture

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(build_fixture(), default=str))

    # Node resolves require() against the script's location, not cwd, so we
    # point NODE_PATH at the install dir's node_modules so the script picks
    # up @babel/standalone, react, react-dom from there.
    import os as _os

    env = _os.environ.copy()
    env["NODE_PATH"] = str(MOUNT_DEPS_DIR / "node_modules")
    proc = subprocess.run(
        [
            "node",
            str(MOUNT_JS),
            str(V4_RENDER),
            str(V5_RENDER),
            str(fixture_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"v4↔v5 mount FAIL:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_tab2_edge_cases_parity(tmp_path):
    """Tab 2 edge cases: trigger date in the future, trigger date before any
    visits, ensuring baseline rate handles zero-denominator gracefully."""
    from commcare_connect.workflow.tests.mbw_v4_v5_parity.fixture import fixture_tab2_edge

    fx = fixture_tab2_edge()
    v4_out = _run_v4(fx, with_task_filters=True)
    v5_out = _run_v5(fx, with_task_filters=True, tmp_path=tmp_path)
    _assert_parity(v4_out, v5_out, label="Tab 2 edge cases")
