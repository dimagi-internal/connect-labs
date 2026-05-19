import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from weekly_changelog import classify_pr, fetch_pr_files  # noqa: E402


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
    )
    assert files == ["commcare_connect/prelogin/views.py", "commcare_connect/workflow/views.py"]


def test_fetch_pr_files_returns_empty_on_error():
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    with patch("weekly_changelog.subprocess.run", return_value=mock_result):
        files = fetch_pr_files(99, "jjackson/connect-labs")
    assert files == []


def test_fetch_pr_files_strips_blank_lines():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "\ncommcare_connect/prelogin/home.html\n\n"
    with patch("weekly_changelog.subprocess.run", return_value=mock_result):
        files = fetch_pr_files(1, "jjackson/connect-labs")
    assert files == ["commcare_connect/prelogin/home.html"]
