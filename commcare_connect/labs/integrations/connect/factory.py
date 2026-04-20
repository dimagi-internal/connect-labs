"""Factory that picks between real Connect export client and synthetic fixture client.

Call this from every location that today instantiates `ExportAPIClient(...)`
directly. For real opps it returns an unchanged `ExportAPIClient`. For opps
registered in the `synthetic` app it returns a `SyntheticExportClient` that
serves fixture JSON from Google Drive.
"""

from __future__ import annotations

from django.conf import settings

from commcare_connect.labs.integrations.connect.export_client import ExportAPIClient

# Module-level cache for the DriveClient + FixtureStore singletons.
_drive_client = None
_fixture_store = None


def _build_drive_client():
    from commcare_connect.labs.synthetic.gdrive import DriveClient

    return DriveClient()


def _get_fixture_store():
    global _drive_client, _fixture_store
    if _fixture_store is None:
        from commcare_connect.labs.synthetic.fixture_store import FixtureStore
        from commcare_connect.labs.synthetic.registry import get_synthetic_opp

        def folder_lookup(opp_id: int) -> str | None:
            row = get_synthetic_opp(opp_id)
            return row.gdrive_folder_id if row else None

        if _drive_client is None:
            _drive_client = _build_drive_client()
        _fixture_store = FixtureStore(drive=_drive_client, folder_lookup=folder_lookup)
    return _fixture_store


def reset_fixture_store_singleton() -> None:
    """For tests and for the reload UI — reset the cached store/client."""
    global _drive_client, _fixture_store
    _drive_client = None
    _fixture_store = None


def get_export_client(
    opportunity_id: int,
    access_token: str,
    timeout: float = 60.0,
):
    """Return an ExportAPIClient or SyntheticExportClient depending on registry state."""
    from commcare_connect.labs.synthetic.client import SyntheticExportClient
    from commcare_connect.labs.synthetic.registry import get_synthetic_opp

    synthetic = get_synthetic_opp(opportunity_id)
    if synthetic:
        return SyntheticExportClient(opp_id=opportunity_id, fixture_store=_get_fixture_store())

    return ExportAPIClient(
        base_url=settings.CONNECT_PRODUCTION_URL,
        access_token=access_token,
        timeout=timeout,
    )
