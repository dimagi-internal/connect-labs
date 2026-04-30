"""Synthetic MBW pipeline fixtures.

Each factory returns rows in the **pipeline output shape** (post-extract,
flattened custom fields), so harness code can feed them into both v1 helpers
and v3-style aggregators without re-running the extraction stage.

The fixtures intentionally exercise the corner cases listed in the
parity-test plan:
- missing GPS, mid-mother visits with no GPS
- single-visit mother (no revisit distance defined)
- visits spanning midnight
- duplicate (mother, datetime) entries
- missing mother_case_id
- missing/empty bf_status
- "ebf" as token within a multi-token bf_status string
- FLWs with no visits, no statuses, no registrations
- date_range edge cases (single day, no GPS at all)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class FixtureBundle:
    """A complete pipeline fixture for an MBW dashboard run."""

    visits: list[dict] = field(default_factory=list)
    registrations: list[dict] = field(default_factory=list)
    gs_forms: list[dict] = field(default_factory=list)
    active_usernames: list[str] = field(default_factory=list)
    flw_names: dict[str, str] = field(default_factory=dict)
    flw_statuses: dict[str, str] = field(default_factory=dict)


def _visit(
    visit_id: int,
    username: str,
    when: datetime,
    *,
    mother_case_id: str | None = "mother_001",
    case_id: str | None = None,
    form_name: str = "ANC Visit",
    gps: tuple[float, float] | None = (-1.2345, 35.6789),
    parity: str | None = "G2P1",
    bf_status: str | None = None,
    app_build_version: int | None = 250,
    status: str = "approved",
) -> dict:
    """Build one visit row in pipeline output shape."""
    gps_str = f"{gps[0]} {gps[1]} 1000 10" if gps else None
    return {
        "visit_id": visit_id,
        "username": username,
        "visit_date": when.date().isoformat(),
        "visit_datetime": when.isoformat(),
        "entity_id": case_id or f"case_{visit_id:03d}",
        "entity_name": f"Mother for visit {visit_id}",
        "case_id": case_id or f"case_{visit_id:03d}",
        "mother_case_id": mother_case_id,
        "form_name": form_name,
        "gps_location": gps_str,
        "metadata": {"location": gps_str} if gps_str else {},
        "parity": parity,
        "bf_status": bf_status,
        "app_build_version": app_build_version,
        "status": status,
        # ANC/PNC/baby fields used by quality metrics:
        "anc_completion_date": when.date().isoformat() if form_name == "ANC Visit" else None,
        "pnc_completion_date": when.date().isoformat() if form_name == "Post delivery visit" else None,
        "baby_dob": "2024-02-15" if form_name == "Post delivery visit" else None,
    }


def _registration(case_id: str, mother_name: str, *, expected_visits: list[str] | None = None) -> dict:
    return {
        "case_id": case_id,
        "mother_name": mother_name,
        "expected_visits": expected_visits or ["2024-01-15", "2024-02-15", "2024-03-15"],
        "user_connect_id": None,
    }


def _gs_form(username_or_connect_id: str, score: float, when) -> dict:
    return {
        "user_connect_id": username_or_connect_id,
        "case_id": f"gs_{username_or_connect_id}_{when.isoformat()}",
        "gs_score": score,
        "assessor_name": "Test Assessor",
        "assessment_date": when.isoformat(),
    }


def small_realistic() -> FixtureBundle:
    """100 visits, 5 FLWs, 30 mothers, mix of ANC/PNC/post-delivery.

    Exercises typical-case data shape without corner cases. Use this as the
    smoke fixture for the harness.
    """
    from datetime import date

    visits: list[dict] = []
    visit_id = 0
    flws = ["flw_alpha", "flw_beta", "flw_gamma", "flw_delta", "flw_epsilon"]
    for fi, flw in enumerate(flws):
        for mi in range(6):  # 6 mothers per FLW
            mother_id = f"mother_{fi}_{mi:02d}"
            case_id = f"case_{fi}_{mi:02d}_anc"
            # ANC visit
            visit_id += 1
            visits.append(
                _visit(
                    visit_id,
                    flw,
                    datetime(2024, 1, 10 + mi, 9, 0, tzinfo=timezone.utc),
                    mother_case_id=mother_id,
                    case_id=case_id,
                    form_name="ANC Visit",
                    gps=(-1.2 + fi * 0.01, 35.6 + mi * 0.005),
                    parity=f"G{(mi % 4) + 1}P{mi % 3}",
                    bf_status=None,
                )
            )
            # 2 follow-ups for some, post-delivery + bf_status for others
            for vi in range(2 if mi % 2 == 0 else 3):
                visit_id += 1
                fname = "Post delivery visit" if vi == 1 else "ANC Visit"
                visits.append(
                    _visit(
                        visit_id,
                        flw,
                        datetime(2024, 2, 5 + mi, 10 + vi, 0, tzinfo=timezone.utc),
                        mother_case_id=mother_id,
                        case_id=case_id,
                        form_name=fname,
                        gps=(-1.2 + fi * 0.01 + vi * 0.001, 35.6 + mi * 0.005 + vi * 0.001),
                        parity=f"G{(mi % 4) + 1}P{mi % 3}",
                        bf_status="ebf" if (vi == 1 and mi % 3 != 2) else ("non-ebf bottle" if vi == 1 else None),
                    )
                )

    registrations = [
        _registration(f"case_{fi}_{mi:02d}_anc", f"Mother {fi}-{mi}") for fi in range(len(flws)) for mi in range(6)
    ]
    gs_forms = [_gs_form(flw, 80.0 + fi, date(2024, 1, 20)) for fi, flw in enumerate(flws)]

    return FixtureBundle(
        visits=visits,
        registrations=registrations,
        gs_forms=gs_forms,
        active_usernames=flws,
        flw_names={flw: flw.replace("_", " ").title() for flw in flws},
        flw_statuses={"flw_alpha": "eligible", "flw_beta": "probation"},
    )


def edge_cases() -> FixtureBundle:
    """Fixture that hits each corner declared in the parity-test plan.

    Each visit has a comment explaining which contract leaf or v1 quirk
    it's exercising.
    """
    visits = [
        # No GPS — must not be flagged, must not appear in median computations.
        _visit(
            1,
            "flw_no_gps",
            datetime(2024, 1, 10, 9, tzinfo=timezone.utc),
            mother_case_id="m_alpha",
            gps=None,
        ),
        # Single-visit mother — no revisit distance defined; must not crash median.
        _visit(
            2,
            "flw_singletons",
            datetime(2024, 1, 11, 9, tzinfo=timezone.utc),
            mother_case_id="m_solo",
            gps=(-1.0, 35.0),
        ),
        # Visits spanning midnight (UTC) for daily-travel chaining.
        _visit(
            3,
            "flw_midnight",
            datetime(2024, 1, 12, 23, 30, tzinfo=timezone.utc),
            mother_case_id="m_late",
            gps=(-1.0, 35.0),
        ),
        _visit(
            4,
            "flw_midnight",
            datetime(2024, 1, 13, 0, 30, tzinfo=timezone.utc),
            mother_case_id="m_late",
            gps=(-1.0001, 35.0001),
        ),
        # Duplicate (mother, datetime) — daily-travel must dedupe.
        _visit(
            5,
            "flw_dupes",
            datetime(2024, 1, 14, 9, tzinfo=timezone.utc),
            mother_case_id="m_dup",
            gps=(-1.0, 35.0),
        ),
        _visit(
            6,
            "flw_dupes",
            datetime(2024, 1, 14, 9, tzinfo=timezone.utc),
            mother_case_id="m_dup",
            gps=(-1.0, 35.0),
        ),
        # Missing mother_case_id — must not break per-mother aggregations.
        _visit(
            7,
            "flw_no_mother",
            datetime(2024, 1, 15, 9, tzinfo=timezone.utc),
            mother_case_id=None,
            gps=(-1.0, 35.0),
        ),
        # bf_status = "ebf" exact match → counted as ebf
        _visit(
            8,
            "flw_ebf",
            datetime(2024, 1, 16, 9, tzinfo=timezone.utc),
            form_name="Post delivery visit",
            mother_case_id="m_ebf1",
            bf_status="ebf",
            gps=(-1.0, 35.0),
        ),
        # bf_status = "ebf bottle" → "ebf" is a token, must count as ebf per V1
        _visit(
            9,
            "flw_ebf",
            datetime(2024, 1, 17, 9, tzinfo=timezone.utc),
            form_name="Post delivery visit",
            mother_case_id="m_ebf2",
            bf_status="ebf bottle",
            gps=(-1.0, 35.0),
        ),
        # bf_status = "non-ebf" → must NOT count as ebf (substring isn't a token)
        _visit(
            10,
            "flw_ebf",
            datetime(2024, 1, 18, 9, tzinfo=timezone.utc),
            form_name="Post delivery visit",
            mother_case_id="m_ebf3",
            bf_status="non-ebf",
            gps=(-1.0, 35.0),
        ),
        # bf_status empty → not in denominator either
        _visit(
            11,
            "flw_ebf",
            datetime(2024, 1, 19, 9, tzinfo=timezone.utc),
            form_name="Post delivery visit",
            mother_case_id="m_ebf4",
            bf_status="",
            gps=(-1.0, 35.0),
        ),
        # All-same parity for fraud concentration → mode_share == 1.0
        _visit(
            12,
            "flw_fraud",
            datetime(2024, 1, 20, 9, tzinfo=timezone.utc),
            mother_case_id="m_f1",
            parity="G2P1",
            gps=(-1.0, 35.0),
        ),
        _visit(
            13,
            "flw_fraud",
            datetime(2024, 1, 21, 9, tzinfo=timezone.utc),
            mother_case_id="m_f2",
            parity="G2P1",
            gps=(-1.0, 35.0),
        ),
        _visit(
            14,
            "flw_fraud",
            datetime(2024, 1, 22, 9, tzinfo=timezone.utc),
            mother_case_id="m_f3",
            parity="G2P1",
            gps=(-1.0, 35.0),
        ),
        # Diverse parity → mode_share ≈ 0.33 (3 distinct parities, 3 visits)
        _visit(
            15,
            "flw_diverse",
            datetime(2024, 1, 23, 9, tzinfo=timezone.utc),
            mother_case_id="m_d1",
            parity="G1P0",
            gps=(-1.0, 35.0),
        ),
        _visit(
            16,
            "flw_diverse",
            datetime(2024, 1, 24, 9, tzinfo=timezone.utc),
            mother_case_id="m_d2",
            parity="G2P1",
            gps=(-1.0, 35.0),
        ),
        _visit(
            17,
            "flw_diverse",
            datetime(2024, 1, 25, 9, tzinfo=timezone.utc),
            mother_case_id="m_d3",
            parity="G3P2",
            gps=(-1.0, 35.0),
        ),
        # Visit with status="rejected" — exercises status filter
        _visit(
            18,
            "flw_rejected",
            datetime(2024, 1, 26, 9, tzinfo=timezone.utc),
            mother_case_id="m_rej",
            gps=(-1.0, 35.0),
            status="rejected",
        ),
    ]

    registrations = [
        _registration("case_001", "Mother One", expected_visits=["2024-01-10", "2024-02-10", "2024-03-10"]),
    ]
    return FixtureBundle(
        visits=visits,
        registrations=registrations,
        gs_forms=[],
        active_usernames=[
            "flw_no_gps",
            "flw_singletons",
            "flw_midnight",
            "flw_dupes",
            "flw_no_mother",
            "flw_ebf",
            "flw_fraud",
            "flw_diverse",
            "flw_rejected",
        ],
        flw_names={},
        flw_statuses={},
    )


__all__ = ["FixtureBundle", "small_realistic", "edge_cases"]
