import pytest

from connect_labs.campaign.services import seed


@pytest.mark.django_db
def test_seed_creates_report_days():
    c = seed.seed_campaign(fresh=True)
    rows = list(c.report_days.all())
    assert len(rows) == 16
    assert [r.day for r in rows] == [f"D{i+1}" for i in range(16)]  # ordered
    for r in rows:
        assert r.enrolled > 0 and 0 < r.attended <= r.enrolled  # attendance is a fraction of enrolled
        assert r.paid > 0
