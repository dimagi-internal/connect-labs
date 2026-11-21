from unittest import mock

import pytest

from connect_labs.workers import tasks

pytestmark = pytest.mark.django_db


def test_scheduled_sync_is_off_by_default(settings):
    settings.WORKERS_SYNC_ENABLED = False
    with mock.patch.object(tasks, "_fresh_source") as source, mock.patch.object(tasks, "due_opportunities") as due:
        assert tasks.sync_due() == {"disabled": True}
        assert tasks.refresh_opportunities() == {"disabled": True}
    source.assert_not_called()
    due.assert_not_called()


def test_scheduled_sync_runs_when_switched_on(settings):
    settings.WORKERS_SYNC_ENABLED = True
    with mock.patch.object(tasks, "due_opportunities", return_value=[]):
        assert tasks.sync_due() == {"synced": [], "failed": {}, "skipped": 0}
