from django.test import TestCase, override_settings
from django.urls import reverse


class PreloginTests(TestCase):
    def test_home_renders(self):
        resp = self.client.get(reverse("prelogin:home"))
        assert resp.status_code == 200
        assert b"Connect by Dimagi" in resp.content

    def test_login_url_in_context_defaults_to_labs_overview(self):
        resp = self.client.get(reverse("prelogin:home"))
        assert resp.context["app_login_url"] == "/labs/overview/"

    def test_login_url_in_context_respects_setting_override(self):
        with override_settings(PRELOGIN_APP_LOGIN_URL="/custom/login/"):
            resp = self.client.get(reverse("prelogin:home"))
            assert resp.context["app_login_url"] == "/custom/login/"
