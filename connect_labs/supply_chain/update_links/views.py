"""Update links in a browser: the programme's screens, and the page behind a link.

**The programme's screens** (/supply/links/) are ordinary operation screens:
list, issue, revoke. Issuing renders the link on the response instead of
redirecting, because the raw token must not travel in a Location header -- it
would sit in browser history and in access logs, and it is shown exactly once.

**The page behind a link** (/supply/u/<token>/) is the one page in the domain
that authenticates nobody. It is NOT an operation screen and does not appear in
the every-write-has-a-screen test: it drives no operation of its own, only the
ordinary ones, through `service.submit`. What stands in for a login:

  - the token, found by keyed hash and compared in constant time;
  - one response for a token that is unknown, expired or revoked, so the page
    cannot be used to learn which guesses were ever real;
  - a per-address budget for bad tokens and a per-link budget for writes;
  - Django's CSRF protection on every form;
  - no analytics script, no referrer to other sites, no caching and no
    indexing, because the token is in the URL.
"""

from urllib.parse import urlencode

import jsonschema
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache

from connect_labs.audit_trail.context import get_audit_context
from connect_labs.supply_chain.api_views import has_program_context
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView
from connect_labs.supply_chain.update_links import service, tokens
from connect_labs.supply_chain.update_links.forms import PUBLIC_FORMS, UpdateLinkIssueForm
from connect_labs.supply_chain.update_links.models import UpdateLinkSubmission
from connect_labs.supply_chain.views import OperationBase

# ---- the programme's screens ------------------------------------------------


def _with_cover(contracts, scope):
    """Stamp each short order with the order bought to cover it, as the programme's page reads it.

    "Part received" on the partner's link beside "short -- covered by SCHI-LP-0921" on
    the programme's order page was one order read two ways. The covering order is
    named only when it is itself on this link; otherwise it is "another order", so a
    link never learns the reference of an order outside its scope.
    """
    in_scope = {c.pk for c in contracts}
    for contract in contracts:
        covers = list(contract.shortfall_covered_by.exclude(status="cancelled").order_by("pk"))
        contract.covered_by_refs = [
            (c.reference or f"order {c.pk}") if c.pk in in_scope else "another order" for c in covers
        ]
    return contracts


class UpdateLinkListView(OperationBase):
    template_name = "supply_chain/update_links.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if context["has_program_context"]:
            links = self.op("update_link_list")
            for link in links:
                link["expires"] = parse_datetime(link["expires_at"])
                link["revoked"] = parse_datetime(link["revoked_at"]) if link["revoked_at"] else None
                link["last_used"] = parse_datetime(link["last_used_at"]) if link["last_used_at"] else None
                for contract in link["contracts"]:
                    contract["status_label"] = contract["status"].replace("_", " ")
            active = [link for link in links if link["state"] == "active"]
            inactive = [link for link in links if link["state"] != "active"]
            context["link_groups"] = [
                group
                for group in (
                    {"label": "Working", "active": True, "links": active},
                    {"label": "Revoked or expired", "active": False, "links": inactive},
                )
                if group["links"]
            ]
        return context


class UpdateLinkIssueView(OperationFormView):
    operation = "update_link_issue"
    form_class = UpdateLinkIssueForm
    title = "Issue an update link"
    intro = (
        "A private link one organisation can use without a labs login — a supplier to confirm orders "
        "and payments, record dispatches, receipts, stock counts and releases; an approver to give its "
        "own answer on an approval asked of it. It covers either the orders, supply points and approvals "
        "you tick here and nothing else, or everything involving the organisation — including orders "
        "created after today, such as a partner's cover order. Whatever they record is marked as theirs."
    )
    submit_label = "Issue link"
    footnote = (
        "The link is shown once, on the next screen. Only a keyed fingerprint of it is kept, so it "
        "cannot be shown again — if it is lost, revoke it and issue another."
    )

    def breadcrumb(self, **kwargs):
        return [{"label": "Update links", "href": reverse("supply_chain:update_links")}, {"label": self.title}]

    def get_initial(self):
        """Arriving from an approval ("Send them a link to answer"): that organisation, that question.

        Only a starting point -- the form still checks the approval was asked
        of the organisation, so a hand-edited query string cannot cross them.
        """
        initial = super().get_initial()
        org, approval = self.request.GET.get("org", ""), self.request.GET.get("approval", "")
        if org.isdigit():
            initial["org"] = int(org)
        if approval.isdigit():
            initial["approvals"] = [int(approval)]
        return initial

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:update_links")

    def succeeded(self, result):
        response = render(
            self.request,
            "supply_chain/update_link_issued.html",
            {**self.get_context_data(form=None), "link": result},
        )
        response["Cache-Control"] = "no-store"
        return response


