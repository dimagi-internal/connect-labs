"""The expandable rail menu: a section's own navigation, a left rail beside the Labs sidebar.

A section (the marketplace today) declares its menu as groups of items, each item a URL
name. `build` resolves them for a request: the URLs, which item is current (the longest
matching path, so `/labs/pulse/cea/` lights "Cost-effectiveness ranking", not "Live
delivery"), and whether the page belongs to the menu at all. The template
`components/expandable_rail_menu.html` draws it; `base.html` includes it for any page a
section's menu claims, so a page joins a menu by being listed here, not by editing its
template.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class Item:
    label: str
    url_name: str
    # Extra path prefixes this item also owns (its drill-down pages), beyond its own URL.
    owns: tuple[str, ...] = ()
    # Shown only to a signed-in user who passes this check (request -> bool).
    visible: object = None


@dataclass(frozen=True)
class Group:
    label: str
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Menu:
    key: str  # ids, test ids and the browser's remembered open/closed choice
    title: str
    subtitle: str
    groups: tuple[Group, ...]
    # Paths under a listed item that must never carry the menu (public links, kiosk views).
    never: tuple[str, ...] = field(default=())
    # The Labs sidebar item that opens this section. On a page carrying the rail, clicking
    # it toggles the rail instead of navigating (Jonathan, 2026-11-06).
    sidebar_url_name: str = ""


def _url(name: str) -> str | None:
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def build(menu: Menu, request) -> dict | None:
    """The menu as the template reads it, or None when this page is not in it."""
    path = request.path
    if any(path.startswith(p) for p in menu.never):
        return None

    groups, best, best_len = [], None, -1
    for g in menu.groups:
        items = []
        for it in g.items:
            if it.visible is not None and not it.visible(request):
                continue
            url = _url(it.url_name)
            if url is None:
                continue
            row = {"label": it.label, "url": url, "active": False}
            for prefix in (url, *it.owns):
                # Longest prefix wins, so a section's landing page ("/labs/pulse/") only lights
                # for the pages no more specific item claims.
                if path.startswith(prefix) and len(prefix) > best_len:
                    best, best_len = row, len(prefix)
            items.append(row)
        if items:
            groups.append({"label": g.label, "items": items})

    if best is None:
        return None
    best["active"] = True
    return {
        "key": menu.key,
        "title": menu.title,
        "subtitle": menu.subtitle,
        "groups": groups,
        "sidebar_url": _url(menu.sidebar_url_name) if menu.sidebar_url_name else None,
    }
