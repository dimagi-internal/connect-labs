"""Django's FormView, with `.save()` replaced by `call_operation`.

That substitution is the whole of this module. Everything else a write screen
needs -- binding POST data, validation, per-field errors, re-rendering with
what was typed -- is `FormView` doing its job.

**Patching, for tests.** `call_operation` is imported here, so a test drives
these screens by patching `connect_labs.supply_chain.form_views.call_operation`.
procurement/views.py documents the same hazard at length: a name imported one
module up cannot be patched from the module that uses it, and a test patching
the wrong one runs real operations against a real database while appearing to
have mocked them.
"""

import jsonschema
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.generic import View
from django.views.generic.edit import FormView

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.identity import IdentityUnresolved
from connect_labs.supply_chain.navigation import supply_tabs
from connect_labs.supply_chain.operations import call_operation, get_operation


def _nests_data(operation) -> bool:
    """Whether this operation takes its fields under a `data` object.

    Most writes do; the destructive ones (`quote_void`, `outreach_delete`)
    take flat arguments. Read off the schema so a screen never restates it.
    """
    data = (operation.input_schema.get("properties") or {}).get("data")
    return isinstance(data, dict) and bool(data.get("properties"))


class SupplyWriteMixin:
    def access(self):
        return _access(self.request)

    def op(self, name, **payload):
        return call_operation(name, self.access(), payload)


@method_decorator(login_required, name="dispatch")
class OperationFormView(SupplyWriteMixin, FormView):
    """One operation, as a page of fields."""

    template_name = "supply_chain/operation_form.html"

    operation = ""
    title = ""
    intro = ""
    submit_label = "Save"
    footnote = ""
    danger = False

    # ---- what a screen overrides ----------------------------------------

    def fixed(self, **kwargs) -> dict:
        """What the URL already knows, and the form must not let be edited."""
        return {}

    def breadcrumb(self, **kwargs) -> list:
        return []

    def cancel_href(self, **kwargs) -> str:
        return ""

    def redirect_to(self, result) -> str:
        raise NotImplementedError

    # ---- the machinery --------------------------------------------------

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["access"] = self.access()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["supply_tabs"] = supply_tabs(self.request)
        context["has_program_context"] = has_program_context(self.request)
        context.update(
            title=self.title,
            intro=self.intro,
            submit_label=self.submit_label,
            footnote=self.footnote,
            danger=self.danger,
            breadcrumb=self.breadcrumb(**self.kwargs),
            cancel_href=self.cancel_href(**self.kwargs),
        )
        return context

    def get(self, request, *args, **kwargs):
        if not has_program_context(request):
            # Build no form at all. Its querysets are scoped to the caller's
            # programme, and `scope_key` refuses rather than inventing one --
            # so constructing the form here is a 500 on a page whose whole job
            # is to say "choose a programme".
            return self.render_to_response(self.get_context_data(form=None))
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not has_program_context(request):
            return self.render_to_response(self.get_context_data(form=None))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        operation = get_operation(self.operation)
        fields = form.payload()
        fixed = dict(self.fixed(**self.kwargs))
        # `fixed` may carry both top-level arguments (the round id in the URL)
        # and data the screen supplies rather than asks for (a round's lines,
        # assembled from their formset). Kept separate rather than merged in a
        # one-liner, because which half a key lands in decides whether the
        # operation sees it at all.
        fixed_data = fixed.pop("data", {})
        if _nests_data(operation):
            payload = {**fixed, "data": {**fields, **fixed_data}}
        else:
            payload = {**fields, **fixed, **fixed_data}

        try:
            result = self.op(self.operation, **payload)
        except IdentityUnresolved as exc:
            # A ValueError, so the clause below would catch it -- but its
            # message names `org_upsert`, which is the right instruction for an
            # API or MCP caller and useless to somebody looking at a browser.
            # Rewritten here, in the surface that knows a screen exists, rather
            # than in the domain that serves all three.
            form.add_error(
                None,
                f"{exc} There is a screen for that: Suppliers → Organisations.",
            )
            return self.form_invalid(form)
        except jsonschema.ValidationError as exc:
            # The schema is stricter than the model in places, and where it
            # refuses, the message names the field. Attaching it to that field
            # beats a banner over a form of twelve.
            named = [p for p in exc.absolute_path if isinstance(p, str) and p != "data"]
            form.add_error(named[-1] if named and named[-1] in form.fields else None, exc.message)
            return self.form_invalid(form)
        except (ValueError, TypeError) as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        return self.succeeded(result)

    def succeeded(self, result):
        """The response once the operation has run. A redirect, almost always.

        Overridable for the one kind of result that must not survive a
        redirect: a secret shown once (an update link's token), which in a
        Location header would sit in browser history and access logs.
        """
        return redirect(self.redirect_to(result))


@method_decorator(login_required, name="dispatch")
class OperationActionView(SupplyWriteMixin, View):
    """A button, not a page. POST only, because it changes something.

    Opening and closing a round take nothing but the id already in the URL, so
    a form would be a page asking no questions. The operation still refuses
    what it should -- `round_open` will not open a round with no delivery
    point -- and that refusal belongs back on the page the button was on,
    because it is an instruction and the fix is one screen away.
    """

    operation = ""
    success_message = ""

    def fixed(self, **kwargs) -> dict:
        return {}

    def redirect_to(self, **kwargs) -> str:
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        if not has_program_context(request):
            raise Http404("no programme selected")
        try:
            self.op(self.operation, **self.fixed(**kwargs))
        except (jsonschema.ValidationError, ValueError, TypeError) as exc:
            messages.error(request, getattr(exc, "message", str(exc)))
        else:
            if self.success_message:
                messages.success(request, self.success_message)
        return redirect(self.redirect_to(**kwargs))
