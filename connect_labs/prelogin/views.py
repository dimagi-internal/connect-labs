from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.template.loader import get_template
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from .blog_manifest import extract_posts

# dimagi.com fetches the blog manifest cross-origin from this exact host.
DIMAGI_ORIGIN = "https://dimagi.com"


class HomeView(TemplateView):
    template_name = "prelogin/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["app_login_url"] = getattr(settings, "PRELOGIN_APP_LOGIN_URL", "/accounts/login/")
        return ctx


class ContactView(HomeView):
    template_name = "prelogin/contact.html"


class InActionView(HomeView):
    """"Connect in action" story pages.

    Standalone templates rather than SPA sections: each one carries its own
    stylesheet and scroll script, and none of it belongs in home.html's bundle.
    """

    template_name = "prelogin/in-action.html"


home = HomeView.as_view()
contact = ContactView.as_view()
in_action_nigeria_cholera = InActionView.as_view()


@require_GET
def blog_manifest(request):
    """JSON list of native Connect blog posts for dimagi.com to render as cards.

    See ``blog_manifest.py`` for why this reads the Insights markup directly and
    how ``data-origin`` drives dedupe on the dimagi.com side.
    """
    source = Path(get_template("prelogin/home.html").origin.name).read_text(encoding="utf-8")
    posts = extract_posts(source, static, request.build_absolute_uri)

    # Fail loudly rather than silently shipping an empty manifest: this parses
    # home.html's own markup, so a rename of the blog-card class or data-type
    # attribute would otherwise make dimagi.com's Connect cards quietly vanish
    # with nothing in any log to explain why.
    if not posts:
        raise RuntimeError(
            "blog/manifest.json extracted 0 posts from home.html. The Insights blog-card "
            'markup (class, data-type="blog", or the blog-tag/time/h3/excerpt structure '
            "blog_manifest.extract_posts parses) probably changed — update the regexes there."
        )

    response = JsonResponse({"generatedAt": timezone.now().isoformat(), "count": len(posts), "posts": posts})
    response["Access-Control-Allow-Origin"] = DIMAGI_ORIGIN
    response["Cache-Control"] = "public, max-age=300, stale-while-revalidate=86400"
    return response
