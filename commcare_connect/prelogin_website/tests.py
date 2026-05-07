from django.test import TestCase
from django.urls import reverse


class PreloginWebsiteTests(TestCase):
    def test_home_renders(self):
        resp = self.client.get(reverse("prelogin_website:home"))
        assert resp.status_code == 200
        assert b"Connect by Dimagi" in resp.content
