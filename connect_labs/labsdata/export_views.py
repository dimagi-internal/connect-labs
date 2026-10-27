"""The dictionary and exports of a data space: its page and its JSON routes.

Every route opens the space as the person first (``_opened`` -> ``spaces.open``), so the
space's own rules -- every source held now, then its audience -- decide everything here
as they do on the Data page (``tests/test_spaces_gate.py`` reads this module too).
Changing the dictionary and signing off are a manager's, checked in ``dictionary`` and
``exports``; no MCP tool or page action reaches any of these writes.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, JsonResponse
from django.views.generic import TemplateView

from . import dictionary, exports, spaces
from .views import _audit_space, _caller, _Invalid, _JsonView


def _opened(request, space_id) -> spaces.OpenSpace:
    return spaces.open(_caller(request), space_id)


class _SpaceJson(_JsonView):
    def _answer(self, fn):
        try:
            return super()._answer(fn)
        except dictionary.DictionaryError as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=400)
        except exports.ExportError as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=e.status)


class ExportsPageView(LoginRequiredMixin, TemplateView):
    """The space's dictionary and exports page."""

    template_name = "labsdata/exports.html"

    def get(self, request, *args, **kwargs):
        try:
            self.opened = _opened(request, kwargs["space_id"])
        except spaces.SpaceNotFound as e:
            raise Http404(e.message) from e
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["space"] = self.opened.space
        ctx["is_manager"] = self.opened.is_manager
        return ctx


class DictionaryView(_SpaceJson):
    """GET: the dictionary (made, with the visits columns, on first open)."""

    def read(self, request):
        return dictionary.describe(_opened(request, self.kwargs["space_id"]))


class DictionaryScanView(_SpaceJson):
    """POST: scan the space's cached visits for its fields (structure only). Anyone who can
    open the space: it changes no setting a person made."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = _opened(request, self.kwargs["space_id"])
        summary = dictionary.scan(opened, user=request.user)
        _audit_space(Action.READ, opened, "labsdata_dictionary_scan", opened.id, request.user, summary)
        return {"scan": summary, "dictionary": dictionary.describe(opened)}


class DictionaryEntryView(_SpaceJson):
    """POST ``{key, setting?, params?, meaning?, level?, quasi?}``: change one entry (a
    manager). Setting it marks it reviewed."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = _opened(request, self.kwargs["space_id"])
        key = str(body.get("key") or "")
        change = {k: body[k] for k in ("setting", "params", "meaning", "level", "quasi") if k in body}
        if not change:
            raise _Invalid("nothing_to_change", "Say what to change.")
        entry = dictionary.update_entry(opened, key, change, user=request.user)
        _audit_space(Action.UPDATE, opened, "labsdata_dictionary", opened.id, request.user, {"entry": key, **change})
        return {"entry": entry}


class DictionaryAcceptView(_SpaceJson):
    """POST ``{keys: [...]}``: review these entries at their suggested settings (a manager)."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = _opened(request, self.kwargs["space_id"])
        keys = [str(k) for k in body.get("keys") or []]
        n = dictionary.accept_suggestions(opened, keys, user=request.user)
        _audit_space(Action.UPDATE, opened, "labsdata_dictionary", opened.id, request.user, {"accepted": n})
        return {"accepted": n, "dictionary": dictionary.describe(opened)}


class DictionaryMergeView(_SpaceJson):
    """POST ``{into, other}``: one question under two names; ``other`` joins ``into`` (a manager)."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = _opened(request, self.kwargs["space_id"])
        entry = dictionary.merge(opened, str(body.get("into") or ""), str(body.get("other") or ""), user=request.user)
        _audit_space(
            Action.UPDATE,
            opened,
            "labsdata_dictionary",
            opened.id,
            request.user,
            {"merged": body.get("other"), "into": body.get("into")},
        )
        return {"entry": entry}


class DictionaryFlagResolveView(_SpaceJson):
    def handle(self, request, body):
        opened = _opened(request, self.kwargs["space_id"])
        dictionary.resolve_flag(opened, self.kwargs["flag_id"], user=request.user)
        return {"resolved": self.kwargs["flag_id"]}


