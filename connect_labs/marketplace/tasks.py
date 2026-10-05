"""Celery entry points for the marketplace."""

from __future__ import annotations

from config import celery_app


@celery_app.task(name="connect_labs.marketplace.tasks.warm_network")
def warm_network() -> int:
    """Recompute the network page's delivery answer before its cache lapses.

    It aggregates the whole pulse spine, which is seconds of work; on a timer
    that runs inside the cache's lifetime, a person clicking a filter never
    waits on it. Returns how many delivering partners it found.
    """
    from connect_labs.marketplace import queries

    return len(queries.spine_first_service(refresh=True))
