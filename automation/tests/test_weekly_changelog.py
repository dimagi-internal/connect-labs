import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from weekly_changelog import classify_pr  # noqa: E402


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
