"""Proxy models for microplans LocalLabsRecords.

Persistence rides the production LabsRecord API (no Django models / tables) per
the labs convention: every record carries experiment=<opportunity_id> and a
`type` discriminator. These proxy classes give typed `@property` access to the
JSON `data`; they are transient (no .save()).
"""

from __future__ import annotations

from commcare_connect.labs.models import LocalLabsRecord

TYPE_AREA = "rooftop_area"
TYPE_FRAME = "rooftop_frame"


class RooftopAreaRecord(LocalLabsRecord):
    """The drawn intervention/comparison area(s) + frame config for one run."""

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def areas(self) -> list[dict]:
        """[{"arm": "intervention"|"comparison", "geometry": <GeoJSON>}, ...]"""
        return self.data.get("areas", [])

    @property
    def config(self) -> dict:
        return self.data.get("config", {})

    @property
    def mode(self) -> str:
        """ "sampling" (PPS subset) | "coverage" (visit every household)."""
        return self.data.get("mode", "sampling")

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")


class RooftopFrameRecord(LocalLabsRecord):
    """A generated frame.

    Sampling mode: pins + cluster hulls + per-arm stats. Coverage mode: the
    cluster polygons live in `hulls` (pins is empty). Parented (labs_record_id)
    to the RooftopAreaRecord it was generated from.
    """

    @property
    def mode(self) -> str:
        return self.data.get("mode", "sampling")

    @property
    def pins(self) -> dict:
        return self.data.get("pins", {"type": "FeatureCollection", "features": []})

    @property
    def hulls(self) -> dict:
        return self.data.get("hulls", {"type": "FeatureCollection", "features": []})

    @property
    def stats(self) -> list[dict]:
        return self.data.get("stats", [])

    @property
    def pin_count(self) -> int:
        return len(self.pins.get("features", []))

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")
