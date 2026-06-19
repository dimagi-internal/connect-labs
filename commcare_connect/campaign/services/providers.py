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

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


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
    """Stub: read the roster live from the CommCare HQ Case/Form API.

    Not implemented until real CommCare access (domain + deliver app) exists. Each
    method raises :class:`NotImplementedError` so wiring it in is an explicit,
    greppable TODO rather than a silently empty result. See issue #674.
    """

    def campaign(self):
        raise NotImplementedError("CommCareProvider.campaign: wire to the live CommCare campaign-case read")

    def regions(self):
        raise NotImplementedError("CommCareProvider.regions: wire to the live CommCare region/geography read")

    def donors(self):
        raise NotImplementedError("CommCareProvider.donors: wire to the live CommCare donor read")

    def worker_roles(self):
        raise NotImplementedError("CommCareProvider.worker_roles: wire to the live CommCare worker-role read")

    def workers(self):
        raise NotImplementedError("CommCareProvider.workers: wire to the live CommCare worker-case + KYC read")


_PROVIDERS = {
    "synthetic": SyntheticProvider,
    "commcare": CommCareProvider,
}


def get_provider(campaign) -> CampaignDataProvider:
    """Return the configured roster provider bound to ``campaign``."""
    name = getattr(settings, "CAMPAIGN_DATA_PROVIDER", "synthetic")
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError as exc:
        raise ImproperlyConfigured(f"CAMPAIGN_DATA_PROVIDER={name!r} is not one of {sorted(_PROVIDERS)}") from exc
    return provider_cls(campaign)
