import datetime as dt

from commcare_connect.labs.synthetic.generator.manifest import FlwPersona, MeanStddev, Timeline
from commcare_connect.labs.synthetic.generator.timeline import VisitSlot, expand_visit_schedule


def _persona(pid, archetype):
    return FlwPersona(
        id=pid,
        archetype=archetype,
        accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
        flag_rate=0.05,
    )


def _timeline():
    return Timeline(
        start_date=dt.date(2026, 2, 1),
        end_date=dt.date(2026, 2, 28),
        weeks=4,
        visit_cadence_per_week_per_flw=MeanStddev(mean=8, stddev=0),
    )


def test_expand_visit_schedule_is_deterministic():
    personas = [_persona("asha", "rockstar"), _persona("ravi", "struggling")]
    a = expand_visit_schedule(_timeline(), personas, random_seed=42)
    b = expand_visit_schedule(_timeline(), personas, random_seed=42)
    assert a == b


def test_expand_visit_schedule_archetype_modulates_count():
    """Rockstars produce more visits than strugglers given the same cadence."""
    rockstars = [_persona("asha", "rockstar")]
    strugglers = [_persona("ravi", "struggling")]
    rs = expand_visit_schedule(_timeline(), rockstars, random_seed=42)
    st = expand_visit_schedule(_timeline(), strugglers, random_seed=42)
    assert len(rs) > len(st)


def test_visit_slots_are_within_timeline():
    personas = [_persona("asha", "rockstar")]
    slots = expand_visit_schedule(_timeline(), personas, random_seed=42)
    for slot in slots:
        assert isinstance(slot, VisitSlot)
        assert dt.date(2026, 2, 1) <= slot.visit_date <= dt.date(2026, 2, 28)
        assert 1 <= slot.week_index <= 4