class DictionaryRegistryView(_SpaceJson):
    """POST ``{registry_id}``: the semantic registry whose properties inherit (a manager)."""

    def handle(self, request, body):
        opened = _opened(request, self.kwargs["space_id"])
        dictionary.set_registry(opened, body.get("registry_id"), user=request.user)
        return {"registry_id": body.get("registry_id")}


class DictionaryLineageView(_SpaceJson):
    """GET: what each property of the dictionary's registry inherits from these fields."""

    def read(self, request):
        from . import lineage

        opened = _opened(request, self.kwargs["space_id"])
        return lineage.for_space(_caller(request), opened)


class DictionaryCasesView(_SpaceJson):
    """POST: add a case field for every aggregate and property of the space's workflow's
    registry, each starting at what it inherits from its form fields (unreviewed, so
    dropped). Anyone who can open the space: it changes no setting a person made."""

    def handle(self, request, body):
        from . import cases

        opened = _opened(request, self.kwargs["space_id"])
        out = cases.refresh(_caller(request), opened, user=request.user)
        return {**out, "dictionary": dictionary.describe(opened) if out.get("available") else None}


class ExportsView(_SpaceJson):
    """GET: the space's exports. POST ``{date_from?, date_to?}``: ask for one."""

    def read(self, request):
        opened = _opened(request, self.kwargs["space_id"])
        return {"exports": [exports.describe(e, opened) for e in exports.for_space(opened)[:50]]}

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = _opened(request, self.kwargs["space_id"])
        export = exports.request(
            opened,
            user=request.user,
            caller=_caller(request),
            date_from=body.get("date_from"),
            date_to=body.get("date_to"),
        )
        _audit_space(
            Action.CREATE,
            opened,
            "labsdata_export",
            export.pk,
            request.user,
            {"date_from": body.get("date_from"), "date_to": body.get("date_to")},
        )
        return {"export": exports.describe(export, opened)}


class ExportSignOffView(_SpaceJson):
    """POST ``{note, acknowledge_warnings}``: a manager who did not ask for it signs it off."""

    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = _opened(request, self.kwargs["space_id"])
        export = exports.sign_off(
            opened,
            self.kwargs["pk"],
            user=request.user,
            note=str(body.get("note") or ""),
            acknowledge_warnings=body.get("acknowledge_warnings") is True,
        )
        _audit_space(Action.UPDATE, opened, "labsdata_export", export.pk, request.user, {"signed_off": True})
        return {"export": exports.describe(export, opened)}


class ExportRejectView(_SpaceJson):
    def handle(self, request, body):
        from connect_labs.audit_trail.models import Action

        opened = _opened(request, self.kwargs["space_id"])
        export = exports.reject(opened, self.kwargs["pk"], user=request.user, note=str(body.get("note") or ""))
        _audit_space(Action.UPDATE, opened, "labsdata_export", export.pk, request.user, {"rejected": True})
        return {"export": exports.describe(export, opened)}


class ExportDownloadView(LoginRequiredMixin, TemplateView):
    """GET: the signed-off file (the requester or a manager, before it expires). Audited
    per opportunity as an export, with the row count."""

    def get(self, request, *args, **kwargs):
        from connect_labs.audit_trail.models import Action

        try:
            opened = _opened(request, kwargs["space_id"])
            export, handle = exports.open_file(opened, kwargs["pk"])
        except spaces.SpaceNotFound as e:
            raise Http404(e.message) from e
        except exports.ExportError as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=e.status)
        from connect_labs.audit_trail.service import record

        for o in export.opportunities:
            record(
                Action.EXPORT,
                resource_type="labsdata_export",
                resource_id=export.pk,
                record_count=export.rows,
                opportunity_id=int(o["id"]),
                metadata={"space_id": opened.id},
                user=request.user,
            )
        return FileResponse(handle, as_attachment=True, filename=export.file_name.rsplit("/", 1)[-1])
