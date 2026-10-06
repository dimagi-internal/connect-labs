"""tools/promote_template_workflow.py: the way back from a template workflow to code.

The merge is three-way -- seed, template version, repo file -- so whatever the
code template gained after the seed survives, and only the template's own changes
land. That is what makes promotion safe to run on a code template that has moved on
(the generic indicator renders gained worker names between a demo's seed and its
fixes)."""

from __future__ import annotations

import json

import pytest

from tools import promote_template_workflow as promote

SEED = "line one\nline two\nline three\nline four\nline five\n"


def _export(code):
    return {
        "template_workflow_id": 7,
        "name": "T",
        "template_type": "t",
        "target": "v3",
        "base": {"version": 1},
        "code_template": {"path": "render.js", "deployed_equals_base": False},
        "changes": [{"version": 2, "note": "fix", "restores_version": None}],
        "base_code": SEED,
        "code": code,
    }


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(promote, "REPO", tmp_path)
    return tmp_path


def test_the_templates_changes_land_and_the_repos_own_are_kept(repo, capsys):
    (repo / "render.js").write_text(SEED.replace("line one", "line one, renamed on main"))
    conflicts = promote.merge(_export(SEED.replace("line five", "line five, fixed in the demo")))
    assert conflicts == 0
    assert (repo / "render.js").read_text() == (
        "line one, renamed on main\nline two\nline three\nline four\nline five, fixed in the demo\n"
    )
    assert "v2: fix" in capsys.readouterr().out


def test_a_clash_is_left_as_markers_and_reported(repo):
    (repo / "render.js").write_text(SEED.replace("line three", "main's three"))
    assert promote.merge(_export(SEED.replace("line three", "the demo's three"))) == 1
    assert "<<<<<<< repo" in (repo / "render.js").read_text()


def test_a_dry_run_leaves_the_file_alone(repo):
    (repo / "render.js").write_text(SEED)
    promote.merge(_export(SEED.replace("two", "2")), dry_run=True)
    assert (repo / "render.js").read_text() == SEED


def test_a_saved_tool_result_is_merged_with_no_network(repo, tmp_path):
    (repo / "render.js").write_text(SEED)
    saved = tmp_path / "export.json"
    saved.write_text(json.dumps(_export(SEED.replace("four", "4"))))
    assert promote.main(["--from-json", str(saved)]) == 0
    assert "line 4" in (repo / "render.js").read_text()
