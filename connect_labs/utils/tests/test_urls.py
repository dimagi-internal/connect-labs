from connect_labs.utils.urls import build_absolute_url


def test_build_absolute_url_falls_back_to_localhost_without_db_access():
    """Unmarked test (no DB access) -- Site.objects.get_current() fails, so
    this exercises the fallback rather than raising."""
    assert build_absolute_url("/foo/bar") == "https://localhost/foo/bar"
