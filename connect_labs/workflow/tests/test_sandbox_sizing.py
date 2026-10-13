"""The sandbox frame grows to its content and the page around it scrolls.

Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Sizing: the frame grows
to its content". The frame runtime (components/workflow/sandbox/viewport.ts) sets the
variables; the shell's own stylesheet is what makes them place the page.
"""

import re

import pytest


def _shell_style(client) -> str:
    body = client.get("/labs/sandbox/render/").content.decode()
    styles = re.findall(r"<style>(.*?)</style>", body, re.S)
    assert styles, "the shell has no stylesheet of its own"
    return re.sub(r"\s+", " ", " ".join(styles))


@pytest.mark.django_db
def test_the_frame_never_scrolls_itself(client):
    assert "html { overflow: hidden; }" in _shell_style(client)


@pytest.mark.django_db
def test_the_body_is_moved_to_what_the_person_sees_and_the_content_back(client):
    style = _shell_style(client)
    # The body's transform makes it the containing block of position: fixed, so a
    # dialog is placed in the part of the frame on screen...
    assert "transform: translateY(var(--labs-slice-top, 0px))" in style
    assert "height: var(--labs-slice-height, auto)" in style
    # ...and the content is moved back by as much, so it stays where it is.
    assert "#sandbox-root { position: relative; top: calc(-1 * var(--labs-slice-top, 0px));" in style
    # Reaching into the page's margins, it keeps its content in the column.
    assert "margin-left: var(--labs-gutter-left, 0px); margin-right: var(--labs-gutter-right, 0px);" in style
    # ...and clips what overflows it where the page did.
    assert (
        "clip-path: inset(-100000px var(--labs-clip-right, -100000px) -100000px var(--labs-clip-left, -100000px));"
        in style
    )
