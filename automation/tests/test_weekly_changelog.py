import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from weekly_changelog import (  # noqa: E402
    classify_pr,
    fetch_pr_files,
    generate_weekly_summary,
    group_prs_by_feature,
    load_user_visible_prs,
)

PR_TEMPLATE = {
    "number": 1,
    "title": "feat: something",
    "html_url": "https://github.com/dimagi-internal/connect-labs/pull/1",
    "merged_at": "2026-05-19T10:00:00Z",
    "body": "## Product Description\nThis changes the UI.",
}


def _write_prs_file(prs):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(prs, f)
    f.close()
    return f.name


def test_classify_pr_all_marketing():
    files = [
        "connect_labs/prelogin/views.py",
        "connect_labs/templates/prelogin/home.html",
        "connect_labs/static/prelogin/app.js",
    ]
    assert classify_pr(files) == "marketing"


def test_classify_pr_all_app():
    files = [
        "connect_labs/workflow/views.py",
        "connect_labs/audit/models.py",
    ]
    assert classify_pr(files) == "app"


def test_classify_pr_mixed():
    files = [
        "connect_labs/prelogin/views.py",
        "connect_labs/workflow/views.py",
    ]
    assert classify_pr(files) == "mixed"


def test_classify_pr_empty_files():
    assert classify_pr([]) == "app"


def test_fetch_pr_files_returns_filenames():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "connect_labs/prelogin/views.py\nconnect_labs/workflow/views.py\n"
    with patch("weekly_changelog.subprocess.run", return_value=mock_result) as mock_run:
        files = fetch_pr_files(42, "dimagi-internal/connect-labs")
    mock_run.assert_called_once_with(
        ["gh", "api", "repos/dimagi-internal/connect-labs/pulls/42/files", "--jq", ".[].filename"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert files == ["connect_labs/prelogin/views.py", "connect_labs/workflow/views.py"]


def test_fetch_pr_files_returns_empty_on_error():
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    with patch("weekly_changelog.subprocess.run", return_value=mock_result):
        files = fetch_pr_files(99, "dimagi-internal/connect-labs")
    assert files == []


def test_fetch_pr_files_returns_empty_on_timeout():
    with patch("weekly_changelog.subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 15)):
        files = fetch_pr_files(7, "dimagi-internal/connect-labs")
    assert files == []


def test_fetch_pr_files_strips_blank_lines():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "\nconnect_labs/prelogin/home.html\n\n"
    with patch("weekly_changelog.subprocess.run", return_value=mock_result):
        files = fetch_pr_files(1, "dimagi-internal/connect-labs")
    assert files == ["connect_labs/prelogin/home.html"]


def test_load_user_visible_prs_adds_marketing_category():
    pr = dict(PR_TEMPLATE, number=10)
    prs_file = _write_prs_file([pr])
    marketing_files = ["connect_labs/prelogin/views.py"]
    try:
        with patch("weekly_changelog.fetch_pr_files", return_value=marketing_files), patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "dimagi-internal/connect-labs"}
        ):
            result = load_user_visible_prs(prs_file)
    finally:
        os.unlink(prs_file)
    assert len(result) == 1
    assert result[0]["category"] == "marketing"


def test_load_user_visible_prs_adds_app_category():
    pr = dict(PR_TEMPLATE, number=11)
    prs_file = _write_prs_file([pr])
    app_files = ["connect_labs/workflow/views.py"]
    try:
        with patch("weekly_changelog.fetch_pr_files", return_value=app_files), patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "dimagi-internal/connect-labs"}
        ):
            result = load_user_visible_prs(prs_file)
    finally:
        os.unlink(prs_file)
    assert result[0]["category"] == "app"


def test_load_user_visible_prs_skips_empty_product_description():
    pr = dict(PR_TEMPLATE, number=12, body="## Product Description\n\n")
    prs_file = _write_prs_file([pr])
    try:
        with patch("weekly_changelog.fetch_pr_files", return_value=[]), patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "dimagi-internal/connect-labs"}
        ):
            result = load_user_visible_prs(prs_file)
    finally:
        os.unlink(prs_file)
    assert result == []


def test_load_user_visible_prs_defaults_to_app_when_no_repo():
    pr = dict(PR_TEMPLATE, number=13)
    prs_file = _write_prs_file([pr])
    try:
        with patch("weekly_changelog.fetch_pr_files") as mock_fetch, patch.dict(os.environ, {}, clear=True):
            # Remove GITHUB_REPOSITORY from env if present
            os.environ.pop("GITHUB_REPOSITORY", None)
            result = load_user_visible_prs(prs_file)
    finally:
        os.unlink(prs_file)
    mock_fetch.assert_not_called()
    assert result[0]["category"] == "app"


