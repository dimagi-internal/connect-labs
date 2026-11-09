"""Timed jobs for the impact models."""

from __future__ import annotations

import logging

from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="connect_labs.impact.tasks.refresh_opportunity_places")
def refresh_opportunity_places() -> int:
    """Place every modelled opportunity's approved visits in their most local areas (``impact.places``).

    About a minute of database time on production, so it runs every six hours
    rather than when a page asks. New visits within those hours change an
    opportunity's shares only slightly; a new opportunity is valued at its
    country's figures until the next run, and its page says so.
    """
    from connect_labs.impact import places
    from connect_labs.pulse import cea
    from connect_labs.pulse.models import PulseOpportunity

    ids = [
        o.opportunity_id
        for o in PulseOpportunity.objects.filter(is_test=False)
        if cea.SERVICE_MODEL.get(o.service_slug or "") and cea.iso3_for(o.country)
    ]
    value = places.refresh(ids)
    logger.info("[impact] placed %s opportunities", len(value["opps"]))
    return len(value["opps"])
