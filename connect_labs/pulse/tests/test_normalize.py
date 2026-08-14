"""Normalisation and the PII boundary.

The fixture below is the real shape of a production ``user_visits`` record,
including a genuine-format ``entity_name`` (a beneficiary's name and phone
number). It is here on purpose: the point of these tests is that such a record
can pass through ingest without any of it landing in the database.
"""

from __future__ import annotations

import pytest

from connect_labs.pulse.models import PulseEvent, PulseOpportunity
from connect_labs.pulse.normalize import (
    FORBIDDEN_FIELDS,
    country_for,
    flag_type_for,
    is_on_map,
    parse_location,
    service_slug_for,
    visit_to_event_fields,
)

# Shape taken verbatim from /export/opportunity/765/user_visits/ (names altered).
REAL_VISIT = {
    "id": 88123456,
    "opportunity_id": 765,
    "username": "985770f1bf2079f58119",
    "user_id": "3f2a-...",
    "entity_id": "Sa,adatu Yakubu - 8037760312 - 1 month visit",
    "entity_name": "Sa,adatu Yakubu - 8037760312",
    "deliver_unit": "1707",
    "visit_date": "2026-07-28T10:06:43.185000Z",
    "date_created": "2026-07-28T13:32:40.564605Z",
    "status": "approved",
    "reason": None,
    "location": "11.0330133 7.63809 709.0 11.8",
    "flagged": "False",
    "flag_reason": "None",
    "form_json": {"form": {"anthropometric": {"child_weight": "3.2"}}},
    "review_status": "agree",
    "justification": "looked fine to me",
}


class TestPIIBoundary:
    def test_forbidden_fields_never_appear_in_output(self):
        fields = visit_to_event_fields(REAL_VISIT)
        assert fields is not None
        for forbidden in FORBIDDEN_FIELDS:
            assert forbidden not in fields

    def test_no_output_value_carries_the_beneficiary_identity(self):
        """Not just the keys — the values must not contain the name or phone."""
        fields = visit_to_event_fields(REAL_VISIT)
        blob = " ".join(str(v) for v in fields.values())
        assert "Sa,adatu" not in blob
        assert "8037760312" not in blob
        assert "child_weight" not in blob

    def test_pulse_event_has_no_column_for_identifying_data(self):
        """Structural guard: adding a PII column should fail this test loudly.

        This is the check that survives someone forgetting the rule, so it
        asserts the whole field list rather than probing for known-bad names.
        """
        actual = {f.name for f in PulseEvent._meta.get_fields()}
        assert actual == {
            "id",
            "connect_visit_id",
            "opportunity_id",
            "program_id",
            "org_slug",
            "field_ts",
            "sync_ts",
            "lat",
            "lon",
            "country",
            "status",
            "flagged",
            "flag_type",
            "review_status",
            "service_slug",
            "worker_hash",
            "usd_to_worker",
            # Bookkeeping, not data about anyone: records that this row's
            # coordinates have already been added to the anonymous grid, so a
            # second fold cannot double-count them.
            "folded_at",
        }

    def test_worker_hash_is_the_upstream_hash_not_a_name(self):
        fields = visit_to_event_fields(REAL_VISIT)
        assert fields["worker_hash"] == "985770f1bf2079f58119"


class TestParseLocation:
    def test_parses_real_four_part_location(self):
        assert parse_location("11.0330133 7.63809 709.0 11.8") == (11.0330133, 7.63809)

    @pytest.mark.parametrize("raw", [None, "", "None", "null", "garbage", "11.03"])
    def test_rejects_unusable(self, raw):
        assert parse_location(raw) is None

    def test_rejects_null_island(self):
        """A GPS chip reporting nothing, not a service delivered at 0,0."""
        assert parse_location("0.0 0.0 0.0 0.0") is None

    def test_rejects_impossible_coordinates(self):
        assert parse_location("120.0 8.5 0 0") is None
        assert parse_location("11.0 -400.0 0 0") is None

    def test_accepts_valid_but_implausible_coordinates(self):
        """Range validation is not plausibility — that's is_on_map's job."""
        assert parse_location("-57.0 -110.02 0 0") == (-57.0, -110.02)


class TestOnMap:
    def test_known_operating_regions_are_on_map(self):
        assert is_on_map(12.0, 8.52) is True
        assert is_on_map(-1.29, 36.82) is True

    def test_stray_pacific_point_is_off_map(self):
        """A real outlier measured in production, in a West-African dataset."""
        assert is_on_map(-57.0, -110.02) is False

    def test_missing_point_is_off_map(self):
        assert is_on_map(None, None) is False


