from connect_labs.labs.synthetic.generator.fixtures.works import build_works_and_modules


def test_build_works_one_per_approved_visit():
    visits = [
        {"id": "v1", "username": "asha", "status": "approved", "deliver_unit_id": 1, "visit_date": "2026-02-05"},
        {"id": "v2", "username": "asha", "status": "rejected", "deliver_unit_id": 1, "visit_date": "2026-02-06"},
        {"id": "v3", "username": "ravi", "status": "approved", "deliver_unit_id": 2, "visit_date": "2026-02-06"},
    ]
    payment_units = [
        {"id": 1, "name": "PU1", "deliver_units": [1, 2]},
    ]
    works, modules = build_works_and_modules(visits, payment_units)
    # one completed work per approved visit
    work_ids = {w["id"] for w in works}
    assert {"v1-cw", "v3-cw"}.issubset(work_ids)
    assert "v2-cw" not in work_ids
    # modules: one per (username, payment unit)
    assert {(m["username"], m["payment_unit_id"]) for m in modules} == {
        ("asha", 1),
        ("ravi", 1),
    }


def test_build_works_returns_lists():
    works, modules = build_works_and_modules([], [])
    assert works == []
    assert modules == []


def test_over_limit_visits_are_payable_like_approved():
    """over_limit is legitimate PAID work — a budget-cap label, not a rejection. Skipping
    it understated payment totals alongside the visit counts."""
    visits = [
        {"id": "v1", "username": "asha", "status": "approved", "deliver_unit_id": 1, "visit_date": "2026-02-05"},
        {"id": "v2", "username": "asha", "status": "over_limit", "deliver_unit_id": 1, "visit_date": "2026-02-06"},
        {"id": "v3", "username": "asha", "status": "rejected", "deliver_unit_id": 1, "visit_date": "2026-02-07"},
        {"id": "v4", "username": "asha", "status": "pending", "deliver_unit_id": 1, "visit_date": "2026-02-08"},
    ]
    pus = [{"id": 10, "name": "PU1", "deliver_units": [1]}]
    works, _ = build_works_and_modules(visits, pus)
    assert {w["id"] for w in works} == {"v1-cw", "v2-cw"}
