"""The Investigations Tracker over HTTP: one public page, and the signed-in pages that change it.

* ``InvestigationsView`` (``/labs/investigations/``) is PUBLIC: no sign-in, GET only, nothing but the published
  content, the models' headline figures and the visit totals the timed job kept. It is a 404 until someone
  publishes, and again after an unpublish.
* ``InvestigationsEditView`` and ``InvestigationsPreviewView`` (under ``/labs/cea/investigations/``) are for Dimagi
  staff: save a draft, see it exactly as the public page would draw it, publish it, restore an earlier one, or take
  the page down.

All three draw ``investigations.page.build``, so a preview is the page.
"""

from __future__ import annotations

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness.investigations import content, figures, page, store
from connect_labs.effectiveness.models import InvestigationsVersion
from connect_labs.labs.chrome import NOT_USED, page_chrome
from connect_labs.utils.dimagi_user import is_dimagi_staff

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


def _payload(data: dict) -> dict:
    return page.build(data, figures.all_models(), store.delivery(), explorer_url=reverse("cea:models"))


class InvestigationsView(View):
    """The public page. Anyone with the address reads it; it writes nothing and names no partner."""

    http_method_names = ["get", "head"]

    def get(self, request):
        data = store.live_content()
        if data is None:
            raise Http404("Not published")
        return render(request, "effectiveness/investigations.html", {"data": _payload(data), "preview": False})


class StaffOnly(LoginRequiredMixin):
    """Signed in to Labs with a Dimagi account: the people who may change what the public page says."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_dimagi_staff(request.user):
            raise PermissionDenied("Dimagi staff may change the Investigations Tracker.")
        return super().dispatch(request, *args, **kwargs)


@CONTEXT_NOT_USED
class InvestigationsPreviewView(StaffOnly, View):
    """The newest saved draft, drawn as the public page would draw it."""

    def get(self, request):
        return render(request, "effectiveness/investigations.html", {"data": _payload(store.draft()), "preview": True})


@CONTEXT_NOT_USED
class InvestigationsEditView(StaffOnly, View):
    def get(self, request):
        return self._render(request, store.draft())

    def post(self, request):
        action = request.POST.get("action", "")
        try:
            if action == "save":
                try:
                    raw = json.loads(request.POST.get("data") or "")
                except ValueError:
                    raise content.ContentError(["The form did not send its content. Reload and try again."]) from None
                try:
                    store.save(request.user, raw, note=request.POST.get("note", ""))
                except content.ContentError as exc:
                    # Keep what they typed on screen, with what is wrong with it.
                    shown = raw if isinstance(raw, dict) else store.draft()
                    return self._render(request, shown, errors=exc.messages, status=400)
            elif action == "publish":
                store.publish(request.user, self._version(request))
            elif action == "restore":
                store.restore(request.user, self._version(request))
            elif action == "unpublish":
                store.unpublish(request.user)
            else:
                raise content.ContentError(["That is not something this page can do."])
        except content.ContentError as exc:
            return self._render(request, store.draft(), errors=exc.messages, status=400)
        except InvestigationsVersion.DoesNotExist:
            return self._render(request, store.draft(), errors=["That version no longer exists."], status=400)
        return redirect("cea:investigations_edit")

    @staticmethod
    def _version(request) -> int:
        try:
            return int(request.POST.get("version") or 0)
        except ValueError:
            raise InvestigationsVersion.DoesNotExist from None

    def _render(self, request, data, errors=None, status=200):
        state = store.state()
        newest = store.latest()
        live_id = state.live_id if state else None
        delivery = store.delivery() or {}
        editor = {
            "content": data,
            "choices": {
                "groups": list(content.GROUP_KEYS),
                "stages": list(content.STAGES),
                "models": list(content.MODEL_STATES),
                "model_links": list(content.MODEL_LINKS),
                "model_keys": sorted(figures.all_models()),
            },
            "figures": figures.all_models(),
            "delivery": delivery.get("services") or {},
        }
        context = {
            "editor": editor,
            "errors": errors or [],
            "newest": newest,
            "live_id": live_id,
            "published_at": state.published_at if state else None,
            "unpublished_changes": bool(newest and newest.pk != live_id),
            "history": store.history(),
            "delivery_as_of": delivery.get("as_of"),
            "public_url": reverse("investigations"),
        }
        return render(request, "effectiveness/investigations_edit.html", context, status=status)