class TestDerivations:
    @pytest.mark.parametrize(
        "lat,lon,expected",
        [(12.0, 8.52, "NG"), (-1.29, 36.82, "KE"), (0.31, 32.58, "UG"), (23.26, 77.41, "IN"), (51.5, -0.12, "")],
    )
    def test_country_for(self, lat, lon, expected):
        assert country_for(lat, lon) == expected

    def test_country_for_missing_point(self):
        assert country_for(None, None) == ""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("KMC - UG - PIPN - P1 - Apr 26", "kmc"),
            ("Mother Baby Wellness (Nigeria)", "mbw"),
            ("Readers - NG - EHA - P1 - Jun 26", "readers"),
            ("EHA - Back To School", "b2s"),
            ("Malaria RDT - NG - DFHF - June 2026", "rdt"),
            ("ITN - DRC - CNRSC - P2 - Jun 26", "itn"),
            ("Some Unmapped Opportunity", "other"),
            (None, "other"),
        ],
    )
    def test_service_slug_for(self, name, expected):
        assert service_slug_for(name) == expected

    @pytest.mark.parametrize(
        "blob,expected",
        [
            ("{'flags': [['duration', 'The form was completed too fast']]}", "duration"),
            ("{'flags': [['location', 'Visit location is far']]}", "location"),
            ("{'flags': [['duplicate', 'A beneficiary was seen twice']]}", "duplicate"),
            ("None", ""),
            (None, ""),
            ("{'flags': [['brand_new_check', 'x']]}", "other"),
        ],
    )
    def test_flag_type_for(self, blob, expected):
        assert flag_type_for(blob) == expected


class TestVisitToEventFields:
    def test_maps_the_core_shape(self):
        fields = visit_to_event_fields(REAL_VISIT)
        assert fields["connect_visit_id"] == 88123456
        assert fields["opportunity_id"] == 765
        assert fields["status"] == "approved"
        assert fields["review_status"] == "agree"
        assert fields["lat"] == pytest.approx(11.0330133)
        assert fields["country"] == "NG"

    def test_keeps_field_and_sync_times_separate(self):
        """The 3.4h gap here is offline sync, and both timestamps are load-bearing:
        replay is paced on field_ts, freshness is judged on sync_ts."""
        fields = visit_to_event_fields(REAL_VISIT)
        assert fields["field_ts"].hour == 10
        assert fields["sync_ts"].hour == 13
        assert fields["sync_ts"] > fields["field_ts"]

    def test_flagged_parses_the_string_boolean(self):
        """The export serialises booleans as 'True'/'False' strings — truthiness
        on the raw string would make every visit flagged."""
        assert visit_to_event_fields({**REAL_VISIT, "flagged": "False"})["flagged"] is False
        assert visit_to_event_fields({**REAL_VISIT, "flagged": "True"})["flagged"] is True

    def test_visit_without_gps_still_becomes_an_event(self):
        """4.7% of real visits lack GPS. They count; they just don't light the map."""
        fields = visit_to_event_fields({**REAL_VISIT, "location": None})
        assert fields is not None
        assert fields["lat"] is None
        assert fields["country"] == ""

    def test_off_map_visit_keeps_the_service_but_drops_the_dot(self):
        """A stray coordinate must not put a light in the South Pacific, but the
        service was still delivered and must still be counted."""
        fields = visit_to_event_fields({**REAL_VISIT, "location": "-57.0 -110.02 0 0"})
        assert fields is not None
        assert fields["status"] == "approved"
        assert fields["lat"] is None
        assert fields["lon"] is None

    def test_returns_none_without_a_usable_timestamp(self):
        assert visit_to_event_fields({**REAL_VISIT, "visit_date": None, "date_created": None}) is None

    def test_returns_none_without_an_id(self):
        assert visit_to_event_fields({k: v for k, v in REAL_VISIT.items() if k != "id"}) is None

    def test_falls_back_to_sync_time_when_field_time_missing(self):
        fields = visit_to_event_fields({**REAL_VISIT, "visit_date": None})
        assert fields["field_ts"] == fields["sync_ts"]


@pytest.mark.django_db
class TestRateAttribution:
    def test_only_approved_work_attributes_money(self):
        """Attributing the rate to rejected or over-limit work would overstate
        what was actually paid to workers — the headline funder number."""
        opp = PulseOpportunity.objects.create(
            opportunity_id=765, name="Mother Baby Wellness (Nigeria)", usd_per_service="0.70"
        )
        approved = visit_to_event_fields({**REAL_VISIT, "status": "approved"}, opp)
        rejected = visit_to_event_fields({**REAL_VISIT, "status": "rejected"}, opp)
        over = visit_to_event_fields({**REAL_VISIT, "status": "over_limit"}, opp)

        assert approved["usd_to_worker"] == "0.70"
        assert rejected["usd_to_worker"] is None
        assert over["usd_to_worker"] is None

    def test_opportunity_supplies_service_and_org(self):
        opp = PulseOpportunity.objects.create(
            opportunity_id=765, name="Mother Baby Wellness (Nigeria)", org_slug="connect-nigeria", program_id=42
        )
        fields = visit_to_event_fields(REAL_VISIT, opp)
        assert fields["service_slug"] == "mbw"
        assert fields["org_slug"] == "connect-nigeria"
        assert fields["program_id"] == 42
