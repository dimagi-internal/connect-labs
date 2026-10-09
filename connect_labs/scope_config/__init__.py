"""Settings: how an organisation, a programme, an opportunity or a person has set labs up.

One config document per scope and namespace (models.py), layered
defaults <- organisation <- programme <- opportunity <- user (merge.py, scopes.py),
changed by members with attribution and undo (service.py), seen and edited on the
Settings page (views.py) and through the `labs_config_*` MCP tools.

Design: docs/superpowers/specs/2026-10-09-labs-scope-config-design.md. Config says
what is shown and what is wired to what; anything that changes a number (a
dispensing rule, a registry) stays domain data with its own history.
"""
