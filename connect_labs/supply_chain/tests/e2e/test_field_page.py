"""The field page in a real browser: saved on the phone first, sent when there is signal.

Needs Playwright and a Chromium (``playwright install chromium``); skipped where there is none, and
kept out of CI with the other e2e directories (pyproject ``--ignore``). Offline is a real browser
offline: the service worker, IndexedDB and the ``online`` event are the ones a phone has.
"""

import io
import os
import random

import pytest

pytest.importorskip("playwright.sync_api")
qrcode = pytest.importorskip("qrcode")
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from connect_labs.labs.access.scopes import SYSTEM  # noqa: E402
from connect_labs.supply_chain.data_access import SupplyDataAccess  # noqa: E402
from connect_labs.supply_chain.models import Document, Item, StockCount  # noqa: E402
from connect_labs.supply_chain.operations import call_operation  # noqa: E402
from connect_labs.supply_chain.tests.test_update_links import _world  # noqa: E402
from connect_labs.supply_chain.update_links.models import UpdateLink  # noqa: E402
from connect_labs.users.tests.factories import UserFactory  # noqa: E402

# Playwright's sync API runs an event loop in this thread; Django's ORM would refuse to be called from it.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

PROGRAM = 10741
GTIN = "09501101530003"  # a valid GTIN-14


@pytest.fixture
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 -- no browser installed here
            pytest.skip(f"no Chromium for Playwright: {exc}".splitlines()[0])
        yield b
        b.close()


@pytest.fixture
def issued(transactional_db):
    da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
    world = _world(da)
    Item.objects.filter(pk=world["item"]["id"]).update(gtin_pack=GTIN)
    issuer_da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, user=UserFactory(), caller=SYSTEM)
    link = call_operation(
        "update_link_issue",
        issuer_da,
        {
            "data": {
                "org_id": world["eha"]["id"],
                "contract_ids": [world["contract"]["id"]],
                "supply_point_ids": [world["warehouse"]["id"]],
                "connect_username": "amina.bello",
            }
        },
    )
    return {**link, "world": world}


@pytest.fixture
def phone(browser, live_server, issued):
    """A browser context that has opened the field page once, online, and has its service worker."""
    context = browser.new_context(viewport={"width": 360, "height": 640})
    page = context.new_page()
    page.goto(f"{live_server.url}/supply/u/{issued['token']}/field/")
    page.wait_for_function("document.getElementById('point').options.length > 0")
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    yield context, page, issued
    context.close()


def _wait_sent(page, count=1):
    """Until the phone lists `count` entries as sent (and so has nothing left waiting)."""
    page.wait_for_function(
        "n => document.querySelectorAll('#done li').length >= n && document.getElementById('queue-box').hidden",
        arg=count,
        timeout=15000,
    )


def _enter(page, qty="12", batch="B-7"):
    page.fill("#qty", qty)
    page.fill("#batch", batch)
    page.click("button[type=submit]")


class TestOffline:
    def test_it_opens_with_no_signal_and_holds_an_entry_until_there_is_some(self, phone):
        context, page, issued = phone
        page.reload()  # let the worker take control, then go dark
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        context.set_offline(True)
        page.reload()
        page.wait_for_function("document.getElementById('point').options.length > 0")
        assert page.inner_text("#net") == "offline"

        _enter(page)
        page.wait_for_selector("#queue li")
        assert "12" in page.inner_text("#queue")
        assert not StockCount.objects.exists()  # nothing has reached the programme yet

        context.set_offline(False)  # the phone finds signal
        _wait_sent(page)
        count = StockCount.objects.get()
        assert (count.connect_username, count.batch) == ("amina.bello", "B-7")
        assert "12" in page.inner_text("#done")

    def test_a_lost_reply_does_not_double_the_entry(self, phone):
        context, page, issued = phone

        def drop_reply(route):
            route.fetch()  # the server records it...
            route.abort()  # ...but the phone never hears

        page.route("**/field/count/", drop_reply)
        _enter(page, qty="5")
        page.wait_for_function("document.getElementById('pending').hidden === false")
        page.wait_for_timeout(800)
        assert StockCount.objects.count() == 1
        page.unroute("**/field/count/")
        page.click("#send-now")  # the phone tries again
        _wait_sent(page)
        assert StockCount.objects.count() == 1

    def test_a_revoked_link_keeps_the_entry_and_says_so(self, phone):
        context, page, issued = phone
        context.set_offline(True)
        _enter(page)
        page.wait_for_selector("#queue li")
        UpdateLink.objects.filter(pk=issued["id"]).update(revoked_at="2026-10-01T00:00:00Z")
        context.set_offline(False)
        page.wait_for_function("document.getElementById('banner').textContent.indexOf('stopped working') >= 0")
        assert page.query_selector_all("#queue li")  # still on the phone, not thrown away
        assert not StockCount.objects.exists()


