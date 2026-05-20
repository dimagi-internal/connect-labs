import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from weekly_changelog import classify_pr, fetch_pr_files, generate_weekly_summary, load_user_visible_prs  # noqa: E402

PR_TEMPLATE = {
    "number": 1,
    "title": "feat: something",
    "html_url": "https://github.com/jjackson/connect-labs/pull/1",
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
        "commcare_connect/prelogin/views.py",
        "commcare_connect/templates/prelogin/home.html",
        "commcare_connect/static/prelogin/app.js",
    ]
    assert classify_pr(files) == "marketing"


def test_classify_pr_all_app():
    files = [
        "commcare_connect/workflow/views.py",
        "commcare_connect/audit/models.py",
    ]
    assert classify_pr(files) == "app"


def test_classify_pr_mixed():
    files = [
        "commcare_connect/prelogin/views.py",
        "commcare_connect/workflow/views.py",
    ]
    assert classify_pr(files) == "mixed"


def test_classify_pr_empty_files():
    assert classify_pr([]) == "app"


def test_fetch_pr_files_returns_filenames():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "commcare_connect/prelogin/views.py\ncommcare_connect/workflow/views.py\n"
    with patch("weekly_changelog.subprocess.run", return_value=mock_result) as mock_run:
        files = fetch_pr_files(42, "jjackson/connect-labs")
    mock_run.assert_called_once_with(
        ["gh", "api", "repos/jjackson/connect-labs/pulls/42/files", "--jq", ".[].filename"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert files == ["commcare_connect/prelogin/views.py", "commcare_connect/workflow/views.py"]


def test_fetch_pr_files_returns_empty_on_error():
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    with patch("weekly_changelog.subprocess.run", return_value=mock_result):
        files = fetch_pr_files(99, "jjackson/connect-labs")
    assert files == []


def test_fetch_pr_files_returns_empty_on_timeout():
    with patch("weekly_changelog.subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 15)):
        files = fetch_pr_files(7, "jjackson/connect-labs")
    assert files == []


def test_fetch_pr_files_strips_blank_lines():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "\ncommcare_connect/prelogin/home.html\n\n"
    with patch("weekly_changelog.subprocess.run", return_value=mock_result):
        files = fetch_pr_files(1, "jjackson/connect-labs")
    assert files == ["commcare_connect/prelogin/home.html"]


def test_load_user_visible_prs_adds_marketing_category():
    pr = dict(PR_TEMPLATE, number=10)
    prs_file = _write_prs_file([pr])
    marketing_files = ["commcare_connect/prelogin/views.py"]
    try:
        with patch("weekly_changelog.fetch_pr_files", return_value=marketing_files), patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "jjackson/connect-labs"}
        ):
            result = load_user_visible_prs(prs_file)
    finally:
        os.unlink(prs_file)
    assert len(result) == 1
    assert result[0]["category"] == "marketing"


def test_load_user_visible_prs_adds_app_category():
    pr = dict(PR_TEMPLATE, number=11)
    prs_file = _write_prs_file([pr])
    app_files = ["commcare_connect/workflow/views.py"]
    try:
        with patch("weekly_changelog.fetch_pr_files", return_value=app_files), patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "jjackson/connect-labs"}
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
            os.environ, {"GITHUB_REPOSITORY": "jjackson/connect-labs"}
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
    """Verify the PR text block sent to Claude includes [category: X] annotation."""
    captured_messages = []

    class FakeResponse:
        content = [MagicMock(text="- **Some fix** — [Marketing] Details here.")]

    class FakeMessages:
        def create(self, **kwargs):
            captured_messages.append(kwargs)
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    prs = [
        {
            "number": 5,
            "title": "feat(prelogin): nav fix",
            "description": "Hamburger menu added.",
            "category": "marketing",
        },
        {
            "number": 6,
            "title": "fix(workflow): dashboard crash",
            "description": "Fixed React crash.",
            "category": "app",
        },
    ]
    generate_weekly_summary(FakeClient(), prs)

    assert len(captured_messages) == 1
    user_content = captured_messages[0]["messages"][0]["content"]
    assert "PR #5 [category: marketing]" in user_content
    assert "PR #6 [category: app]" in user_content
