"""The field page: a lean, installable, offline-capable way to record stock from a phone.

Behind the same link and the same checks as the form page (``LinkRequestMixin``): the token in the
URL, and -- when the link asks for it -- a browser that has proved it can read the recipient's
PersonalID chat. It does one job, a stock count, with a photo and a barcode/QR scan to fill the
product in, and is built for old, low-end Android phones on poor connections:

  - the page and its script are small and make no request to any other site;
  - a service worker keeps the page, its script and the link's reference data, so it opens with no
    signal once it has been opened with some;
  - entries are saved on the phone first (IndexedDB) and sent when there is signal, each with an id
    the phone made up, so sending one twice records it once;
  - a photo goes separately from the numbers, so a slow upload never holds the count back.

The endpoints speak JSON. They are protected by the link token and, on a verified link, the device
cookie, and use Django's CSRF check: the reference-data response carries a token, which the script echoes.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

from connect_labs.supply_chain.update_links import service
from connect_labs.supply_chain.update_links.models import UpdateLinkSubmission
from connect_labs.supply_chain.update_links.views import LinkRequestMixin, _count, _private

# Higher than the form page's: a worker catching up after days without signal sends a backlog at once.
FIELD_WRITE_LIMIT = 300
# A quantity as a person types it: digits, optionally a decimal part. Decimal() also accepts
# "5E21", "Infinity" and "NaN", which a number input lets through when letters land in it.
QUANTITY = re.compile(r"^\d+(\.\d+)?$")
MAX_QUANTITY = Decimal("1000000000")
FIELD_WRITE_WINDOW = 60 * 60
MAX_PHOTO_BYTES = 4 * 1024 * 1024
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# Cached files are versioned by this: change it and every phone fetches the new script.
FIELD_VERSION = "1"

_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def sniff_image(content: bytes) -> str | None:
    """The image type of ``content`` by its first bytes, or None. The upload's own claim is not trusted."""
    for signature, content_type in _IMAGE_SIGNATURES:
        if content.startswith(signature):
            return content_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _urls(token) -> dict:
    kwargs = {"token": token}
    return {
        "page": reverse("supply_chain:update_link_field", kwargs=kwargs),
        "data": reverse("supply_chain:update_link_field_data", kwargs=kwargs),
        "count": reverse("supply_chain:update_link_field_count", kwargs=kwargs),
        "photo": reverse("supply_chain:update_link_field_photo", kwargs=kwargs),
        "sw": reverse("supply_chain:update_link_field_sw", kwargs=kwargs),
        "manifest": reverse("supply_chain:update_link_field_manifest", kwargs=kwargs),
        "verify": reverse("supply_chain:update_link_public", kwargs=kwargs),
    }


def _static_files() -> dict:
    return {
        "script": static("supply_chain/field/field.js"),
        "decoder": static("supply_chain/field/jsQR.min.js"),
    }


