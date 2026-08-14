"""Corpus-level PII guarantee: nothing identifying survives ingest.

The fixtures here are **fabricated** — the repo must not contain real
beneficiary names or phone numbers. They reproduce the exact shape of
production records: ``entity_name`` of the form ``"<name> - <phone>"``, a
populated ``form_json``, and a GPS ``location`` string.

This test was written after running the identical check against 300 genuinely
un-stripped production records (300 names, 256 phone numbers, zero reaching the
database or the API). This is the committed regression guard for that result.

One subtlety worth preserving, because it produced a false positive first time:
GPS coordinates contain long digit runs (``11.0144677``) that look like phone
numbers to a naive regex. We *deliberately* store coordinates — they are the
map. So phone candidates are harvested only from ``entity_name`` and from form
fields whose key names a phone, never from arbitrary digit runs.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse import ingest
from connect_labs.pulse.models import PulseCursor, PulseEvent, PulseOpportunity

FAKE_PEOPLE = [
    ("Amina Bello", "08031110001"),
    ("Chidinma Okeke", "08031110002"),
    ("Fatima Danjuma", "08031110003"),
    ("Ngozi Adeyemi", "08031110004"),
    ("Halima Suleiman", "08031110005"),
    ("Yewande Balogun", "08031110006"),
]


def raw_visit(vid: int, person_idx: int) -> dict:
    """A record shaped exactly like a production user_visits row."""
    name, phone = FAKE_PEOPLE[person_idx % len(FAKE_PEOPLE)]
    return {
        "id": vid,
        "opportunity_id": 765,
        "username": "985770f1bf2079f58119",
        "user_id": "0e70-3fbb-32a6",
        "entity_id": f"{name} - {phone} - 1 month visit",
        "entity_name": f"{name} - {phone}",
        "deliver_unit": "1707",
        "visit_date": "2026-07-28T10:06:43.185000Z",
        "date_created": "2026-07-28T13:32:40.564605Z",
        "status": "approved",
        "location": "11.0144677 7.6929683 631.0 11.8",
        "flagged": "False",
        "flag_reason": "None",
        "review_status": "agree",
        "justification": f"spoke to {name} directly",
        "reason": None,
        "form_json": {
            "form": {
                "caregiver_phone": phone,
                "caregiver_name": name,
                "anthropometric": {"child_weight": "3.2"},
                "meta": {"location": "11.0144677 7.6929683 631.0 11.8"},
            }
        },
    }


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def paginate(self, endpoint, params=None, *, partial_ok=False):
        yield sorted(self.rows, key=lambda r: r["id"])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _harvest(rows) -> tuple[set[str], set[str]]:
    """Every identifying token present in the source records."""
    names: set[str] = set()
    phones: set[str] = set()

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if re.search(r"phone|msisdn|mobile|contact", key, re.I):
                phones.update(re.findall(r"\d{7,}", node))
            if re.search(r"name", key, re.I):
                names.update(p for p in re.split(r"[\s\-,]+", node) if len(p) >= 4 and not p.isdigit())

    for row in rows:
        for part in re.split(r"[\s\-,]+", row.get("entity_name") or ""):
            if len(part) >= 4 and not part.isdigit():
                names.add(part)
            elif part.isdigit() and len(part) >= 7:
                phones.add(part)
        walk(row.get("form_json") or {})
    return names, phones


@pytest.fixture
def ingested(db):
    opp = PulseOpportunity.objects.create(
        opportunity_id=765, name="Mother Baby Wellness (Nigeria)", usd_per_service="0.70"
    )
    rows = [raw_visit(1000 + i, i) for i in range(60)]
    # Already-positioned cursor: a fresh one would seed to the present rather
    # than ingest, and this module needs records actually stored to search.
    cursor = PulseCursor.objects.create(
        opportunity_id=765,
        endpoint=ingest.VISITS_ENDPOINT,
        last_id=0,
        last_polled_at=timezone.now() - timedelta(days=1),
    )
    ingest.tail_visits(_Client(rows), cursor, max_rows=10_000)
    ingest.rebuild_rollups()
    ingest.record_success("tail")
    ingest.record_success("cheap")
    return rows, opp


@pytest.mark.django_db
class TestNoPIISurvivesIngest:
    def test_fixture_actually_contains_pii(self, ingested):
        """Guards the guard: if the fixture stopped carrying PII this whole
        module would pass vacuously — which is exactly the trap that made the
        first version of this check meaningless."""
        rows, _ = ingested
        names, phones = _harvest(rows)
        assert len(names) >= 10
        assert len(phones) >= 6
        assert PulseEvent.objects.count() == 60

    def test_no_identifying_token_reaches_the_database(self, ingested):
        rows, _ = ingested
        names, phones = _harvest(rows)
        blob = " ".join(
            " ".join(str(getattr(e, f.name)) for f in PulseEvent._meta.concrete_fields)
            for e in PulseEvent.objects.all()
        )
        assert sorted(n for n in names if n in blob) == []
        assert sorted(p for p in phones if p in blob) == []

    def test_no_identifying_token_reaches_the_api(self, client, ingested):
        """The API is what a public link actually serves, so it gets its own
        check rather than relying on the database being clean."""
        rows, _ = ingested
        names, phones = _harvest(rows)
        body = (
            client.get(reverse("pulse:api_events"), {"limit": 500}).content.decode()
            + client.get(reverse("pulse:api_summary")).content.decode()
            + client.get(reverse("pulse:api_replay"), {"hours": 336}).content.decode()
        )
        assert sorted(n for n in names if n in body) == []
        assert sorted(p for p in phones if p in body) == []

    def test_gps_is_deliberately_retained(self, ingested):
        """The counterpart assertion: stripping must not be so aggressive that
        it removes the coordinates the map is made of."""
        event = PulseEvent.objects.first()
        assert event.lat == pytest.approx(11.0144677, abs=1e-4)
        assert event.country == "NG"

    def test_form_json_is_never_stored(self, ingested):
        blob = json.dumps(
            [
                {f.name: str(getattr(e, f.name)) for f in PulseEvent._meta.concrete_fields}
                for e in PulseEvent.objects.all()[:5]
            ]
        )
        assert "child_weight" not in blob
        assert "anthropometric" not in blob


@pytest.mark.django_db
class TestCoordinatePrecision:
    """What must not be shown must not be sent.

    The screen promises "household coordinates are never shown below town
    scale", and the events/replay APIs serve unauthenticated public links --
    so the payload itself is the boundary, not the rendering. Storage keeps
    full precision (the map and any future server-side resolution need it);
    every serialized coordinate is rounded to two decimals, ~1.1 km. Four
    decimals -- what these endpoints used to ship -- is ~11 m: a household.
    """

    def _assert_town_scale(self, coords):
        assert coords, "expected coordinates in the payload"
        for value in coords:
            assert value == round(value, 2), f"{value} is finer than town scale"

    def test_event_feed_is_town_scale(self, client, ingested):
        body = json.loads(client.get(reverse("pulse:api_events"), {"limit": 500}).content)
        lat_i, lon_i = body["fields"].index("lat"), body["fields"].index("lon")
        rows = body["events"]
        self._assert_town_scale([r[i] for r in rows for i in (lat_i, lon_i) if r[i] is not None])

    def test_replay_is_town_scale(self, client, ingested):
        # Explicit range: the corpus is timestamped in fixed July 2026, which
        # the rolling window (capped at 14 days) can't be relied on to reach.
        import time as _time

        body = json.loads(
            client.get(
                reverse("pulse:api_replay"),
                {"from": 1750000000, "to": int(_time.time())},
            ).content
        )
        lat_i, lon_i = body["fields"].index("lat"), body["fields"].index("lon")
        rows = body["events"]
        self._assert_town_scale([r[i] for r in rows for i in (lat_i, lon_i) if r[i] is not None])

    def test_stored_precision_is_untouched(self, ingested):
        """Rounding belongs at the boundary, never in the store."""
        event = PulseEvent.objects.exclude(lat=None).first()
        assert event.lat != round(event.lat, 2)
