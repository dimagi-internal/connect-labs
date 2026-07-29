"""Tests for the solicitation markdown template filter.

The filter renders author-supplied free text (``description`` / ``scope_of_work``)
that is shown to other labs users, so its output must be sanitized against XSS.
"""
from connect_labs.solicitations.templatetags.markdown_filters import render_markdown


def test_renders_basic_markdown():
    html = render_markdown("**bold** and *italic*")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html


def test_empty_value_returns_empty_string():
    assert render_markdown("") == ""
    assert render_markdown(None) == ""


def test_strips_script_tags():
    html = render_markdown("hi\n\n<script>alert(document.cookie)</script>")
    assert "<script" not in html.lower()
    assert "alert(document.cookie)" not in html


def test_strips_event_handler_attributes():
    html = render_markdown("<img src=x onerror=alert(1)>")
    assert "onerror" not in html.lower()


def test_strips_javascript_url_in_link():
    html = render_markdown("[click me](javascript:alert(1))")
    # The link text survives, but the javascript: scheme must be dropped.
    assert "javascript:" not in html.lower()
    assert "click me" in html


def test_keeps_safe_http_links():
    html = render_markdown("[ok](https://example.com)")
    assert 'href="https://example.com"' in html
