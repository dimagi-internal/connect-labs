"""Per-page options for the application header.

The header is shared by every labs page, and most of it is right everywhere.
Two pieces are not:

* the **labs context selector** (opportunity / program / organization) is what
  a workflow or audit page is scoped by, and meaningless on a page that is not
  scoped by it — the marketplace, for one, where it was the largest thing on a
  phone screen. Shown unless a page opts out.
  A page that keeps the selector but reads its data from somewhere else (its
  own URL, a saved space, every programme at once) says so, and the selector
  shows a muted "not used on this page" instead of a selection that looks
  applied but is not. The person's saved context is untouched and the menu
  still opens: what they apply there is kept for the pages that do use it.
* the **Pulse widget** — live delivery in one line, clicking through to the
  night map. Shown only where a page opts in.

A page declares its choices with the decorator, next to its other decorators::

    @login_required
    @page_chrome(labs_context=False, pulse_widget=True)
    def home(request): ...

``labs_context`` is one of:

* ``True`` (``APPLIED``, the default) -- the page is scoped by the selection;
* ``NOT_USED`` -- the selector shows, muted, as not applied to this page;
* ``STARTED_FROM`` -- the page opened on the selection but chooses its own
  data from there (the Data page with nothing in its URL);
* ``False`` (``HIDDEN``) -- no selector at all.

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

APPLIED = True
HIDDEN = False
NOT_USED = "not_used"
STARTED_FROM = "started_from"
LABS_CONTEXT_STATES = (APPLIED, HIDDEN, NOT_USED, STARTED_FROM)


@dataclass(frozen=True)
class PageChrome:
    labs_context: bool | str = APPLIED
    pulse_widget: bool = False

    def __post_init__(self):
        # A real bool, not 1 or 0 (which compare equal to one): those are typos, not states.
        if not (isinstance(self.labs_context, bool) or self.labs_context in (NOT_USED, STARTED_FROM)):
            raise ValueError(f"labs_context must be one of {LABS_CONTEXT_STATES!r}, not {self.labs_context!r}")

    @property
    def labs_context_not_used(self) -> bool:
        return self.labs_context == NOT_USED

    @property
    def labs_context_started_from(self) -> bool:
        return self.labs_context == STARTED_FROM


DEFAULT = PageChrome()


def page_chrome(**options):
    """Declare how the header renders on this page.

    Options are PageChrome's fields; an unknown one (or an unknown
    ``labs_context`` state) raises at import time, where a typo is found,
    rather than rendering the default in silence.
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