class TestPhoto:
    def test_a_big_photo_is_shrunk_on_the_phone_and_travels_with_the_count(self, phone, tmp_path):
        context, page, issued = phone
        rng = random.Random(1)
        noisy = Image.frombytes("RGB", (3000, 2000), bytes(rng.randrange(256) for _ in range(3000 * 2000 * 3)))
        path = tmp_path / "big.jpg"
        noisy.save(path, "JPEG", quality=95)
        assert path.stat().st_size > 2_000_000

        page.set_input_files("#photo", str(path))
        page.wait_for_function("!document.getElementById('photo-note').hidden")
        _enter(page)
        _wait_sent(page)

        document = Document.objects.get()
        assert document.stock_count_id == StockCount.objects.get().pk and document.kind == "photo"
        assert document.size_bytes < 1_500_000


@pytest.fixture
def camera_phone(live_server, issued, tmp_path):
    """A phone whose back camera is looking at a code: Chromium's fake camera, fed a QR as motion JPEG."""
    frame = io.BytesIO()
    qrcode.make(f"https://id.gs1.org/01/{GTIN}/10/LIVE5").convert("RGB").resize((640, 480)).save(frame, "JPEG")
    feed = tmp_path / "camera.mjpeg"
    feed.write_bytes(frame.getvalue() * 30)
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(
                args=[
                    "--use-fake-device-for-media-stream",
                    "--use-fake-ui-for-media-stream",
                    f"--use-file-for-fake-video-capture={feed}",
                ]
            )
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {exc}".splitlines()[0])
        context = b.new_context(viewport={"width": 360, "height": 640}, permissions=["camera"])
        page = context.new_page()
        page.goto(f"{live_server.url}/supply/u/{issued['token']}/field/")
        page.wait_for_function("document.getElementById('point').options.length > 0")
        yield page, issued
        b.close()


class TestScan:
    def test_the_live_camera_finds_a_code_and_fills_in_the_product(self, camera_phone):
        page, issued = camera_phone
        page.click("#scan")
        page.wait_for_function("document.getElementById('scanner').style.display === 'none'", timeout=20000)
        assert page.input_value("#item") == str(issued["world"]["item"]["id"])
        assert page.input_value("#batch") == "LIVE5"

    def test_a_photographed_code_fills_in_the_product_and_batch(self, phone, tmp_path):
        context, page, issued = phone
        image = qrcode.make(f"https://id.gs1.org/01/{GTIN}/10/LOT9?17=270630")
        path = tmp_path / "code.png"
        image.save(path)

        page.click("#scan")
        page.set_input_files("#shot", str(path))
        page.wait_for_function("document.getElementById('scanner').style.display === 'none'", timeout=15000)
        assert page.input_value("#item") == str(issued["world"]["item"]["id"])
        assert page.input_value("#batch") == "LOT9"

    def test_a_code_for_a_product_not_on_the_link_says_so(self, phone):
        context, page, issued = phone
        page.click("#scan")
        page.fill("#typed", "https://id.gs1.org/01/09506000134352")
        page.click("#typed-go")
        assert "No product on this link" in page.inner_text("#scan-note")


class TestCodeParsing:
    @pytest.mark.parametrize(
        "raw, gtin, batch",
        [
            ("https://id.gs1.org/01/09501101530003/10/LOT9?17=270630", "09501101530003", "LOT9"),
            ("(01)09501101530003(10)AB12(17)270630", "09501101530003", "AB12"),
            ("09501101530003", "09501101530003", ""),
            ("hello", "", ""),
        ],
    )
    def test_the_codes_a_pack_can_carry(self, phone, raw, gtin, batch):
        context, page, issued = phone
        parsed = page.evaluate("raw => window.SupplyField.parseCode(raw)", raw)
        assert (parsed["gtin"], parsed["batch"]) == (gtin, batch)
