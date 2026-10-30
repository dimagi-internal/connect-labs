"""The field page's server side: reference data, idempotent counts, photos, the service worker.

What a phone does with them (the outbox, the retries, the scan) is exercised in a real browser in
``tests/e2e/test_field_page.py``.
"""

import io
import json
import re
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Document, StockCount
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests.test_update_link_send import FakeOCS
from connect_labs.supply_chain.tests.test_update_links import _world
from connect_labs.supply_chain.update_links import delivery
from connect_labs.supply_chain.update_links.models import UpdateLinkSubmission
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PROGRAM = 10731


@pytest.fixture
def world():
    return _world(SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM))


def _issue(world, **extra):
    da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, user=UserFactory(), caller=SYSTEM)
    return call_operation(
        "update_link_issue",
        da,
        {
            "data": {
                "org_id": world["eha"]["id"],
                "contract_ids": [world["contract"]["id"]],
                "supply_point_ids": [world["warehouse"]["id"]],
                "connect_username": "amina.bello",
                **extra,
            }
        },
    )


@pytest.fixture
def issued(world):
    return _issue(world)


def _u(name, issued):
    return reverse(f"supply_chain:update_link_field{name}", args=[issued["token"]])


def _entry(world, **over):
    return {
        "client_id": "e1234567890abcdef",
        "supply_point": world["warehouse"]["id"],
        "item": world["item"]["id"],
        "quantity": "12",
        "unit_basis": "pack",
        "counted_on": timezone.localdate().isoformat(),
        "batch": "B-7",
        "device_time": "2026-10-26T08:15:00Z",
        **over,
    }


def _post(client, issued, body, name="_count"):
    return client.post(_u(name, issued), data=json.dumps(body), content_type="application/json")


def _jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, "JPEG")
    return buffer.getvalue()


class TestThePage:
    def test_it_loads_from_nowhere_but_itself_and_points_at_its_own_pieces(self, client, issued):
        response = client.get(_u("", issued))
        assert response.status_code == 200
        html = response.content.decode()
        # No other host: no web font, no CDN, no analytics.
        assert not re.findall(r'(?:src|href)="https?://', html)
        assert "fonts.googleapis" not in html
        assert 'rel="manifest"' in html and "field.js" in html
        assert response["Cache-Control"].startswith("max-age=0") or "no-" in response["Cache-Control"]
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_a_link_that_asks_for_proof_sends_the_page_to_the_proof_first(self, client, world):
        gated = _issue(world, require_verification=True, send_via_bot="bot-1")
        response = client.get(_u("", gated))
        assert response.status_code == 302
        assert response["Location"] == reverse("supply_chain:update_link_public", args=[gated["token"]])
        data = client.get(_u("_data", gated))
        assert data.status_code == 403 and data.json()["error"] == "verify"

    def test_a_bad_token_is_one_answer_everywhere(self, client):
        for name in ("", "_data", "_sw", "_manifest"):
            url = reverse(f"supply_chain:update_link_field{name}", args=["not-a-token"])
            assert client.get(url).status_code == 404


class TestTheReferenceData:
    def test_it_lists_what_the_link_may_name_and_nothing_else(self, client, issued, world):
        body = client.get(_u("_data", issued)).json()
        assert body["org"] == "EHA Clinics" and body["recipient"] == "amina.bello"
        assert [p["id"] for p in body["points"]] == [world["warehouse"]["id"]]
        assert [i["id"] for i in body["items"]] == [world["item"]["id"]]
        assert body["items"][0]["pack_unit"] == "carton" and body["items"][0]["base_per_pack"] == 50
        assert body["csrf"]


