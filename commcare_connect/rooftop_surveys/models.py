"""Proxy models for rooftop_surveys LocalLabsRecords.

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
    def created_at(self) -> str:
        return self.data.get("created_at", "")


class RooftopFrameRecord(LocalLabsRecord):
    """A generated sampling frame: pins + cluster hulls + per-arm stats.

    Parented (labs_record_id) to the RooftopAreaRecord it was generated from.
    """

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
