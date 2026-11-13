"""The effectiveness pages' scripts live in static files, and the data-driven ones never parse model text as HTML."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static" / "effectiveness"
TEMPLATES = ROOT / "templates" / "effectiveness"

#: Template -> the scripts it loads, in order. ``ui.js`` (the DOM builder and number formats) comes first where used.
PAGES = {
    "models.html": ["ui.js", "models.js"],
    "market.html": ["ui.js", "market.js"],
    "implementation.html": ["implementation.js"],
}


@pytest.mark.parametrize("page, scripts", PAGES.items())
def test_each_page_loads_its_scripts_from_static_files(page, scripts):
    html = (TEMPLATES / page).read_text(encoding="utf-8")
    loaded = re.findall(r"\{% static 'effectiveness/([a-z_]+\.js)' %\}", html)
    assert loaded == scripts
    assert all((STATIC / name).exists() for name in scripts)
    assert "{% load static %}" in html


@pytest.mark.parametrize("page", PAGES)
def test_no_page_carries_inline_code(page):
    """Data goes in through ``json_script``; the only inline script allowed besides is the map token."""
    html = (TEMPLATES / page).read_text(encoding="utf-8")
    inline = [b for b in re.findall(r"<script>(.*?)</script>", html, flags=re.S) if "MAPBOX_TOKEN" not in b]
    assert inline == []


@pytest.mark.parametrize("name", ["ui.js", "models.js", "market.js"])
def test_the_dom_builder_pages_never_parse_model_text_as_html(name):
    """Labels and sources come from the model specs; these scripts build nodes with textContent, so none is HTML."""
    assert "innerHTML" not in (STATIC / name).read_text(encoding="utf-8")
