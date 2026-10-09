"""MCP Apps (SEP-1865): the extension, the coaching View, and who may call what.

The protocol half needs no database: an in-memory client drives the real server's
initialize / tools/list / resources. What the tools DO is covered with the rest of
the workflow run tools (``test_workflow_run_tools.py``).
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

from fastmcp import Client

from connect_labs.mcp.server import UIExtensionMiddleware, mcp
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.mcp.tool_registry import list_tools as registry_list_tools
from connect_labs.mcp.ui import (
    MCP_APP_MIME_TYPE,
    RESOURCES,
    UI_EXTENSION_ID,
    WORKFLOW_ACTION_PREVIEW_URI,
)

VIEW_URI = WORKFLOW_ACTION_PREVIEW_URI


def _session():
    async def run():
        async with Client(mcp) as client:
            return (
                None,
                {t.name: t for t in await client.list_tools()},
                await client.list_resources(),
                await client.read_resource(VIEW_URI),
            )

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------


def _over_http(monkeypatch, body: dict, headers: dict | None = None) -> dict:
    """POST one JSON-RPC message to the real Streamable-HTTP app, the bearer accepted."""
    import json

    import httpx
    from fastmcp.server.auth import AccessToken

    from connect_labs.mcp.server import CommCarePATVerifier, build_http_app

    async def accept(self, token):
        return AccessToken(token=token, client_id="t", scopes=["mcp"], claims={"sub": "1", "user_id": 1})

    monkeypatch.setattr(CommCarePATVerifier, "verify_token", accept)
    app = build_http_app()

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://labs") as c:
                return await c.post(
                    "/",
                    json=body,
                    headers={
                        "authorization": "Bearer x",
                        "accept": "application/json, text/event-stream",
                        "content-type": "application/json",
                        **(headers or {}),
                    },
                )

    response = asyncio.run(run())
    assert response.status_code == 200, response.text
    text = response.text
    if text.lstrip().startswith("event:"):
        text = next(line[5:] for line in text.splitlines() if line.startswith("data:"))
    return json.loads(text)["result"]


def test_the_server_declares_the_ui_extension_on_a_handshake(monkeypatch):
    """A client that negotiates the extension in initialize sees it declared back --
    on a 2025-era handshake too, whose schema the SDK would otherwise sieve it out of."""
    for version in ("2025-06-18", "2025-11-25"):
        result = _over_http(
            monkeypatch,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": version,
                    "capabilities": {"extensions": {UI_EXTENSION_ID: {"mimeTypes": [MCP_APP_MIME_TYPE]}}},
                    "clientInfo": {"name": "canopy", "version": "1"},
                },
            },
        )
        assert result["capabilities"]["extensions"][UI_EXTENSION_ID] == {"mimeTypes": [MCP_APP_MIME_TYPE]}, version


def test_the_server_declares_the_ui_extension_on_discover(monkeypatch):
    result = _over_http(
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {"name": "canopy", "version": "1"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        headers={"mcp-protocol-version": "2026-07-28", "mcp-method": "server/discover"},
    )
    assert result["capabilities"]["extensions"][UI_EXTENSION_ID] == {"mimeTypes": [MCP_APP_MIME_TYPE]}


def test_the_declaration_survives_a_handshake_eras_capabilities():
    """A 2025-era initialize result is sieved to a schema with no extensions field;
    the middleware puts the declaration back, keeping any other extension."""
    import mcp.types as mt

    caps = mt.ServerCapabilities(extensions={"other/ext": {"a": 1}})
    result = mt.InitializeResult(
        protocol_version="2025-06-18",
        capabilities=caps,
        server_info=mt.Implementation(name="t", version="1"),
    )

    async def call_next(_context):
        return result

    out = asyncio.run(UIExtensionMiddleware().on_initialize(None, call_next))
    assert out.capabilities.extensions == {
        "other/ext": {"a": 1},
        UI_EXTENSION_ID: {"mimeTypes": [MCP_APP_MIME_TYPE]},
    }


# ---------------------------------------------------------------------------
# Tools and their visibility
# ---------------------------------------------------------------------------


def test_workflow_run_action_is_rendered_by_the_view_and_callable_by_it():
    _, tools, *_ = _session()
    assert tools["workflow_run_action"].meta["ui"] == {"resourceUri": VIEW_URI, "visibility": ["model", "app"]}


def test_the_views_preview_tool_is_app_only():
    _, tools, *_ = _session()
    assert tools["workflow_action_preview_view"].meta["ui"] == {"resourceUri": VIEW_URI, "visibility": ["app"]}
    # Listed (a host must see it to forward the View's call), hidden by the host.
    assert "deliver_to" in tools["workflow_action_preview_view"].input_schema["properties"]


def test_only_view_tools_carry_ui_meta():
    with_ui = {t["name"] for t in registry_list_tools() if "ui" in (t.get("_meta") or {})}
    assert with_ui == {"workflow_run_action", "workflow_action_preview_view"}


def test_the_views_preview_tool_reads_and_the_commit_writes():
    assert get_tool("workflow_action_preview_view").is_write is False
    assert get_tool("workflow_run_action").is_write is True


def test_the_agents_description_says_coaching_is_sent_by_a_click():
    text = get_tool("workflow_run_action").description
    assert "SENT BY THE PERSON'S CLICK, NEVER BY YOU" in text
    assert "Start coaching" in text and "Send to me (QA test)" in text


def test_the_agents_description_has_it_preview_at_once_and_leave_the_briefing_to_labs():
    """Seen live (2026-10-09): asked to start coaching, the agent sent the person to the
    page's button instead of previewing, and when it did preview it wrote each worker's own
    `prompt` -- which then dropped the picture and the fixed opening (no longer: Labs keeps
    it inside the briefing) -- then read the synthetic
    stand-in bot as a mismatch."""
    text = get_tool("workflow_run_action").description
    assert "PREVIEW STRAIGHT AWAY" in text
    assert "always sends it to the workflow's own coach" in text and "edit freely" in text
    assert "sample stand-in" in text and "not a mismatch" in text
    assert "A synthetic preview also has no `opening`" in text


def test_after_previewing_the_agent_says_one_line_not_the_card():
    """Owner, 2026-10-09: the chat restated the card's topic, coach, synthetic note and
    options; it should be extremely minimal beyond the card."""
    from connect_labs.mcp.tools.workflow_run import CHAT_AFTER_PREVIEW, HOW_TO_RUN_CLICK_TO_SEND

    assert "ONE short line" in CHAT_AFTER_PREVIEW and "do not restate" in CHAT_AFTER_PREVIEW
    assert CHAT_AFTER_PREVIEW in get_tool("workflow_run_action").description
    assert CHAT_AFTER_PREVIEW in HOW_TO_RUN_CLICK_TO_SEND
    assert "give each worker's `briefing` topics" not in get_tool("workflow_run_action").description


def test_the_view_tool_belongs_to_the_act_scope_and_no_read_scope():
    from connect_labs.labs import canopy

    holding = {scope for scope, tools in canopy.SCOPE_TOOLS.items() if "workflow_action_preview_view" in tools}
    assert holding == {"workflow:act"}


def test_a_restricted_caller_reaches_neither_coaching_tool():
    from connect_labs.mcp import token_scopes

    assert not {"workflow_run_action", "workflow_action_preview_view"} & token_scopes.RESTRICTED_TOOLS


# ---------------------------------------------------------------------------
# The resource
# ---------------------------------------------------------------------------


def test_the_view_is_listed_and_read_as_an_mcp_app():
    _, _, listed, contents = _session()
    entry = next(r for r in listed if str(r.uri) == VIEW_URI)
    assert entry.mime_type == MCP_APP_MIME_TYPE
    (content,) = contents
    assert content.mime_type == MCP_APP_MIME_TYPE
    assert content.text.lower().startswith("<!doctype html>")
    # No csp declared: the host's restrictive default applies.
    assert (content.meta or {}).get("ui") == {"prefersBorder": True}


def test_every_ui_resource_is_a_ui_uri_with_a_file():
    for resource in RESOURCES:
        assert resource.uri.startswith("ui://")
        assert resource.html().strip()


class _Doc(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.script = ""
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self._in_script = tag == "script"

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.script += data


def _view():
    doc = _Doc()
    doc.feed(RESOURCES[0].html())
    # Quote style is the formatter's (prettier), not the View's: compare on one.
    doc.script = doc.script.replace("'", '"')
    return doc


def _html():
    return RESOURCES[0].html().replace("'", '"')


def test_the_view_is_self_contained():
    """The default CSP allows nothing from the network: no external script, style,
    image, font or frame, and no fetch -- every call goes to the host."""
    doc = _view()
    for tag, attrs in doc.tags:
        assert not (tag == "script" and attrs.get("src")), "external script"
        assert tag not in ("link", "iframe", "object", "embed", "base"), tag
        assert not (tag == "img" and attrs.get("src")), "a static image"
    html = RESOURCES[0].html()
    assert not re.search(r"""(src|href)\s*=\s*["']?https?:""", html)
    assert "@import" not in html and "url(http" not in html
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "eval(", "new Function"):
        assert banned not in doc.script, banned


