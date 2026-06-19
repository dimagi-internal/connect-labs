"""Data-source seam for the HQ/Connect-owned slice of a campaign.

The campaign **roster** — the campaign record, its regions, donors, worker roles, and
workers (with embedded KYC/identity) — is conceptually OWNED BY CommCare HQ / Connect.
Today it lives in our own DB as synthetic seed data; eventually it is read live from
Connect. This module puts those reads behind a small provider interface so that
"go real" is a per-entity config flip, not a serializer rewrite.

Tool-owned entities (Payment, Activity, Microplan, Reporting, AuditLog, Connection)
are deliberately NOT behind this seam — the tool authors them in its own DB and the
serializer reads them directly.

Select the active provider with the ``CAMPAIGN_DATA_PROVIDER`` setting:

    "synthetic" (default) -> SyntheticProvider, reads our ORM seed rows
    "connect"             -> ConnectProvider, reads live Connect (stub until staging)

See issue #674 for the rollout plan.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class CampaignDataProvider(ABC):
    """Read interface for the HQ/Connect-owned roster of one campaign.

    Each method returns model-like objects the serializers already understand
    (duck-typed): a Region exposes ``region_id``/``name``/``lgas``/``plan``, a Worker
    the worker fields, etc. A real :class:`ConnectProvider` will build equivalent
    objects from Connect API responses so the serializers never change.
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


class ConnectProvider(CampaignDataProvider):
    """Stub: read the roster live from CommCare HQ / Connect.

    Not implemented until a real staging environment + Connect access exist. Each
    method raises :class:`NotImplementedError` so wiring it in is an explicit,
    greppable TODO rather than a silently empty result. See issue #674.
    """

    def campaign(self):
        raise NotImplementedError("ConnectProvider.campaign: wire to the live Connect campaign read")

    def regions(self):
        raise NotImplementedError("ConnectProvider.regions: wire to the live Connect region read")

    def donors(self):
        raise NotImplementedError("ConnectProvider.donors: wire to the live Connect donor read")

    def worker_roles(self):
        raise NotImplementedError("ConnectProvider.worker_roles: wire to the live Connect deliver-unit read")

    def workers(self):
        raise NotImplementedError("ConnectProvider.workers: wire to the live Connect worker/KYC read")


_PROVIDERS = {
    "synthetic": SyntheticProvider,
    "connect": ConnectProvider,
}


def get_provider(campaign) -> CampaignDataProvider:
    """Return the configured roster provider bound to ``campaign``."""
    name = getattr(settings, "CAMPAIGN_DATA_PROVIDER", "synthetic")
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError as exc:
        raise ImproperlyConfigured(f"CAMPAIGN_DATA_PROVIDER={name!r} is not one of {sorted(_PROVIDERS)}") from exc
    return provider_cls(campaign)
