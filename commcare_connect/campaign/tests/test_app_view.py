import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_ping_is_wired(client):
    resp = client.get(reverse("campaign:ping"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