def test_the_view_speaks_the_mcp_apps_protocol():
    script = _view().script
    for method in (
        '"ui/initialize"',
        '"ui/notifications/initialized"',
        '"ui/notifications/tool-input"',
        '"ui/notifications/tool-result"',
        '"ui/notifications/host-context-changed"',
        '"ui/notifications/size-changed"',
        '"ui/update-model-context"',
        '"ui/message"',
        '"ui/open-link"',
        '"ui/resource-teardown"',
        '"tools/call"',
    ):
        assert method in script, method
    # It previews as the viewer and sends through the commit tool.
    assert '"workflow_action_preview_view"' in script and '"workflow_run_action"' in script
    # Only the parent window is listened to; only a PNG data: URI is drawn.
    assert "event.source !== window.parent" in script
    assert 'indexOf("data:image/png;base64,") === 0' in script
    # Buttons follow the host's say-so on acting for this viewer.
    assert "hostCapabilities.serverTools" in script


def test_the_view_has_one_send_and_a_one_click_test_send():
    """Owner, 2026-10-09: too many buttons to tell how to send. One primary Send to the
    worker, a quiet link to send a test instead, and in test mode only the username box,
    Send test (preview + send in one click) and Cancel."""
    html = _html()
    assert '"Send to " + workerName(p)' in html
    assert "Send a test to me instead" in html
    assert 'id="qa-send"' in html and ">Send test<" in html
    assert "Not yet" not in html and "Preview my test send" not in html
    assert "state.autoSend" in html
    assert "PersonalID username" in html
    assert "deliver_to" in html