class UpdateLinkRevokeView(OperationActionView):
    operation = "update_link_revoke"
    success_message = "Link revoked. It stopped working immediately."

    def fixed(self, **kwargs):
        return {"link_id": int(kwargs["link_id"])}

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:update_links")


# ---- the page behind a link -------------------------------------------------

# A person mistyping a link a few times is fine. Thirty wrong tokens from one
# address in ten minutes is somebody guessing, and the answer to a guess
# costs them nothing but costs us a hash and a query.
BAD_TOKEN_LIMIT = 30
BAD_TOKEN_WINDOW = 10 * 60
# Generous for a person, low enough that a leaked link cannot be used to flood
# the ledger before someone notices and revokes it.
WRITE_LIMIT = 60
WRITE_WINDOW = 60 * 60


# Proxies that append to X-Forwarded-For in front of labs: the load balancer.
# It APPENDS the address it saw, so its entry is the rightmost one; anything to
# the left is whatever the caller sent. Keying the throttle on the leftmost
# entry would let a guesser rotate it per request (the same rule as
# `mcp/oauth.py`'s `_client_ip`).
_TRUSTED_PROXY_HOPS = 1


def _client_address(request) -> str:
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if len(forwarded) >= _TRUSTED_PROXY_HOPS:
        return forwarded[-_TRUSTED_PROXY_HOPS]
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _count(key, window) -> int:
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


def _private(response):
    """Headers for a page whose URL is a credential."""
    response["X-Robots-Tag"] = "noindex, nofollow"
    # Same-origin, not no-referrer: the token must never reach another site,
    # but under no-referrer a browser serialises a form POST's Origin as
    # "null", Django's CSRF check refuses it, and every submission on the
    # page is a 403. The test client sends no Origin, which is how that
    # shipped; test_a_browser_can_submit_the_form_it_was_given replays it.
    response["Referrer-Policy"] = "same-origin"
    response["Cache-Control"] = "no-store"
    return response


def _not_valid(request, status=404):
    # Deliberately the same bytes for every failure: no token, no CSRF token,
    # no date. Whatever differs between two failures tells a guesser something.
    return _private(render(request, "supply_chain/update_link_invalid.html", status=status))


def _done_sentence(form_class, detail) -> str:
    """The banner after a write, as a sentence about the record it made.

    "Recorded: dispatch HHS-WB-3310, 450 packets, batch DP-2608-A." rather
    than "Recorded: record a dispatch — HHS-WB-3310: ..." -- the action's own
    label echoed back. Built from the read-back of the row, so it repeats what
    the programme holds. Starts with "Recorded:" whatever the action.
    """
    if form_class is None:
        return ""
    noun = str(form_class.done_noun or form_class.title).lower()
    if form_class.action == "record_shipment" and detail.endswith(" — dispatched"):
        # The noun already says it left; "— dispatched" after it says it twice.
        detail = detail[: -len(" — dispatched")]
    head, colon, rest = detail.partition(": ")
    if colon and head and " " not in head:
        # A reference, then what it carried: "HHS-WB-3310: 450 packets".
        text = f"{noun} {head}, {rest}"
    elif colon and head.lower() == noun:
        # A dispatch with no reference reads back as "Dispatch: 450 packets".
        text = f"{noun}, {rest}"
    elif detail:
        text = f"{noun} — {detail}"
    else:
        text = noun
    return f"Recorded: {text.rstrip('.')}."


