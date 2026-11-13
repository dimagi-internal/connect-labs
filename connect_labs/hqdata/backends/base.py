"""What a sync needs from wherever the data lives."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from connect_labs.hqdata.models import HQSource
from connect_labs.hqdata.records import CaseRecord, FormRecord


class SourceBackend(Protocol):
    def iter_records(self, source: HQSource, since: datetime | None) -> Iterator[FormRecord | CaseRecord]:
        """Every record of the source's kind changed at or after ``since`` (all of them when None).

        Records must come ordered by ``indexed_on`` ascending, each carrying it: the sync
        checkpoints on the newest one it has committed, so an unordered walk would skip rows
        after a failure."""


class BackendUnavailable(Exception):
    """The project cannot be read right now (no credential, host refused). Not a data error."""
