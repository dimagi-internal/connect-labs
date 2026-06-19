import pytest

from commcare_connect.campaign.models import Campaign, ReportDay, Workspace


@pytest.mark.django_db
def test_reportday_roundtrip():
    ws = Workspace.objects.create(country="Nigeria", name="Nigeria", slug="nigeria")
    c = Campaign.objects.create(workspace=ws, name="C", code="X")
    ReportDay.objects.create(campaign=c, day="D1", enrolled=120000, attended=105000, paid=92000, order=0)
    assert c.report_days.count() == 1
    assert c.report_days.first().day == "D1"
