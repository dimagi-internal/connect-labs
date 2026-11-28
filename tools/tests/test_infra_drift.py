"""infra_drift must say a stack is BEHIND when a merged template change never deployed.

On 2026-11-28 `labs-jj-monitoring` was running its 11-06 template while three merged
changes (#3144, #3243, #3255) waited; the RDS-connections alarm kept paging on the old
rule. These tests pin the placement logic, and the one normalisation that decided
whether the tool worked at all: a stack deployed from a non-UTF-8 shell stores `?`
for every non-ASCII character, and without folding them 10 of 14 stacks read UNKNOWN.
"""

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "infra_drift.py"
_spec = importlib.util.spec_from_file_location("infra_drift", _SRC)
infra_drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(infra_drift)

NEW = "Resources:\n  A: 60  # second fix\n"
MID = "Resources:\n  A: 60\n"
OLD = "Resources:\n  A: 300\n"
HISTORY = [
    ("ccc", "2026-11-24", "second fix", NEW),
    ("bbb", "2026-11-23", "first fix", MID),
    ("aaa", "2026-11-06", "baseline", OLD),
]


def test_newest_commit_is_in_sync():
    assert infra_drift.match_history(NEW, HISTORY) == {"state": "in_sync", "deployed_sha": "ccc", "undeployed": []}


def test_older_commit_lists_everything_merged_since():
    res = infra_drift.match_history(OLD, HISTORY)
    assert res["state"] == "behind"
    assert res["deployed_sha"] == "aaa"
    assert [u["sha"] for u in res["undeployed"]] == ["ccc", "bbb"]


def test_a_body_on_no_commit_is_unknown_not_in_sync():
    res = infra_drift.match_history("Resources:\n  A: 999\n", HISTORY)
    assert res["state"] == "unknown"


def test_question_marks_from_a_non_utf8_upload_still_match():
    repo = "# guardrails — first slice\nResources: {}\n"
    stored = "# guardrails ? first slice\nResources: {}\n"
    hist = [("ddd", "2026-11-06", "x", repo)]
    assert infra_drift.match_history(stored, hist)["state"] == "in_sync"


def test_line_endings_and_trailing_space_are_not_a_change():
    hist = [("eee", "2026-11-06", "x", "a: 1\nb: 2\n")]
    assert infra_drift.match_history("a: 1  \r\nb: 2\r\n", hist)["state"] == "in_sync"


def test_stack_names_follow_the_readme():
    assert infra_drift.stack_for("infra/labs-monitoring.yml") == "labs-jj-monitoring"
    assert infra_drift.stack_for("infra/labs-observe-readonly.yml") == "labs-jj-observe"
