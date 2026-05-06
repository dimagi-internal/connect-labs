from commcare_connect.labs.synthetic.generator.works import build_works_and_modules


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
