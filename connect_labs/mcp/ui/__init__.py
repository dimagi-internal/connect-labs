"""MCP Apps (SEP-1865, ``io.modelcontextprotocol/ui``): Views this server serves.

A tool whose ``_meta.ui.resourceUri`` names one of these resources is rendered by a
host that speaks MCP Apps (canopy) as that View, in a sandboxed iframe. The View then
calls this server's tools through the host, as whoever is looking at it -- never as
the agent. See canopy-web ``docs/superpowers/specs/2026-10-08-mcp-apps-host-design.md``
("The host-app half: Labs").

Each View is one self-contained HTML5 document: inline script and style, no network
load of any kind. That is what the spec's restrictive default CSP allows (no
``_meta.ui.csp`` is declared), and it is why a picture reaches a View as a ``data:``
URI rather than a Labs link -- the View's opaque origin holds no Labs session.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: The extension id and the one content type it defines (SEP-1865).
UI_EXTENSION_ID = "io.modelcontextprotocol/ui"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"

#: What this server declares under ``capabilities.extensions`` (SEP-1724).
UI_EXTENSION_SETTINGS = {"mimeTypes": [MCP_APP_MIME_TYPE]}

#: The coaching preview: a workflow action's preview, the picture, and the buttons
#: that send it -- as the viewer.
WORKFLOW_ACTION_PREVIEW_URI = "ui://labs/workflow-action-preview"

_HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class UIResource:
    uri: str
    name: str
    description: str
    filename: str
    #: The resource's ``_meta.ui``. No ``csp``: the host's restrictive default applies.
    meta: dict

    def html(self) -> str:
        return _read(self.filename)


@cache
def _read(filename: str) -> str:
    return (_HERE / filename).read_text(encoding="utf-8")


RESOURCES: tuple[UIResource, ...] = (
    UIResource(
        uri=WORKFLOW_ACTION_PREVIEW_URI,
        name="workflow_action_preview",
        description=(
            "A workflow action's preview -- for coaching, the worker, the briefing topics, the "
            "opening message and the picture -- with the buttons that send it as the viewer."
        ),
        filename="workflow_action_preview.html",
        meta={"prefersBorder": True},
    ),
)


def tool_meta(resource_uri: str, *visibility: str) -> dict:
    """A tool's ``_meta`` linking it to a View: ``{"ui": {"resourceUri", "visibility"}}``.

    ``visibility`` is ``"model"`` and/or ``"app"``: ``("app",)`` is a tool only a View
    may call -- a host hides it from the agent and refuses the agent a call to it.
    Visibility is the host's to enforce, not a security boundary: every app-only tool
    still authorizes its caller itself."""
    assert visibility and set(visibility) <= {"model", "app"}, visibility
    return {"ui": {"resourceUri": resource_uri, "visibility": list(visibility)}}
