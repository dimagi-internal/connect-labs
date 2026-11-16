"""Read a project through the CommCare HQ API, as the project's sync owner."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from connect_labs.hqdata.backends.base import BackendUnavailable
from connect_labs.hqdata.models import HQProject, HQSource
from connect_labs.hqdata.records import CaseRecord, FormRecord
from connect_labs.labs.integrations.commcare.api_client import CommCareDataAccess


def _hq_timestamp(moment: datetime) -> str:
    """Naive UTC, to the microsecond: what commcare-export sends HQ as ``indexed_on_start``."""
    from datetime import timezone as dt_timezone

    return moment.astimezone(dt_timezone.utc).replace(tzinfo=None).isoformat()


class _OwnerTokenAccess(CommCareDataAccess):
    """A headless client that re-reads its owner's token when HQ rejects the one it holds.

    A sync job runs for up to 35 minutes on one token. The base client has no session to
    refresh from, so a mid-walk 401 would end the job as NEEDS_REAUTH although the stored
    refresh token is fine. Here a 401 asks ``get_valid_cchq_access_token`` for the owner's
    current token (it refreshes under the per-user lock) and retries once if it changed.
    """

    def __init__(self, domain: str, owner, token: str):
        super().__init__(None, domain, cchq_access_token=token)
        self._owner = owner

    def _refresh_token(self) -> bool:
        from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError, get_valid_cchq_access_token

        try:
            fresh = get_valid_cchq_access_token(self._owner)
        except CCHQTokenError:
            return False
        if not fresh or fresh == self.access_token:
            return False
        self.access_token = fresh
        self.commcare_oauth = {"access_token": fresh}
        return True


class HQApiBackend:
    def __init__(self, access: CommCareDataAccess):
        self.access = access

    @classmethod
    def for_project(cls, project: HQProject) -> HQApiBackend:
        """A backend that authenticates as the project's sync owner. Raises BackendUnavailable without one."""
        from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError, get_valid_cchq_access_token

        if project.sync_owner_id is None:
            raise BackendUnavailable("This project has no sync owner, so there is no CommCare token to read it with.")
        try:
            token = get_valid_cchq_access_token(project.sync_owner)
        except CCHQTokenError as exc:
            raise BackendUnavailable(str(exc)) from exc
        return cls(_OwnerTokenAccess(project.domain, project.sync_owner, token))

    def iter_records(self, source: HQSource, since: datetime | None) -> Iterator[FormRecord | CaseRecord]:
        start = _hq_timestamp(since) if since else None
        if source.kind == HQSource.KIND_FORMS:
            for obj in self.access.iter_forms_since(start, xmlns=source.xmlns or None, app_id=source.app_id or None):
                yield FormRecord.from_hq(obj)
        else:
            for obj in self.access.iter_cases_since(start, case_type=source.case_type or None):
                yield CaseRecord.from_hq(obj)
