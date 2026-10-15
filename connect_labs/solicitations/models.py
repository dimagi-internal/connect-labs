"""Solicitation models.

Every solicitation, response and review lives in the labs database
(``local_models.py``). They are re-exported here so Django registers them. The
LabsRecord proxies that used to live here served the prod store this app has
left; ``solicitations_import_records`` reads those records raw.
"""

from connect_labs.solicitations.local_models import (  # noqa: F401
    Solicitation,
    SolicitationResponse,
    SolicitationReview,
)
