"""Data-source seam for the CommCare-HQ-owned slice of a campaign.

The campaign **roster** — the campaign record, its regions, donors, worker roles, and
workers (with embedded KYC/identity) — is OWNED BY CommCare HQ per the Data Model
(workers are CommCare cases; KYC is the CommCare-owned Compliance dataset). Today it
lives in our own DB as synthetic data (cases via ``WorkerCase`` + the seed); eventually
it is read live from the CommCare Case/Form API. This module puts those reads behind a
small provider interface so that "go real" is a per-entity config flip, not a
serializer rewrite.

Tool-owned entities (Payment, Activity, Microplan, Reporting, AuditLog, Connection)
are deliberately NOT behind this seam — the tool authors them in its own DB and the
serializer reads them directly.

Select the active provider with the ``CAMPAIGN_DATA_PROVIDER`` setting:

    "synthetic" (default) -> SyntheticProvider, reads our ORM seed rows
    "commcare"            -> CommCareProvider, reads live CommCare Case/Form API (stub until access)

See issue #674 for the rollout plan.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from connect_labs.campaign.services import commcare_api, commcare_cases_backend


class CampaignDataProvider(ABC):
    """Read interface for the CommCare-HQ-owned roster of one campaign.

    Each method returns model-like objects the serializers already understand
    (duck-typed): a Region exposes ``region_id``/``name``/``lgas``/``plan``, a Worker
    the worker fields, etc. A real :class:`CommCareProvider` will build equivalent
    objects from CommCare Case/Form API responses so the serializers never change.
    """

    def __init__(self, campaign):
        self._campaign = campaign

    @abstractmethod
    def campaign(self):
        """The campaign record itself."""

    @abstractmethod
    def regions(self):
        """Regions for the campaign (each with its ``plan`` available)."""

    @abstractmethod
    def donors(self):
        """Donors funding the campaign."""

    @abstractmethod
    def worker_roles(self):
        """Worker role definitions (name + pay rate)."""

    @abstractmethod
    def workers(self):
        """Workers (FLWs) including their identity / KYC fields."""


class SyntheticProvider(CampaignDataProvider):
    """Current behavior: read the roster from our own ORM (synthetic seed rows)."""

    def campaign(self):
        return self._campaign

    def regions(self):
        # select_related('plan') so the PLANNING projection needs no extra query
        return list(self._campaign.regions.select_related("plan").all())

    def donors(self):
        return list(self._campaign.donors.all())

    def worker_roles(self):
        return list(self._campaign.worker_roles.all())

    def workers(self):
        return list(self._campaign.workers.all())


class CommCareProvider(CampaignDataProvider):
    """Read the roster from the campaign's CommCare project space via the Case API.

    Workers (with embedded KYC) are read as CommCare cases through
    :mod:`commcare_api` — served in-app from ``WorkerCase`` for a synthetic domain,
    or from real CommCare HQ otherwise; the campaign code is identical either way.
    The small reference data (campaign/regions/donors/worker-roles) is read from the
    tool's ORM, populated by the synthetic pipeline (and, later, a CommCare sync).
    """

    def __init__(self, campaign, request=None):
        super().__init__(campaign)
        self._request = request

    def campaign(self):
        return self._campaign

    def regions(self):
        return list(self._campaign.regions.select_related("plan").all())

    def donors(self):
        return list(self._campaign.donors.all())

    def worker_roles(self):
        return list(self._campaign.worker_roles.all())

    def workers(self):
        cases = commcare_api.fetch_cases(
            self._campaign.commcare_domain, commcare_cases_backend.WORKER_CASE_TYPE, request=self._request
        )
        return [SimpleNamespace(**case["properties"]) for case in cases]


def get_provider(campaign, request=None) -> CampaignDataProvider:
    """Pick the roster provider for ``campaign``.

    A campaign bound to a CommCare project space (``commcare_domain`` set) reads its
    roster through the Case API (:class:`CommCareProvider`); otherwise it uses the
    legacy local-ORM :class:`SyntheticProvider`. The ``CAMPAIGN_DATA_PROVIDER`` setting
    can force the choice for domain-less campaigns (used in tests)."""
    if campaign.commcare_domain:
        return CommCareProvider(campaign, request=request)
    name = getattr(settings, "CAMPAIGN_DATA_PROVIDER", "synthetic")
    if name == "commcare":
        return CommCareProvider(campaign, request=request)
    if name == "synthetic":
        return SyntheticProvider(campaign)
    raise ImproperlyConfigured(f"CAMPAIGN_DATA_PROVIDER={name!r} is not one of ['synthetic', 'commcare']")
