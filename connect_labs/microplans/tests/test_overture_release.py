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
    """A region must state which release it was cut from, or it would be used
    against any pin and serve stale buildings.

    ``None`` is a legal value and means "declared, never cut" — it still fails
    the match in covering_region(), so it routes to the live read exactly like a
    stale one. What it must never be is ABSENT, which would KeyError at the
    routing decision.
    """
    for name, meta in overture.EXTRACT_REGIONS.items():
        assert "release" in meta, f"{name} extract does not declare a release"
        assert meta["release"] is None or isinstance(meta["release"], str)
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


def test_a_stale_extract_warns_on_the_fetch_path_not_only_in_a_cli(caplog, monkeypatch):
    """The warning that never ran.

    ``verify_release()`` has always been able to say "your extract is stale, you
    are paying the slow read" — but nothing outside the tests ever called it, so
    a Nigeria extract left on a pruned release cost ~350s per uncached ward for
    weeks with no signal anywhere. The cheap half now runs on every fetch.
    """
    monkeypatch.setitem(
        overture.EXTRACT_REGIONS, "nigeria", {"release": "1999-01-01.0", "bbox": (2.6, 4.2, 14.7, 13.9)}
    )
    with caplog.at_level("WARNING"):
        overture.verify_release_quietly()
    assert any("nigeria" in r.getMessage() for r in caplog.records), caplog.text
    assert "microplans_build_extract" in caplog.text, "the warning must name the command that fixes it"


def test_no_warning_when_every_extract_matches_the_pin(caplog, monkeypatch):
    monkeypatch.setitem(
        overture.EXTRACT_REGIONS,
        "nigeria",
        {"release": overture.OVERTURE_RELEASE, "bbox": (2.6, 4.2, 14.7, 13.9)},
    )
    with caplog.at_level("WARNING"):
        overture.verify_release_quietly()
    assert "microplans_build_extract" not in caplog.text


def test_verify_release_quietly_does_not_touch_the_network(monkeypatch):
    """It runs on the hot fetch path; a bucket listing there would be a regression."""

    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("verify_release_quietly must not open a connection")

    monkeypatch.setattr(overture, "connect", boom)
    monkeypatch.setattr(overture, "available_releases", boom)
    overture.verify_release_quietly()


def test_a_never_cut_region_is_not_reported_as_stale():
    """Backlog and regression are different problems and must read differently.

    ``stale_extracts()`` drives a warning on every fetch — it means "someone cut
    this from a release we no longer read, go re-cut it". A country we simply
    have not extracted yet is a backlog item; folding the two together would bury
    a real regression under ten countries nobody has got to.
    """
    uncut = overture.uncut_regions()
    stale = overture.stale_extracts()
    assert not (set(uncut) & set(stale)), "a region cannot be both never-cut and stale"
    for name in uncut:
        assert overture.EXTRACT_REGIONS[name]["release"] is None


def test_every_declared_region_has_a_usable_bbox():
    """A bad bbox silently extracts the wrong ground, which no test downstream catches."""
    for name, meta in overture.EXTRACT_REGIONS.items():
        minx, miny, maxx, maxy = meta["bbox"]
        assert minx < maxx and miny < maxy, f"{name} bbox is inverted"
        assert -180 <= minx <= 180 and -180 <= maxx <= 180, f"{name} longitude out of range"
        assert -90 <= miny <= 90 and -90 <= maxy <= 90, f"{name} latitude out of range"


def test_an_uncut_region_never_routes_to_an_extract():
    """Until it is cut, a declared region must still take the live read."""
    for name in overture.uncut_regions():
        bb = overture.EXTRACT_REGIONS[name]["bbox"]
        inside = (bb[0] + 0.2, bb[1] + 0.2, bb[0] + 0.3, bb[1] + 0.3)
        assert overture.covering_region(inside) != name