def _with_holdings(program_id, scope):
    """The link's supply points, each carrying what it holds of the link's orders' items.

    A distributor that records a release should see it land: "Dawaki project
    site — holds 60 units", read from the same ledger the programme's stock
    page reads. Only the items on the link's own orders, so a link shows
    nothing about stock it was not given. Nothing held, or a holding the
    ledger cannot state in one unit, shows no figure rather than a guess.
    """
    from connect_labs.supply_chain.models import Item, scope_key
    from connect_labs.supply_chain.stock.services import ledger
    from connect_labs.supply_chain.values import Quantity, quantity_phrase

    item_ids = {pk for pk in scope.contracts.values_list("item_id", flat=True) if pk}
    # Scoped by the link's own programme as well as by the ids. The ids come
    # from this link's contracts and so are already confined to it; naming the
    # scope again means a reader does not have to establish that to know this
    # read is safe.
    items = list(
        Item.objects.filter(pk__in=item_ids, scope_key=scope_key(program_id=scope.link.program_id)).order_by("name")
    )
    points = list(scope.supply_points)
    for point in points:
        held = []
        for item in items:
            quantity = ledger.balance(program_id, point, item=item)
            if isinstance(quantity, Quantity) and quantity.amount and quantity.unit:
                held.append(quantity_phrase(quantity.amount, quantity.unit))
        point.holding = ", ".join(held)
    return points


