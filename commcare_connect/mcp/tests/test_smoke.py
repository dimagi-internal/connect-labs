import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_mcp_endpoint_rejects_get(client):
    """GET /mcp/ returns 405."""
    url = reverse("mcp:endpoint")
    response = client.get(url)
    assert response.status_code == 405
