"""
MBW Monitoring workflow template package.

DEPRECATED — legacy v1, retained only to serve a few pre-existing production
workflow instances. NOT a reference pattern; use `mbw_auditing_v5` for new MBW
work. It is registered but flagged `deprecated` so it's hidden from the
creatable-template list and cannot be instantiated anew. See DEPRECATED.md.

Exports the TEMPLATE dict for auto-discovery by the template registry.
"""

from connect_labs.workflow.templates.mbw_monitoring.template import TEMPLATE

__all__ = ["TEMPLATE"]
