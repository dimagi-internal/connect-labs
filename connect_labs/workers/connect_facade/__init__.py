"""The Connect facade: the one place labs reads Connect Workers from Connect.

Callers ask `get_source()` and use the `ConnectWorkerSource` interface. Today every
method is served by `ExportFacade`, which works around the export API's gaps
(see its module docstring and docs/connect-asks.md). When Connect ships one of the
asked-for endpoints, add a `NativeConnectSource` declaring that method in its
`capabilities.native` to `NATIVE_SOURCES`; `RoutedSource` switches that method to it
and leaves the rest on the facade.
"""

from __future__ import annotations

from .base import (
    ACCESSIBLE_OPPORTUNITIES,
    ASSESSMENT,
    ASSIGNED_TASKS,
    AUDIT_REPORT_ENTRIES,
    COMPLETED_MODULE,
    COMPLETED_WORKS,
    ENTITY_KINDS,
    EVENTS,
    LEARN_MODULES,
    METHOD_KEYS,
    NOTIFICATIONS,
    OPPORTUNITY_WORKERS,
    PAYMENT,
    TASK_TYPES,
    USER_DATA,
    VISITS_THIN,
    WORK_AREAS,
    WORKER_OPPORTUNITIES,
    AccessibleOpportunities,
    Capabilities,
    ConnectWorkerSource,
    Coverage,
    OpportunityRef,
    Result,
    RoutedSource,
    entities_key,
)
from .export import ExportFacade

#: Sources that serve some methods natively, consulted first. Empty until Connect ships an endpoint.
NATIVE_SOURCES: list = []


def get_source(client=None, *, visit_fetch: bool = False) -> RoutedSource:
    """The source the sync and the profile read through.

    ``client``: an `ExportAPIClient`; default the sync identity's (`workers.identity`),
    which raises `WorkerSyncAuthError` when there is none.
    """
    if client is None:
        from connect_labs.workers.identity import get_sync_client

        client = get_sync_client()
    fallback = ExportFacade(client, visit_fetch=visit_fetch)
    return RoutedSource([factory(client) for factory in NATIVE_SOURCES], fallback)


__all__ = [
    "ACCESSIBLE_OPPORTUNITIES",
    "ASSESSMENT",
    "ASSIGNED_TASKS",
    "AUDIT_REPORT_ENTRIES",
    "COMPLETED_MODULE",
    "COMPLETED_WORKS",
    "ENTITY_KINDS",
    "EVENTS",
    "LEARN_MODULES",
    "METHOD_KEYS",
    "NOTIFICATIONS",
    "OPPORTUNITY_WORKERS",
    "PAYMENT",
    "TASK_TYPES",
    "USER_DATA",
    "VISITS_THIN",
    "WORK_AREAS",
    "WORKER_OPPORTUNITIES",
    "AccessibleOpportunities",
    "Capabilities",
    "ConnectWorkerSource",
    "Coverage",
    "ExportFacade",
    "OpportunityRef",
    "Result",
    "RoutedSource",
    "entities_key",
    "get_source",
]
