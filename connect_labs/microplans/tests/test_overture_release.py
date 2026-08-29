"""The Overture release pin, and what happens when it expires.

Overture prunes old releases. A pin left behind stops resolving, and the raw
failure names a glob rather than the cause — which is how a stale pin read as a
query bug for long enough to break footprint sampling in every country without
a local extract.
"""

from __future__ import annotations

import pytest

from connect_labs.microplans.core import overture


def test_extract_regions_declare_the_release_they_were_cut_from():
    """A region without a release would be used against any pin, serving stale data."""
    for name, meta in overture.EXTRACT_REGIONS.items():
        assert meta.get("release"), f"{name} extract has no release"
        assert meta.get("bbox") and len(meta["bbox"]) == 4


def test_an_extract_is_only_used_on_a_matching_release():
    """Bumping the pin must degrade to the live read, never serve stale buildings."""
    nigeria = overture.EXTRACT_REGIONS["nigeria"]
    inside = (7.0, 9.0, 8.0, 10.0)

    if nigeria["release"] == overture.OVERTURE_RELEASE:
        assert overture.covering_region(inside) == "nigeria"
    else:
        assert overture.covering_region(inside) is None


def test_an_area_outside_every_extract_has_no_region():
    # Rwanda — no extract, so it must take the live path.
    assert overture.covering_region((28.9, -2.8, 30.9, -1.0)) is None


def test_verify_release_names_what_is_available(monkeypatch):
    monkeypatch.setattr(overture, "available_releases", lambda con=None: ["2099-01-01.0", "2098-01-01.0"])

    with pytest.raises(RuntimeError) as err:
        overture.verify_release()

    message = str(err.value)
    assert overture.OVERTURE_RELEASE in message
    assert "2099-01-01.0" in message
    # The remedy has to be in the message; the raw DuckDB error has none.
    assert "re-extract" in message


def test_verify_release_is_quiet_when_the_pin_is_live(monkeypatch):
    monkeypatch.setattr(overture, "available_releases", lambda con=None: [overture.OVERTURE_RELEASE, "old"])

    overture.verify_release()  # must not raise


def test_verify_release_does_not_guess_when_the_bucket_cannot_be_listed(monkeypatch):
    """No network, no opinion — a listing failure must not look like an expired pin."""
    monkeypatch.setattr(overture, "available_releases", lambda con=None: [])

    overture.verify_release()  # must not raise
