"""Per-page options for the application header.

The header is shared by every labs page, and most of it is right everywhere.
Two pieces are not:

* the **labs context selector** (opportunity / program / organization) is what
  a workflow or audit page is scoped by, and meaningless on a page that is not
  scoped by it — the marketplace, for one, where it was the largest thing on a
  phone screen. Shown unless a page opts out.
* the **Pulse widget** — live delivery in one line, clicking through to the
  night map. Shown only where a page opts in.

A page declares its choices with the decorator, next to its other decorators::

    @login_required
    @page_chrome(labs_context=False, pulse_widget=True)
    def home(request): ...

Class-based views take it in the URLconf, ``page_chrome(...)(View.as_view())``,
or through ``method_decorator``. A view can also put a ``PageChrome`` in its own
template context under ``page_chrome``; view context beats context processors,
so that wins over the decorator.

Not to be confused with presentation mode (``presentation.py``), which drops
the whole shell for a link shared with an outside audience. That is a property
of the LINK; this is a property of the PAGE.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps


@dataclass(frozen=True)
class PageChrome:
    labs_context: bool = True
    pulse_widget: bool = False


DEFAULT = PageChrome()


def page_chrome(**options):
    """Declare how the header renders on this page.

    Options are PageChrome's fields; an unknown one raises at import time,
    where a typo is found, rather than rendering the default in silence.
    """
    chrome = PageChrome(**options)

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            request.page_chrome = chrome
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def chrome_context(request) -> dict:
    """Context processor: the page's header options, or the defaults."""
    return {"page_chrome": getattr(request, "page_chrome", DEFAULT)}
