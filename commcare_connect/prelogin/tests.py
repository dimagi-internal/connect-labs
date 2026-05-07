from django.test import TestCase
from django.urls import reverse


class PreloginTests(TestCase):
    def test_home_renders(self):
        resp = self.client.get(reverse("prelogin:home"))
        assert resp.status_code == 200
        assert b"Connect by Dimagi" in resp.content
