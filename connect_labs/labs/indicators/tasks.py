"""Celery tasks of the indicators app.

``app.autodiscover_tasks()`` imports only ``<app>.tasks`` for each installed app, so a task defined in a
subpackage is invisible to the worker unless it is imported here: a message naming it would be
rejected as an unregistered task.
"""

from connect_labs.labs.indicators.emod.tasks import run_pmc_model, sweep_dead_pmc_runs  # noqa: F401
