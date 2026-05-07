from django.conf import settings
from django.views.generic import TemplateView


class HomeView(TemplateView):
    template_name = "prelogin_website/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["app_login_url"] = getattr(settings, "PRELOGIN_APP_LOGIN_URL", "/labs/overview/")
        return ctx


home = HomeView.as_view()