def test_a_test_send_that_cannot_go_says_why_and_never_sticks_on_sending():
    """Live 2026-10-09: a test send by someone whose Labs account had no Open Chat Studio
    connection sat on "Sending…" -- the preview came back with `needs` and no token, and the
    card cleared its auto-send without redrawing; the reason was at the top, out of view."""
    script = _view().script
    assert "var wanted = state.autoSend;" in script
    assert "Connect Open Chat Studio in Labs first, then Send test again." in script


def test_a_spent_token_reads_as_already_sent_not_stale():
    """A double click must not loop through 'went stale' refreshes (2026-10-09)."""
    script = _view().script
    assert "/already confirmed/i.test(err.message)" in script
    assert "already confirmed|" not in script


def test_the_view_uses_the_hosts_theme_variables():
    html = _html()
    for var in ("--color-background-primary", "--color-text-primary", "--font-sans", "--border-radius-md"):
        assert f"var({var})" in html, var
    assert "light-dark(" in html
    # Legible at a ~380px panel and on a full page.
    assert 'name="viewport"' in html and "@media (min-width: 560px)" in html


def test_the_run_context_tells_the_agent_how_to_run_each_action():
    """The context is what an agent reads first; it must carry the click-to-send rule itself."""
    from connect_labs.mcp.tools.workflow_run import _with_how_to_run

    coach, task = _with_how_to_run([{"key": "c", "type": "start_ocs_outreach"}, {"key": "t", "type": "create_task"}])
    assert "PREVIEW it" in coach["how_to_run"] and "ONE short line" in coach["how_to_run"]
    assert "stays inside the briefing" in coach["how_to_run"]
    # Seen live 2026-10-09: "give me a data summary ... to initiate coaching" got a summary
    # ending "say so and I'll start it", and no card until a second message.
    assert "summary of a worker's data to start coaching" in coach["how_to_run"]
    assert "in that same reply" in coach["how_to_run"]
    assert "`confirm`" in task["how_to_run"] and "explicit yes" in task["how_to_run"]


def test_the_view_asks_for_open_chat_studio_before_anything_else():
    """Owner, 2026-10-09: check the OCS connection first, before the card shows anything."""
    script = _view().script
    assert "p.ocs.connected === false" in script and "renderConnectFirst(p)" in script
    assert "Connect Open Chat Studio first" in script


def test_a_send_tells_the_agent_to_confirm_not_to_look_it_up():
    """Live 2026-10-09: woken with 'follow it with workflow_action_status', the agent looked
    up a run made under the viewer's account, could not see it, and wrote paragraphs."""
    script = _view().script
    assert "follow it with workflow_action_status" not in script
    assert "do not look it up" in script and "one short line" in script


def test_the_card_carries_nothing_beyond_who_the_picture_the_opening_and_send():
    """Owner, 2026-10-09: the card was busier than it needed to be."""
    script = _view().script
    assert "Nothing is sent until you click Send." not in script
    assert '"Coach: " + p.bot.name' not in script and "Sent with the conversation." not in script
    assert "var items = [];" in script  # no history of earlier sends
    assert '"Coach " + one' in script
