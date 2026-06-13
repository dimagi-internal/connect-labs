from commcare_connect.labs.synthetic.generator.fixtures.manifest import FlwPersona, MeanStddev
from commcare_connect.labs.synthetic.generator.fixtures.user_data import build_user_data


def _p(pid, name, archetype="steady"):
    return FlwPersona(
        id=pid,
        display_name=name,
        archetype=archetype,
        accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
        flag_rate=0.05,
    )


def test_build_user_data_one_row_per_persona():
    visits = [
        {"username": "asha", "visit_date": "2026-02-15"},
        {"username": "asha", "visit_date": "2026-02-20"},
        {"username": "ravi", "visit_date": "2026-02-12"},
    ]
    rows = build_user_data([_p("asha", "Asha M."), _p("ravi", None)], visits)
    by_user = {r["username"]: r for r in rows}
    assert by_user["asha"]["name"] == "Asha M."
    assert by_user["ravi"]["name"] == "ravi"  # falls back to id
    assert by_user["asha"]["last_active"] == "2026-02-20"


def test_build_user_data_handles_no_visits():
    rows = build_user_data([_p("asha", "Asha M.")], [])
    assert len(rows) == 1
    assert rows[0]["last_active"] is None