@method_decorator(never_cache, name="dispatch")
class UpdateLinkPublicView(View):
    template_name = "supply_chain/update_link_public.html"

    def dispatch(self, request, *args, **kwargs):
        # The audit trail is append-only and archived under object lock, so a
        # token written into it could never be taken back out. Record the
        # route, not the credential.
        audit = get_audit_context()
        if audit is not None:
            audit.path = reverse("supply_chain:update_link_public", kwargs={"token": "redacted"})
        address = _client_address(request)
        if (cache.get(f"supply:update-link:bad:{address}") or 0) >= BAD_TOKEN_LIMIT:
            return _not_valid(request, status=429)
        link = tokens.find_usable_link(kwargs.get("token"))
        if link is None:
            _count(f"supply:update-link:bad:{address}", BAD_TOKEN_WINDOW)
            return _not_valid(request)
        self.link = link
        return super().dispatch(request, *args, **kwargs)

    def _forms(self, scope, bound=None):
        forms = []
        for action, form_class in PUBLIC_FORMS.items():
            form = bound if bound is not None and bound.action == action else form_class(scope=scope)
            forms.append(form)
        return forms

    def _render(self, scope, bound=None, status=200):
        forms = self._forms(scope, bound)
        # An approver link covers approvals and nothing else, so the supplier
        # actions are neither offered nor listed as unavailable -- they are not
        # this organisation's to take -- and the other way tender.
        approver_link = scope.approvals is not None and scope.approvals.exists()
        supplier_link = scope.contracts.exists() or scope.supply_points.exists()
        # A partner's link covers an order to record what reached it; the
        # supplier's own actions (confirm, dispatch, move a dispatch along) are
        # not its to take, so they are not offered or listed as unavailable.
        supplies = scope.supplied.exists()
        # The same the other way tender, on a link that follows its
        # organisation: a supplier that receives nothing is not offered
        # "Record goods received". On a listed link the issuer chose the
        # orders, and every one of them can be received against.
        receives = scope.received.exists() or not scope.follows_org
        priced = scope.contracts.filter(consideration="priced").exists()
        forms = [
            form
            for form in forms
            if (form.for_approvers and approver_link)
            or (
                not form.for_approvers
                and supplier_link
                and (supplies or not form.for_suppliers)
                and (receives or not form.for_receivers)
                and (priced or not form.for_priced)
            )
            or form is bound
        ]
        done_action = self.request.GET.get("done", "")
        if done_action not in PUBLIC_FORMS:
            done_action = ""
        recent = [
            {
                "action": submission.action,
                "title": service.record_noun(submission.action),
                "detail": service.describe(submission),
                **service.when(submission),
            }
            for submission in UpdateLinkSubmission.objects.filter(link=self.link)[:10]
        ]
        # What this link has already put on the record. An action can be
        # unavailable for two opposite reasons -- the link's scope never
        # covered it, or its work is DONE -- and `is_available()` returns
        # False for both. Printed together, the page told a distributor that
        # "confirm an order" was unavailable on the same screen that showed
        # his own confirmation of it (the test-kit render, scene 9). The
        # approver half of this was fixed by excluding `for_approvers`; this
        # is the same fault for suppliers, so it is fixed by cause rather
        # than by role.
        recorded_here = set(UpdateLinkSubmission.objects.filter(link=self.link).values_list("action", flat=True))
        context = {
            "link": self.link,
            "org": self.link.org,
            "contracts": _with_cover(
                list(scope.contracts.prefetch_related("shipments__lines", "shipments__receipts")), scope
            ),
            "supply_points": _with_holdings(self.link.program_id, scope),
            "approvals": list(scope.approvals) if scope.approvals is not None else [],
            "forms": [form for form in forms if form.is_available() or form is bound],
            # An approver link with every question answered has nothing left
            # to take: say that, rather than listing "record your answer" as
            # unavailable, which reads as an instruction.
            "unavailable": [
                form
                for form in forms
                if not form.is_available()
                and form is not bound
                and not form.for_approvers
                and form.action not in recorded_here
            ],
            "all_answered": any(form.for_approvers for form in forms)
            and not any(form.for_approvers and form.is_available() for form in forms),
            "bound_action": bound.action if bound is not None else "",
            "done": PUBLIC_FORMS.get(done_action),
            "recent": recent,
            # What the submission just made put on the record, read back from
            # the row it produced -- the confirmation a supplier (or anyone
            # watching) can check against what they meant to send.
            "done_sentence": _done_sentence(
                PUBLIC_FORMS.get(done_action),
                recent[0]["detail"] if recent and done_action and recent[0]["action"] == done_action else "",
            ),
            "expires_on": timezone.localtime(self.link.expires_at).date(),
        }
        return _private(render(self.request, self.template_name, context, status=status))

    def get(self, request, token):
        return self._render(service.scope_for(self.link))

    def post(self, request, token):
        form_class = PUBLIC_FORMS.get(request.POST.get("action", ""))
        if form_class is None:
            raise Http404("no such action")
        scope = service.scope_for(self.link)
        form = form_class(request.POST, request.FILES, scope=scope)
        if not form.is_valid():
            return self._render(scope, bound=form)

        if _count(f"supply:update-link:writes:{self.link.pk}", WRITE_WINDOW) > WRITE_LIMIT:
            form.add_error(None, "This link has recorded a lot in the last hour. Wait a while and try again.")
            return self._render(scope, bound=form, status=429)

        try:
            service.submit(self.link, form.action, form.payload())
        except service.OutOfScope as exc:
            form.add_error(None, str(exc))
            return self._render(scope, bound=form)
        except jsonschema.ValidationError as exc:
            form.add_error(None, exc.message)
            return self._render(scope, bound=form)
        except (ValueError, TypeError) as exc:
            form.add_error(None, str(exc))
            return self._render(scope, bound=form)
        # Rebuilt from the route rather than echoed from the request path, and
        # `done` is one of PUBLIC_FORMS' own keys, never the posted string.
        target = reverse("supply_chain:update_link_public", kwargs={"token": token})
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            raise Http404("no such link")
        return redirect(f"{target}?{urlencode({'done': form.action})}")
