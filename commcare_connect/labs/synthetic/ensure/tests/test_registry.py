"""Env-template registry: discovery + safe resolution.

Pure load/validate — no DB. Mirrors the workflow template registry's
discover-on-load + accessor contract for the YAML env manifests under
``commcare_connect/labs/synthetic/envs/``.
"""

from __future__ import annotations

import pytest

from commcare_connect.labs.synthetic.ensure.registry import discover_envs, get_env, get_env_path, list_envs

PAR = "program-admin-report"


def test_discover_finds_par():
    envs = discover_envs()
    assert PAR in envs
    entry = envs[PAR]
    assert entry.key == PAR
    assert entry.path.name == f"{PAR}.yaml"
    assert entry.manifest.env == PAR


def test_get_env_returns_entry_with_resource_kinds():
    entry = get_env(PAR)
    kinds = [r.kind for r in entry.manifest.resources]
    assert kinds == ["opp_data", "opp_data", "weekly_runs", "run_audits", "tasks", "rollup"]
    # Both PAR opps surface in the summary.
    assert 10000 in entry.summary["opportunity_ids"]
    assert 10001 in entry.summary["opportunity_ids"]


def test_summary_shape():
    summary = get_env(PAR).summary
    assert summary["key"] == PAR
    assert summary["env"] == PAR
    assert summary["resource_count"] == 6
    assert summary["resource_kinds"][0] == "opp_data"


def test_list_envs_includes_par():
    keys = [e["key"] for e in list_envs()]
    assert PAR in keys


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", "does.not.exist"])
def test_bad_names_rejected_by_path_resolution(bad):
    with pytest.raises(ValueError):
        get_env_path(bad)


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", ""])
def test_get_env_rejects_unsafe_names(bad):
    with pytest.raises(ValueError):
        get_env(bad)


def test_get_env_unknown_name_raises():
    with pytest.raises(ValueError):
        get_env("no-such-env")
