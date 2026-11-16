"""Timed jobs for the effectiveness models."""

from __future__ import annotations

import logging

from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="connect_labs.effectiveness.tasks.refresh_opportunity_places")
def refresh_opportunity_places() -> int:
    """Place every modelled opportunity's approved visits in their most local areas (``effectiveness.places``).

    About a minute of database time on production, so it runs every six hours
    rather than when a page asks. New visits within those hours change an
    opportunity's shares only slightly; a new opportunity is valued at its
    country's figures until the next run, and its page says so.
    """
    from connect_labs.effectiveness import implementation as cea
    from connect_labs.effectiveness import places
    from connect_labs.pulse.models import PulseOpportunity

    opps = {
        o.opportunity_id: cea.iso3_for(o.country)
        for o in PulseOpportunity.objects.filter(is_test=False)
        if cea.SERVICE_MODEL.get(o.service_slug or "") and cea.iso3_for(o.country)
    }
    value = places.refresh(opps)
    logger.info("[effectiveness] placed %s opportunities", len(value["opps"]))
    return len(value["opps"])


@celery_app.task(name="connect_labs.effectiveness.tasks.check_model_sheets")
def check_model_sheets() -> list[str]:
    """Has a live model Sheet moved away from the vendored spec? Daily; the answer is shown on the models page.

    A drifted Sheet is an ERROR log, which Sentry reports, because someone has to run
    ``effectiveness_vendor`` and commit the refresh. A check that could not read a Sheet raises, so a broken
    credential is a failed task rather than a quiet "no drift".
    """
    from connect_labs.effectiveness import sheet_drift

    status = sheet_drift.record(sheet_drift.check_all())
    if status["drifted"]:
        logger.error(
            "[effectiveness] live model Sheet(s) differ from the vendored spec: %s. "
            "Refresh with effectiveness_vendor and commit.",
            ", ".join(status["drifted"]),
        )
    return status["drifted"]
