"""The ``personal_page_*`` tools: a page one person owns, shared with the people and scopes they choose.

A personal page (workflow/personal_pages.py) belongs to no organisation, programme or
opportunity. It lives at ``/labs/p/user/<owner>/<slug>/``, reads no data of its own (its
links go to other pages, which check their own access), and opens for its owner, Dimagi
staff, the people named in its share list and anyone who may use a scope in it. Only its
owner (and staff) change it. These tools act as the signed-in person; a page they may not
open is "not found".
"""

from __future__ import annotations

from django.conf import settings

from connect_labs.workflow import personal_pages

from ..tool_registry import MCPToolError, register

_SLUG = {"type": "string", "description": "The page's address under its owner: /labs/p/user/<owner>/<slug>/."}
_OWNER = {"type": "string", "description": "The owner's username. Default: you."}
_PEOPLE = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Usernames or emails who may open the page. Replaces the list; [] empties it.",
}
_SCOPES = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Scopes whose members may open the page: 'program:<id>', 'opportunity:<id>', 'organization:<slug>'. "
        "Replaces the list; [] empties it. You must be in each scope you add."
    ),
}


def _token(user) -> str | None:
    """The person's Connect token, for checking scope membership; None if they have none (names still work)."""
    from connect_labs.labs.connect_tokens import get_valid_access_token

    try:
        return get_valid_access_token(user)
    except Exception:  # noqa: BLE001 -- no token is "scopes cannot be checked", which then refuses them
        return None


def _absolute(path: str) -> str:
    return (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/") + path


def _describe(page, definition=None) -> dict:
    out = personal_pages.summary(page, definition)
    out["url"] = _absolute(out["url"])
    return out


def _page(user, slug, owner=None, token=None):
    """The page, if ``user`` may open it; else NOT_FOUND (the same answer as no such page)."""
    if not isinstance(slug, str) or not slug:
        raise MCPToolError("INVALID_SCHEMA", "slug is the page's address, e.g. 'demo'.")
    page = personal_pages.find(owner or user.username, slug)
    if page is None or personal_pages.view_refusal(user, page, access_token=token):
        raise MCPToolError("NOT_FOUND", "No such page.")
    return page


def _guard(call):
    try:
        return call()
    except personal_pages.PageProblem as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))
    except personal_pages.PageNotFound as exc:
        raise MCPToolError("NOT_FOUND", str(exc))
    except PermissionError as exc:
        raise MCPToolError("PERMISSION_DENIED", str(exc))


@register(
    name="personal_page_create",
    description=(
        "Create a page of your own -- not an organisation's, programme's or opportunity's -- at "
        "/labs/p/user/<you>/<slug>/ (e.g. a launcher of links to other labs pages). It reads no data of its own: "
        "render_code is JSX defining function WorkflowUI({definition, scope}), drawn in the page sandbox, where "
        "scope is {type: 'user', key: <owner>, user: <viewer>}. Shared with no one unless you pass people "
        "(usernames/emails) and/or scopes ('program:<id>', 'opportunity:<id>', 'organization:<slug>'); change that "
        "later with personal_page_share. Only you (and Dimagi staff) can change it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slug": _SLUG,
            "name": {"type": "string", "description": "The page's title (at most 120 characters)."},
            "description": {"type": "string"},
            "render_code": {"type": "string", "description": "JSX; a starter is used if omitted."},
            "people": _PEOPLE,
            "scopes": _SCOPES,
        },
        "required": ["slug", "name"],
        "additionalProperties": False,
    },
    is_write=True,
)
def personal_page_create(user, slug=None, name=None, description="", render_code=None, people=None, scopes=None):
    token = _token(user)

    def run():
        page = personal_pages.create(
            user, slug=slug, name=name, description=description, render_code=render_code, access_token=token
        )
        if people is not None or scopes is not None:
            personal_pages.set_sharing(user, page, people=people, scopes=scopes, access_token=token)
        loaded = personal_pages.load(user, page, access_token=token)
        return _describe(page, loaded[0] if loaded else None)

    return _guard(run)


@register(
    name="personal_page_list",
    description="Your own pages (personal_page_create) with who each is shared with, and the pages shared with you.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def personal_page_list(user):
    token = _token(user)
    return {
        "mine": [_describe(p) for p in personal_pages.owned_by(user)],
        "shared_with_me": [_describe(p) for p in personal_pages.shared_with(user, access_token=token)],
    }


@register(
    name="personal_page_get",
    description="One personal page: its address, share list, definition and render code (JSX).",
    input_schema={
        "type": "object",
        "properties": {"slug": _SLUG, "owner": _OWNER},
        "required": ["slug"],
        "additionalProperties": False,
    },
)
def personal_page_get(user, slug=None, owner=None):
    token = _token(user)
    page = _page(user, slug, owner, token)
    loaded = _guard(lambda: personal_pages.load(user, page, access_token=token))
    if loaded is None:
        raise MCPToolError("NOT_FOUND", "No such page.")
    definition, render_code = loaded
    return {
        **_describe(page, definition),
        "can_edit": personal_pages.edit_refusal(user, page) is None,
        "definition": definition.data,
        "render_code": render_code,
    }


@register(
    name="personal_page_update",
    description=(
        "Change a personal page you own. Send only what changes: name, description, new_slug (moves its address), "
        "render_code (replaces the JSX)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slug": _SLUG,
            "owner": _OWNER,
            "name": {"type": "string"},
            "description": {"type": "string"},
            "new_slug": {"type": "string"},
            "render_code": {"type": "string"},
        },
        "required": ["slug"],
        "additionalProperties": False,
    },
    is_write=True,
)
def personal_page_update(user, slug=None, owner=None, name=None, description=None, new_slug=None, render_code=None):
    token = _token(user)
    page = _page(user, slug, owner, token)

    def run():
        personal_pages.update(
            user,
            page,
            name=name,
            description=description,
            slug=new_slug,
            render_code=render_code,
            access_token=token,
        )
        loaded = personal_pages.load(user, page, access_token=token)
        return _describe(page, loaded[0] if loaded else None)

    return _guard(run)


@register(
    name="personal_page_share",
    description=(
        "Set who may open a personal page you own: people (usernames/emails) and/or scopes "
        "('program:<id>', 'opportunity:<id>', 'organization:<slug>'), whose members may then open it. Each list you "
        "send replaces that list ([] empties it); a list you leave out is kept. They can open it, never change it."
    ),
    input_schema={
        "type": "object",
        "properties": {"slug": _SLUG, "owner": _OWNER, "people": _PEOPLE, "scopes": _SCOPES},
        "required": ["slug"],
        "additionalProperties": False,
    },
    is_write=True,
)
def personal_page_share(user, slug=None, owner=None, people=None, scopes=None):
    token = _token(user)
    page = _page(user, slug, owner, token)
    _guard(lambda: personal_pages.set_sharing(user, page, people=people, scopes=scopes, access_token=token))
    return _describe(page)


@register(
    name="personal_page_delete",
    description="Delete a personal page you own (a backup of its definition and code is kept).",
    input_schema={
        "type": "object",
        "properties": {"slug": _SLUG, "owner": _OWNER},
        "required": ["slug"],
        "additionalProperties": False,
    },
    is_write=True,
)
def personal_page_delete(user, slug=None, owner=None):
    token = _token(user)
    page = _page(user, slug, owner, token)
    described = _describe(page)
    _guard(lambda: personal_pages.delete(user, page, access_token=token))
    return {"deleted": True, **described}
