"""
Tests for the commcare_connect -> connect_labs (PR #797) Celery compatibility layer:

  * the on_after_finalize alias that lets old-named messages still resolve, and
  * the fix_stale_beat_tasks management command that repairs persisted
    django-celery-beat PeriodicTask rows.
"""

from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from config.celery_app import app, register_legacy_task_aliases


def test_alias_points_old_name_at_same_task():
    """A connect_labs.* task gets a commcare_connect.* alias pointing at the same object."""
    fake_task = object()
    sender = SimpleNamespace(tasks={"connect_labs.labs.tasks.cleanup_expired_sql_cache": fake_task})

    register_legacy_task_aliases(sender)

    legacy = "commcare_connect.labs.tasks.cleanup_expired_sql_cache"
    assert legacy in sender.tasks
    assert sender.tasks[legacy] is fake_task


def test_alias_does_not_overwrite_existing_and_ignores_foreign_names():
    existing = object()
    foreign = object()
    current = object()
    sender = SimpleNamespace(
        tasks={
            "connect_labs.labs.tasks.foo": current,
            "commcare_connect.labs.tasks.foo": existing,  # pre-existing, must be preserved
            "django_celery_beat.something": foreign,  # unrelated namespace, untouched
        }
    )

    register_legacy_task_aliases(sender)

    assert sender.tasks["commcare_connect.labs.tasks.foo"] is existing
    assert "commcare_connect.django_celery_beat.something" not in sender.tasks


def test_real_app_registers_legacy_alias_for_cleanup_task():
    """Integration: a real registered task resolves under its old name on the project app."""
    # Importing the module registers the task on the shared project app (the @celery_app.task
    # decorator runs at import time). At worker boot autodiscover does this for every app.
    import connect_labs.labs.tasks  # noqa: F401

    register_legacy_task_aliases(app)

    new_name = "connect_labs.labs.tasks.cleanup_expired_sql_cache"
    legacy_name = "commcare_connect.labs.tasks.cleanup_expired_sql_cache"
    assert new_name in app.tasks
    assert legacy_name in app.tasks
    assert app.tasks[legacy_name] is app.tasks[new_name]


@pytest.mark.django_db
def test_fix_stale_beat_tasks_rewrites_prefix():
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)
    stale = PeriodicTask.objects.create(
        name="cleanup-sql-cache",
        task="commcare_connect.labs.tasks.cleanup_expired_sql_cache",
        interval=schedule,
    )
    fresh = PeriodicTask.objects.create(
        name="already-migrated",
        task="connect_labs.labs.tasks.cleanup_expired_sql_cache",
        interval=schedule,
    )

    out = StringIO()
    call_command("fix_stale_beat_tasks", stdout=out)

    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.task == "connect_labs.labs.tasks.cleanup_expired_sql_cache"
    assert fresh.task == "connect_labs.labs.tasks.cleanup_expired_sql_cache"
    assert "Updated 1 PeriodicTask row(s)." in out.getvalue()


@pytest.mark.django_db
def test_fix_stale_beat_tasks_dry_run_makes_no_changes():
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)
    stale = PeriodicTask.objects.create(
        name="cleanup-sql-cache-dry",
        task="commcare_connect.labs.tasks.cleanup_expired_sql_cache",
        interval=schedule,
    )

    out = StringIO()
    call_command("fix_stale_beat_tasks", "--dry-run", stdout=out)

    stale.refresh_from_db()
    assert stale.task == "commcare_connect.labs.tasks.cleanup_expired_sql_cache"
    assert "Dry run" in out.getvalue()
