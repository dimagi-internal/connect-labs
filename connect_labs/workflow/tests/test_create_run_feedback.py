"""Pins the click feedback on the list page's "Create Run" button.

Creating a run is a plain full-page form POST, and the view behind it does two
Connect API round trips plus an S3 mirror before it redirects into the runner.
That is roughly a second on a good day. The button used to carry no pressed
state and no busy state, so for that whole second nothing on screen changed and
the click read as having missed entirely — the sibling "create workflow from
template" button, which is JS-driven, has had a spinner the whole time.

Two independent mechanisms are pinned here because they fail independently:
`active:` paints the press with no JS in the path at all, and the `starting`
flag covers the rest of the wait (and blocks a double submit).
"""

import re
from pathlib import Path

import connect_labs

TEMPLATE_PATH = Path(connect_labs.__file__).resolve().parent / "templates" / "workflow" / "list.html"


def _create_run_form() -> str:
    """The Create Run <form> block, as authored."""
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    match = re.search(r"<form[^>]*api_start_run.*?</form>", source, flags=re.S)
    assert match, "the Create Run form is gone — this guard now checks nothing"
    return match.group(0)


def test_the_card_component_declares_the_starting_flag():
    """x-show/:class on an undeclared property silently never turns on."""
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "starting: false," in source


def test_the_press_itself_paints_without_waiting_for_javascript():
    """The complaint was about the press, not the wait: `hover:` alone leaves
    mousedown looking identical to hover, so nothing acknowledges the click."""
    form = _create_run_form()
    assert "active:bg-green-200" in form


def test_the_button_goes_busy_for_the_rest_of_the_wait():
    form = _create_run_form()
    assert 'x-show="starting"' in form, "no spinner while the POST is in flight"
    assert "fa-spinner" in form
    assert "Creating run..." in form


def test_a_second_submit_is_blocked_while_one_is_in_flight():
    """A dead-looking button invites a second click, which would create a
    second run — the exact thing the missing feedback made likely."""
    form = _create_run_form()
    assert "@submit=" in form
    assert "$event.preventDefault()" in form
    assert "starting = true" in form
