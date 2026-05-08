from django.urls import reverse


class TestPreloginHome:
    def test_renders_with_brand(self, client):
        resp = client.get(reverse("prelogin:home"))
        assert resp.status_code == 200
        assert b"Connect by Dimagi" in resp.content

    def test_login_url_defaults_to_accounts_login(self, client):
        resp = client.get(reverse("prelogin:home"))
        assert resp.context["app_login_url"] == "/accounts/login/"

    def test_login_url_respects_setting_override(self, client, settings):
        settings.PRELOGIN_APP_LOGIN_URL = "/custom/login/"
        resp = client.get(reverse("prelogin:home"))
        assert resp.context["app_login_url"] == "/custom/login/"