def test_generate_weekly_summary_includes_category_annotation():
    """Verify the group text sent to Claude includes [category: X] annotation."""
    captured_messages = []

    class FakeResponse:
        content = [MagicMock(text="- **Some fix** — [Marketing] Details here.")]

    class FakeMessages:
        def create(self, **kwargs):
            captured_messages.append(kwargs)
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    groups = [
        {
            "title": "Nav fix",
            "description": "Hamburger menu added.",
            "pr_numbers": [5],
            "lead_pr": 5,
            "lead_url": "https://github.com/dimagi-internal/connect-labs/pull/5",
            "category": "marketing",
        },
        {
            "title": "Dashboard fix",
            "description": "Fixed React crash.",
            "pr_numbers": [6],
            "lead_pr": 6,
            "lead_url": "https://github.com/dimagi-internal/connect-labs/pull/6",
            "category": "app",
        },
    ]
    generate_weekly_summary(FakeClient(), groups)

    assert len(captured_messages) == 1
    user_content = captured_messages[0]["messages"][0]["content"]
    assert "Group [category: marketing]" in user_content
    assert "Group [category: app]" in user_content


def test_group_prs_by_feature_returns_groups():
    """Verify grouping parses JSON and attaches marketing category from source PRs."""
    fake_json = json.dumps(
        [
            {
                "title": "New dashboard",
                "description": "Overview tab is live.",
                "pr_numbers": [10, 11],
                "lead_pr": 10,
                "type": "New",
            }
        ]
    )

    class FakeResponse:
        content = [MagicMock(text=fake_json)]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    prs = [
        {
            "number": 10,
            "title": "feat: dashboard",
            "description": "Overview.",
            "url": "https://github.com/dimagi-internal/connect-labs/pull/10",
            "category": "app",
        },
        {
            "number": 11,
            "title": "fix: dashboard crash",
            "description": "Fixed crash.",
            "url": "https://github.com/dimagi-internal/connect-labs/pull/11",
            "category": "app",
        },
    ]
    groups = group_prs_by_feature(FakeClient(), prs)

    assert len(groups) == 1
    g = groups[0]
    assert g["title"] == "New dashboard"
    assert g["lead_pr"] == 10
    assert g["lead_url"] == "https://github.com/dimagi-internal/connect-labs/pull/10"
    assert g["category"] == "app"
    assert set(g["pr_numbers"]) == {10, 11}


def test_group_prs_by_feature_derives_marketing_category():
    """Verify group category is derived from constituent PRs (marketing > mixed > app)."""
    fake_json = json.dumps(
        [
            {
                "title": "Homepage update",
                "description": "Navigation changed.",
                "pr_numbers": [20, 21],
                "lead_pr": 20,
                "type": "Improvement",
            }
        ]
    )

    class FakeResponse:
        content = [MagicMock(text=fake_json)]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    prs = [
        {
            "number": 20,
            "title": "feat: workflow",
            "description": "Workflow update.",
            "url": "https://github.com/dimagi-internal/connect-labs/pull/20",
            "category": "app",
        },
        {
            "number": 21,
            "title": "feat: prelogin nav",
            "description": "Navigation change.",
            "url": "https://github.com/dimagi-internal/connect-labs/pull/21",
            "category": "marketing",
        },
    ]
    groups = group_prs_by_feature(FakeClient(), prs)

    assert len(groups) == 1
    # One app PR + one marketing PR → group inherits "marketing"
    assert groups[0]["category"] == "marketing"


def test_group_prs_by_feature_fallback_on_bad_json():
    """Verify graceful fallback when Claude returns invalid JSON."""

    class FakeResponse:
        content = [MagicMock(text="not valid json at all")]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    prs = [
        {
            "number": i,
            "title": f"PR {i}",
            "description": f"Desc {i}.",
            "url": f"https://github.com/dimagi-internal/connect-labs/pull/{i}",
            "category": "app",
        }
        for i in range(1, 15)  # 14 PRs — more than fallback cap of 10
    ]
    groups = group_prs_by_feature(FakeClient(), prs)

    # Fallback produces one-group-per-PR, capped at 10
    assert len(groups) == 10
    assert groups[0]["lead_pr"] == 1
    assert groups[0]["pr_numbers"] == [1]
