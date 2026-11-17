"""The compact layout's pages: a Supply page drawn from templates/supply_chain/compact/ for a programme that chose it.

`process_template_response`, so it runs after a view has chosen its template and before it is drawn. Only a
TemplateResponse (every TemplateView and FormView) is swapped; a page drawn with render() keeps its usual template.
A page with no compact copy is drawn as it always is. See navigation.py, "THE COMPACT LAYOUT".
"""

from functools import lru_cache

from django.template import TemplateDoesNotExist
from django.template.loader import get_template

PREFIX = "supply_chain/"
COMPACT = "supply_chain/compact/"


@lru_cache(maxsize=512)
def compact_name(name: str) -> str | None:
    """The compact copy of a Supply template, or None when it has none."""
    if not name.startswith(PREFIX) or name.startswith(COMPACT):
        return None
    candidate = COMPACT + name[len(PREFIX) :]
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return None
    return candidate


class CompactLayoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_template_response(self, request, response):
        names = getattr(response, "template_name", None)
        if not names or not request.path.startswith("/supply/"):
            return response
        from connect_labs.supply_chain.navigation import compact_layout

        if not compact_layout(request):
            return response
        listed = [names] if isinstance(names, str) else list(names)
        swapped = [compact_name(n) or n for n in listed]
        if swapped != listed:
            response.template_name = swapped
        return response
