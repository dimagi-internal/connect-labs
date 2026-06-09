from django.conf import settings
from django.views.generic import TemplateView


class HomeView(TemplateView):
    template_name = "prelogin/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["app_login_url"] = getattr(settings, "PRELOGIN_APP_LOGIN_URL", "/accounts/login/")
        return ctx


class ContactView(HomeView):
    template_name = "prelogin/contact.html"


home = HomeView.as_view()
contact = ContactView.as_view()
