import pytest

from connect_labs.labs.integrations.connect.export_client import ExportAPIClient
from connect_labs.labs.integrations.connect.factory import get_export_client
from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.client import SyntheticExportClient
from connect_labs.labs.synthetic.models import SyntheticOpportunity


@pytest.fixture(autouse=True)
def _reset_singletons():
    from connect_labs.labs.integrations.connect import factory

    registry.invalidate_cache()
    factory.reset_fixture_store_singleton()
    yield
    registry.invalidate_cache()
    factory.reset_fixture_store_singleton()


@pytest.mark.django_db
def test_real_opp_returns_export_client():
    client = get_export_client(opportunity_id=1, access_token="tok")
    assert isinstance(client, ExportAPIClient)
    client.close()


@pytest.mark.django_db
def test_synthetic_opp_returns_synthetic_client(monkeypatch):
    SyntheticOpportunity.objects.create(opportunity_id=99, gdrive_folder_id="folder-a", enabled=True)

    # Avoid real Drive auth in the factory
    class FakeDrive:
        def list_folder(self, _):
            return {}

        def download_file(self, _):
            return b"[]"

    from connect_labs.labs.integrations.connect import factory

    monkeypatch.setattr(factory, "_build_drive_client", lambda: FakeDrive())

    client = get_export_client(opportunity_id=99, access_token="tok")
    assert isinstance(client, SyntheticExportClient)


@pytest.mark.django_db
def test_disabled_opp_returns_real_client(monkeypatch):
    SyntheticOpportunity.objects.create(opportunity_id=99, gdrive_folder_id="folder-a", enabled=False)

    client = get_export_client(opportunity_id=99, access_token="tok")
    assert isinstance(client, ExportAPIClient)
    client.close()
