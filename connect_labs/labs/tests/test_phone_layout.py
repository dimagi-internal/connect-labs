"""The application shell on a phone: the sidenav is a drawer, not a rail that takes a fifth of the screen.

Below Tailwind's `md` (768px) the 64px rail is gone and the menu button in the
header's corner opens the nav over a backdrop; from `md` up the shell is what it
always was. These pin the classes that make that so -- every one of them must also
exist in the built stylesheet (labs is Tailwind v4, where an unknown class fails
silently), which the PR checked against a build.
"""

import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory

pytestmark = pytest.mark.django_db


def _shell(**context):
    request = RequestFactory().get("/labs/targeting/")
    request.user = AnonymousUser()
    return render_to_string("base.html", context, request=request)


def _classes(html, pattern):
    match = re.search(pattern, html)
    assert match, pattern
    return match.group(1).split()


def test_the_body_reserves_the_rail_only_from_md_up():
    body = _classes(_shell(), r'<body[^>]*class="([^"]*)"')
    assert "md:pl-16" in body
    assert "pl-16" not in body


def test_the_header_spans_a_phone_and_clears_the_menu_button():
    header = _classes(_shell(), r'<header class="([^"]*)"')
    assert {"left-0", "md:left-16"} <= set(header)
    assert "left-16" not in header


def test_the_sidenav_is_an_off_canvas_drawer_below_md():
    html = _shell()
    toggle = _classes(html, r'<button[^>]*data-testid="labs-nav-toggle"[^>]*class="([^"]*)"')
    assert "md:hidden" in toggle
    nav = re.search(r'<div id="labs-sidenav"[^>]*:class="([^"]*)"', html, re.S).group(1)
    # Closed: off screen on a phone, the 64px rail from md up.
    assert "-translate-x-full md:w-16 md:translate-x-0" in nav
    assert 'data-testid="labs-nav-backdrop"' in html


def test_present_mode_still_draws_no_shell():
    html = _shell(present_mode=True)
    assert "labs-nav-toggle" not in html
    assert "md:pl-16" not in html
