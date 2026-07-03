import os

import sentry_sdk
from celery import Celery
from celery.signals import task_retry

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("connect_labs")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Legacy package rename: commcare_connect -> connect_labs (PR #797).
LEGACY_TASK_PREFIX = "commcare_connect."
CURRENT_TASK_PREFIX = "connect_labs."


@app.on_after_finalize.connect
def register_legacy_task_aliases(sender, **kwargs):
    """Make every ``connect_labs.*`` task also resolvable under its historical
    ``commcare_connect.*`` name.

    PR #797 renamed the Python package ``commcare_connect`` -> ``connect_labs``,
    which also changed every Celery task's dotted name. A code rename does NOT
    touch persisted schedule state: the django-celery-beat ``PeriodicTask`` rows
    (this deployment uses the DatabaseScheduler) still reference the old
    ``commcare_connect.*`` task paths, so embedded beat keeps enqueuing messages
    under those names. When a message names a task the worker doesn't have in its
    strategy map, the consumer raises ``KeyError`` out of ``on_task_received`` and
    crash-restarts, dropping other in-flight/queued tasks.

    Registering the old name as an alias for the same task object guarantees a
    graceful handoff for any message still queued/scheduled under the old name.
    This is deliberately narrow (a name<->name alias) and does NOT swallow
    genuinely-unknown task errors. It is belt-and-suspenders with the one-time
    ``fix_stale_beat_tasks`` management command that rewrites the DB rows.
    """
    registry = sender.tasks
    for name in list(registry):
        if not name.startswith(CURRENT_TASK_PREFIX):
            continue
        legacy_name = LEGACY_TASK_PREFIX + name[len(CURRENT_TASK_PREFIX) :]
        if legacy_name not in registry:
            # TaskRegistry is a dict subclass; aliasing by direct assignment
            # points the old name at the same task instance without re-registering
            # (which would rename the task). The worker builds a consumer strategy
            # per registry key, so the old name gets its own strategy entry.
            registry[legacy_name] = registry[name]


@task_retry.connect
def sentry_log_retry(request=None, reason=None, einfo=None, **kwargs):
    with sentry_sdk.push_scope() as scope:
        if request:
            scope.set_tag("celery_task", request.task)
            scope.set_tag("retries", request.retries)
        if reason:
            scope.set_extra("reason", str(reason))
        sentry_sdk.capture_message("Celery task retrying", level="warning")