class TestCounting:
    def test_a_count_is_recorded_as_a_physical_count_credited_to_the_person_it_was_sent_to(
        self, client, issued, world
    ):
        response = _post(client, issued, _entry(world))
        assert response.status_code == 200 and response.json()["ok"] is True
        count = StockCount.objects.get()
        assert (count.kind, count.batch, count.connect_username) == ("physical_count", "B-7", "amina.bello")
        submission = UpdateLinkSubmission.objects.get()
        assert submission.client_id == "e1234567890abcdef"
        assert submission.device_time.isoformat().startswith("2026-10-26T08:15")
        assert submission.verified is False

    def test_the_same_entry_sent_twice_is_one_entry(self, client, issued, world):
        first = _post(client, issued, _entry(world)).json()
        again = _post(client, issued, _entry(world)).json()
        assert first["duplicate"] is False and again["duplicate"] is True and again["id"] == first["id"]
        assert StockCount.objects.count() == 1 and UpdateLinkSubmission.objects.count() == 1

    @pytest.mark.parametrize(
        "over, fragment",
        [
            ({"client_id": "x"}, "valid id"),
            ({"supply_point": 999999}, "place"),
            ({"item": 999999}, "product"),
            ({"quantity": "-1"}, "zero or more"),
            ({"quantity": "lots"}, "number"),
            ({"quantity": "5E21"}, "number"),
            ({"quantity": "NaN"}, "number"),
            ({"quantity": "1000000001"}, "too large"),
            ({"unit_basis": "crate"}, "packs or"),
            ({"counted_on": "2999-01-01"}, "future"),
        ],
    )
    def test_a_bad_entry_is_refused_and_says_why(self, client, issued, world, over, fragment):
        response = _post(client, issued, _entry(world, **over))
        assert response.status_code == 400 and fragment in response.json()["error"]
        assert not StockCount.objects.exists()

    def test_a_place_outside_the_link_is_refused(self, client, issued, world):
        response = _post(client, issued, _entry(world, supply_point=world["llo"]["id"]))
        assert response.status_code == 400 and "not covered" in response.json()["error"]

    def test_the_csrf_check_is_on_and_the_data_response_hands_over_the_token(self, issued, world):
        strict = Client(enforce_csrf_checks=True)
        body = json.dumps(_entry(world))
        assert strict.post(_u("_count", issued), body, content_type="application/json").status_code == 403
        token = strict.get(_u("_data", issued)).json()["csrf"]
        ok = strict.post(_u("_count", issued), body, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        assert ok.status_code == 200

    def test_a_verified_browser_marks_its_entries_verified(self, world):
        with patch.object(delivery, "OCSDataAccess", FakeOCS):
            gated = _issue(world, require_verification=True, send_via_bot="bot-1")
            browser = Client()
            browser.post(reverse("supply_chain:update_link_public", args=[gated["token"]]), {"action": "send_code"})
            code = re.search(r"\b(\d{6})\b", [m for i in FakeOCS.instances for m in i.sent][-1]["message_text"]).group(
                1
            )
            browser.post(
                reverse("supply_chain:update_link_public", args=[gated["token"]]),
                {"action": "check_code", "code": code},
            )
        assert _post(browser, gated, _entry(world)).status_code == 200
        assert UpdateLinkSubmission.objects.get().verified is True


class TestPhotos:
    def test_a_photo_is_held_against_the_count_it_belongs_to(self, client, issued, world):
        _post(client, issued, _entry(world))
        response = client.post(
            _u("_photo", issued),
            {"client_id": "e1234567890abcdef", "photo": io.BytesIO(_jpeg())},
        )
        assert response.status_code == 200, response.content
        document = Document.objects.get()
        assert document.kind == "photo" and document.stock_count_id == StockCount.objects.get().pk
        assert document.content_type == "image/jpeg"

    def test_a_photo_sent_twice_is_held_once(self, client, issued, world):
        _post(client, issued, _entry(world))
        for _ in range(2):
            client.post(_u("_photo", issued), {"client_id": "e1234567890abcdef", "photo": io.BytesIO(_jpeg())})
        assert Document.objects.count() == 1

    def test_a_photo_before_its_count_is_told_to_wait(self, client, issued):
        response = client.post(_u("_photo", issued), {"client_id": "e1234567890abcdef", "photo": io.BytesIO(_jpeg())})
        assert response.status_code == 409 and response.json()["retry"] is True

    def test_what_is_not_an_image_is_refused_whatever_it_calls_itself(self, client, issued, world):
        _post(client, issued, _entry(world))
        fake = io.BytesIO(b"%PDF-1.4 not a picture")
        fake.name = "photo.jpg"
        response = client.post(_u("_photo", issued), {"client_id": "e1234567890abcdef", "photo": fake})
        assert response.status_code == 400 and not Document.objects.exists()

    def test_an_oversize_photo_is_refused(self, client, issued, world):
        _post(client, issued, _entry(world))
        big = io.BytesIO(_jpeg() + b"0" * (4 * 1024 * 1024))
        big.name = "big.jpg"
        assert client.post(_u("_photo", issued), {"client_id": "e1234567890abcdef", "photo": big}).status_code == 413

    def test_another_links_entry_is_not_reachable(self, client, issued, world):
        other = _issue(world)
        _post(client, other, _entry(world))
        response = client.post(_u("_photo", issued), {"client_id": "e1234567890abcdef", "photo": io.BytesIO(_jpeg())})
        assert response.status_code == 409 and not Document.objects.exists()


class TestOffline:
    def test_the_service_worker_keeps_the_page_the_data_and_the_script(self, client, issued):
        response = client.get(_u("_sw", issued))
        assert response["Content-Type"].startswith("application/javascript")
        body = response.content.decode()
        assert _u("", issued) in body and _u("_data", issued) in body and "field.js" in body
        assert re.search(r'var CACHE = "supply-field-1-\d+";', body)

    def test_the_manifest_starts_at_the_page(self, client, issued):
        manifest = client.get(_u("_manifest", issued)).json()
        assert manifest["start_url"] == _u("", issued) and manifest["display"] == "standalone"

    def test_the_decoder_and_script_exist_where_the_page_points(self, client, issued):
        from django.contrib.staticfiles import finders

        assert finders.find("supply_chain/field/field.js") and finders.find("supply_chain/field/jsQR.min.js")


class TestWeight:
    def test_the_first_open_stays_small_enough_for_a_poor_connection(self, client, issued):
        """Page + script + worker, raw. Gzip is about a third of this; the point is that it is tens of KB, not MB."""
        from django.contrib.staticfiles import finders

        page = len(client.get(_u("", issued)).content)
        worker = len(client.get(_u("_sw", issued)).content)
        script = len(open(finders.find("supply_chain/field/field.js"), "rb").read())
        assert page + worker + script < 40 * 1024

    def test_the_decoder_is_not_part_of_the_first_open(self, client, issued):
        html = client.get(_u("", issued)).content.decode()
        assert (
            "<script src=" in html
            and 'jsQR.min.js"></script>' not in html
            and 'src="/static/supply_chain/field/jsQR' not in html
        )
        # ...nor does the worker hold its install up for it
        worker = client.get(_u("_sw", issued)).content.decode()
        assert "jsQR" in worker.split("var LATER")[1] and "jsQR" not in worker.split("var LATER")[0]


class TestFindingIt:
    def test_the_form_page_offers_the_light_one(self, client, issued):
        page = client.get(reverse("supply_chain:update_link_public", args=[issued["token"]])).content.decode()
        assert "data-field-link" in page and _u("", issued) in page

    def test_the_issue_response_carries_its_address(self, issued):
        assert issued["field_url"] == f"{issued['url']}field/"
