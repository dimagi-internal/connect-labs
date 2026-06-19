import pytest

from commcare_connect.campaign.services import seed, serializers


@pytest.mark.django_db
def test_report_days_serialized():
    c = seed.seed_campaign(fresh=True)
    p = serializers.bootstrap_payload(c)
    assert len(p["REPORT_DAYS"]) == 16
    d0 = p["REPORT_DAYS"][0]
    assert set(d0.keys()) == {"day", "enrolled", "attended", "paid"}
    assert d0["day"] == "D1"
