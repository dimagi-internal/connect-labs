"""Export client backed by a UserSyntheticDataset (PostgreSQL).

Drop-in replacement for ExportAPIClient and SyntheticExportClient.
Used by the factory when the requesting user has generated their own
synthetic data for an opportunity.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from connect_labs.labs.synthetic.client import SyntheticExportClient
from connect_labs.labs.synthetic.models import UserSyntheticDataset


class UserSyntheticExportClient:
    """Serves fixture rows from a UserSyntheticDataset stored in PostgreSQL."""

    def __init__(self, dataset: UserSyntheticDataset):
        self._fixtures = dataset.fixtures

    def paginate(self, endpoint: str, params: dict | None = None) -> Generator[list[dict[str, Any]], None, None]:
        key = SyntheticExportClient._endpoint_key(endpoint)
        data = self._fixtures.get(key, [])
        if isinstance(data, list):
            yield data
        elif isinstance(data, dict):
            yield [data]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def fetch_all(self, endpoint: str, params: dict | None = None) -> list[dict[str, Any]]:
        key = SyntheticExportClient._endpoint_key(endpoint)
        data = self._fixtures.get(key, [])
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
        return []


def get_user_synthetic_client(user, opportunity_id: int) -> UserSyntheticExportClient | None:
    """Return a client if the user has valid synthetic data for this opportunity, else None."""
    dataset = UserSyntheticDataset.for_user_and_opp(user, opportunity_id)
    if dataset is None:
        return None
    return UserSyntheticExportClient(dataset)
