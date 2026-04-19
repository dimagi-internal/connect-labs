import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_mcp_endpoint_reachable(client):
    """POST /mcp/ returns a 501 from the placeholder view."""
    url = reverse("mcp:endpoint")
    response = client.post(url, data="{}", content_type="application/json")
    assert response.status_code == 501
    assert response.json() == {"error": "not implemented"}


def test_mcp_endpoint_rejects_get(client):
    """GET /mcp/ returns 405."""
    url = reverse("mcp:endpoint")
    response = client.get(url)
    assert response.status_code == 405
