"""Supplier update links: a way in for an organisation with no labs login.

Gap G7 in docs/superpowers/specs/2026-09-23-supply-field-use-cases.md. Design
doc section 17.2 already ruled that a partner records through the same
operations we do; this is only the door. Every write behind it is an ordinary
`call_operation`, carrying `source="supplier_reported"` and the link's
organisation, so there is no second set of rules to keep in step.
"""