def _json(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


class FieldBase(LinkRequestMixin):
    """Common to every field endpoint: a verified browser if the link asks for one, JSON refusals."""

    def refuse_if_gated(self, token):
        if self.gated:
            return _json({"error": "verify", "verify_url": _urls(token)["verify"]}, status=403)
        return None


@method_decorator(never_cache, name="dispatch")
class FieldPageView(FieldBase):
    def get(self, request, token):
        if self.gated:
            # The proof lives on the form page; send them there and they come back.
            return redirect(_urls(token)["verify"])
        files = _static_files()
        context = {
            "link": self.link,
            "org": self.link.org,
            "urls": _urls(token),
            "static_files": files,
            "verified": self.device is not None,
            # Read by the script. `db` names this link's store on the phone, so two links opened in one
            # browser never share a queue.
            "config": {
                "urls": _urls(token),
                "decoder": files["decoder"],
                "db": f"supply-field-{self.link.pk}",
                "version": FIELD_VERSION,
                "recipient": self.link.connect_username,
            },
        }
        return _private(render(request, "supply_chain/field.html", context))


@method_decorator(never_cache, name="dispatch")
class FieldDataView(FieldBase):
    """What the page needs to run with no connection: the points and products this link may name."""

    def get(self, request, token):
        if (refused := self.refuse_if_gated(token)) is not None:
            return refused
        scope = service.scope_for(self.link)
        points = [{"id": p.pk, "name": p.name} for p in scope.supply_points.order_by("name")]
        items = [
            {
                "id": i.pk,
                "name": i.name,
                "pack_unit": i.pack_unit,
                "base_unit": i.base_unit,
                "base_per_pack": i.base_per_pack,
                "gtins": [g for g in (i.gtin_base, i.gtin_pack, i.gtin_case) if g],
            }
            for i in scope.items.order_by("name")
        ]
        return _private(
            _json(
                {
                    "org": self.link.org.name,
                    "recipient": self.link.connect_username,
                    "verified": self.device is not None,
                    # Settings keep the CSRF token in the session, out of reach of a script, so the
                    # script is handed one with the data it syncs against -- online, which is the
                    # only time it needs it.
                    "csrf": get_token(request),
                    "expires_at": self.link.expires_at.isoformat(),
                    "points": points,
                    "items": items,
                }
            )
        )


def _entry_from(body: dict, scope):
    """Validate a posted entry. Returns ``(data, client_id, device_time)`` or raises ValueError."""
    client_id = str(body.get("client_id") or "")
    if not CLIENT_ID.match(client_id):
        raise ValueError("The entry has no valid id.")
    point = scope.supply_points.filter(pk=_int(body.get("supply_point"))).first()
    if point is None:
        raise ValueError("That place is not covered by this link.")
    item = scope.items.filter(pk=_int(body.get("item"))).first()
    if item is None:
        raise ValueError("That product is not covered by this link.")
    raw = str(body.get("quantity")).strip()
    if raw.startswith("-"):
        raise ValueError("Enter a quantity of zero or more.")
    if not QUANTITY.match(raw):
        raise ValueError("Enter the quantity as a number, like 12 or 2.5.")
    try:
        quantity = Decimal(raw)
    except InvalidOperation:
        raise ValueError("Enter the quantity as a number, like 12 or 2.5.") from None
    if quantity > MAX_QUANTITY:
        raise ValueError("That quantity is too large: the most one count can hold is 1,000,000,000.")
    basis = body.get("unit_basis") or "pack"
    if basis not in ("pack", "base"):
        raise ValueError("Count in packs or in single units.")
    counted_on = parse_date(str(body.get("counted_on") or "")) or timezone.localdate()
    if counted_on > date.today() + timedelta(days=1):
        raise ValueError("The count date is in the future.")
    batch = str(body.get("batch") or "").strip()[:64]
    device_time = parse_datetime(str(body.get("device_time") or ""))
    data = {
        "supply_point": point,
        "item": item,
        "quantity": str(quantity),
        "unit_basis": basis,
        "counted_on": counted_on,
        "batch": batch,
    }
    return data, client_id, device_time


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


@method_decorator(never_cache, name="dispatch")
class FieldCountView(FieldBase):
    def post(self, request, token):
        if (refused := self.refuse_if_gated(token)) is not None:
            return refused
        try:
            body = json.loads(request.body or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Unreadable entry.")
        except ValueError:
            return _json({"error": "Unreadable entry."}, status=400)
        if _count(f"supply:update-link:field-writes:{self.link.pk}", FIELD_WRITE_WINDOW) > FIELD_WRITE_LIMIT:
            return _json({"error": "Too many entries this hour. Try again later."}, status=429)
        scope = service.scope_for(self.link)
        try:
            data, client_id, device_time = _entry_from(body, scope)
            result = service.submit(
                self.link,
                "record_stock_count",
                data,
                verified=self.device is not None,
                client_id=client_id,
                device_time=device_time,
            )
        except IntegrityError:
            # Two sends of the same entry raced; the other won.
            prior = UpdateLinkSubmission.objects.filter(link=self.link, client_id=str(body.get("client_id"))).first()
            return _json({"ok": True, "duplicate": True, "id": prior.result_id if prior else None})
        except service.OutOfScope as exc:
            return _json({"error": str(exc)}, status=400)
        except (ValueError, TypeError) as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"ok": True, "duplicate": bool(result.get("duplicate")), "id": result.get("id")})


@method_decorator(never_cache, name="dispatch")
class FieldPhotoView(FieldBase):
    def post(self, request, token):
        if (refused := self.refuse_if_gated(token)) is not None:
            return refused
        client_id = str(request.POST.get("client_id") or "")
        upload = request.FILES.get("photo")
        if not CLIENT_ID.match(client_id) or upload is None:
            return _json({"error": "No photo."}, status=400)
        if upload.size > MAX_PHOTO_BYTES:
            return _json({"error": "That photo is too large."}, status=413)
        content = upload.read()
        content_type = sniff_image(content)
        if content_type is None:
            return _json({"error": "That is not a photo."}, status=400)
        if _count(f"supply:update-link:field-writes:{self.link.pk}", FIELD_WRITE_WINDOW) > FIELD_WRITE_LIMIT:
            return _json({"error": "Too many entries this hour. Try again later."}, status=429)
        try:
            result = service.attach_photo(
                self.link, client_id, filename=upload.name or "photo", content_type=content_type, content=content
            )
        except service.OutOfScope as exc:
            return _json({"error": str(exc), "retry": True}, status=409)
        except (ValueError, TypeError) as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"ok": True, "duplicate": result["duplicate"]})


@method_decorator(never_cache, name="dispatch")
class FieldServiceWorkerView(LinkRequestMixin):
    """The script that keeps the page working offline. Served from inside its own scope."""

    def get(self, request, token):
        urls = _urls(token)
        files = _static_files()
        core = [urls["page"], urls["data"], files["script"]]
        # The decoder is big and only needed once somebody scans with a browser that cannot decode itself, so
        # it is fetched after the worker is running rather than holding the install up on a slow connection.
        later = [files["decoder"]]
        body = render(
            request,
            "supply_chain/field_sw.js",
            {
                "cache_name": f"supply-field-{FIELD_VERSION}-{self.link.pk}",
                "shell": json.dumps(core),
                "later": json.dumps(later),
            },
            content_type="application/javascript",
        )
        body["Cache-Control"] = "no-cache"
        return body


@method_decorator(never_cache, name="dispatch")
class FieldManifestView(LinkRequestMixin):
    def get(self, request, token):
        urls = _urls(token)
        return JsonResponse(
            {
                "name": f"{self.link.org.name} stock",
                "short_name": "Stock",
                "start_url": urls["page"],
                "scope": urls["page"],
                "display": "standalone",
                "background_color": "#f5f5f4",
                "theme_color": "#3843d0",
            },
            content_type="application/manifest+json",
        )
