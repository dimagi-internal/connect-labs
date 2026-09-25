"""The supplier marketplace in a browser, at /supply/market/.

Browsing is public: an open public round is meant to be found. Everything
that writes needs a labs sign-in (through Connect, like everyone else), an
organisation the person acts for, and -- to bid -- that organisation's
supplier profile. The pages are their own light shell rather than the labs
one: a supplier has no programs, workflows or tabs to navigate, and the labs
furniture would be buttons that lead nowhere for them.

Every page reads through `market.service`, which is what decides what a
visitor may see; these views only choose which organisation is acting and
render what they are given.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from connect_labs.audit_trail.context import get_audit_context
from connect_labs.marketplace import membership
from connect_labs.marketplace.models import OrgMembership
from connect_labs.supply_chain import records
from connect_labs.supply_chain.market import cards, service
from connect_labs.supply_chain.market.forms import BidForm, InviteForm, OfferingForm, ProfileForm, RegisterForm
from connect_labs.supply_chain.models import SupplierOffering, SupplierProfile, fill_profile


def _signed_in(request) -> bool:
    return bool(getattr(request, "user", None) and request.user.is_authenticated)


def _sign_in(request):
    return redirect(f"{reverse('labs:login')}?{urlencode({'next': request.get_full_path()})}")


def _acting_org(request, orgs):
    """The organisation this request acts for: the one chosen, or the only one."""
    chosen = request.POST.get("org") or request.GET.get("org")
    if chosen and chosen.isdigit():
        for org in orgs:
            if org.pk == int(chosen):
                return org
    return orgs[0] if len(orgs) == 1 else None


def _suppliers(orgs):
    """The organisations among these that have a supplier profile."""
    with_profile = set(SupplierProfile.objects.filter(org__in=orgs).values_list("org_id", flat=True))
    return [org for org in orgs if org.pk in with_profile]


def _context(request, **extra):
    orgs = membership.orgs_for(request)
    return {"signed_in": _signed_in(request), "orgs": orgs, "supplier_orgs": _suppliers(orgs), **extra}


def _render(request, template, **extra):
    return render(request, f"supply_chain/market/{template}", _context(request, **extra))


# ---- public -----------------------------------------------------------------


def _delivered_to(round_) -> str:
    point = round_.delivery_point or {}
    return f"{point.get('country', '')} {point.get('country_name', '')}"


class MarketHomeView(View):
    def get(self, request):
        orgs = membership.orgs_for(request)
        everything = service.listed_rounds(orgs)
        listed = everything
        category = request.GET.get("category", "")
        country = (request.GET.get("country") or "").strip()
        if category:
            listed = [
                r for r in listed if any(line.commodity and line.commodity.category == category for line in r.lines)
            ]
        if country:
            listed = [r for r in listed if country.lower() in _delivered_to(r.round).lower()]

        return _render(
            request,
            "home.html",
            rounds=listed,
            sections=cards.sections(listed),
            # Across every open round, not the filtered few: the figures say what
            # the market is, and a filter narrows the list below them.
            headline=cards.headline(everything, service.registered_supplier_count()),
            categories=records.COMMODITY_CATEGORIES,
            category=category,
            country=country,
        )


class MarketRoundView(View):
    def get(self, request, round_id):
        orgs = membership.orgs_for(request)
        try:
            listed = service.visible_round(round_id, orgs)
        except service.NotAvailable:
            raise Http404("no such round")
        return _render(request, "round.html", listed=listed, card=cards.card_for(listed))


# ---- bidding ------------------------------------------------------------------


class _SupplierView(View):
    """Signed in, acting for an organisation with a supplier profile."""

    def dispatch(self, request, *args, **kwargs):
        if not _signed_in(request):
            return _sign_in(request)
        self.orgs = membership.orgs_for(request)
        if not _suppliers(self.orgs):
            messages.info(request, "Register your organisation's supplier profile to bid.")
            return redirect(reverse("supply_chain:market_register"))
        return super().dispatch(request, *args, **kwargs)

    def acting(self, request):
        org = _acting_org(request, _suppliers(self.orgs))
        return org


class BidView(_SupplierView):
    def _line(self, listed, slug):
        for line in listed.lines:
            if line.commodity_slug == slug:
                return line
        raise Http404("this round is not asking for that product")

    def _page(self, request, listed, line, form, org, quote=None, status=200):
        response = _render(
            request,
            "bid.html",
            listed=listed,
            line=line,
            form=form,
            acting=org,
            revising=quote,
            offerings=SupplierOffering.objects.filter(profile__org=org) if org else [],
        )
        response.status_code = status
        return response

    def get(self, request, round_id, slug):
        try:
            listed = service.visible_round(round_id, self.orgs)
        except service.NotAvailable:
            raise Http404("no such round")
        line = self._line(listed, slug)
        org = self.acting(request)
        requirements = line.commodity.spec_requirements if line.commodity else []
        initial = {"as_quoted_currency": "USD", "quantity_basis_unit": line.quantity_unit}
        offering_id = request.GET.get("offering", "")
        if org and offering_id.isdigit():
            offering = SupplierOffering.objects.filter(pk=int(offering_id), profile__org=org).first()
            if offering is not None:
                initial.update(
                    base_per_pack_stated=offering.base_per_pack, lead_time_days=offering.typical_lead_time_days
                )
        return self._page(request, listed, line, BidForm(initial=initial, requirements=requirements), org)

    def post(self, request, round_id, slug):
        try:
            listed = service.visible_round(round_id, self.orgs)
        except service.NotAvailable:
            raise Http404("no such round")
        line = self._line(listed, slug)
        org = self.acting(request)
        form = BidForm(request.POST, requirements=line.commodity.spec_requirements if line.commodity else [])
        if org is None:
            form.add_error(None, "Choose which organisation this bid is from.")
        if not form.is_valid():
            return self._page(request, listed, line, form, org, status=400)
        try:
            with transaction.atomic():
                service.bid(round_id, slug, org=org, orgs=self.orgs, user=request.user, data=form.payload())
        except (service.NotAvailable, service.NeedsProfile, ValueError) as refused:
            form.add_error(None, str(refused))
            return self._page(request, listed, line, form, org, status=400)
        messages.success(request, f"Your bid for {line.name} is in. The buyer sees it as entered by {org.name}.")
        return redirect(reverse("supply_chain:market_bids"))


class ReviseView(BidView):
    def _quote(self, request, quote_id):
        try:
            return service.own_quote(quote_id, self.orgs)
        except service.NotAvailable:
            raise Http404("no such bid")

    def get(self, request, quote_id):
        quote = self._quote(request, quote_id)
        try:
            listed = service.visible_round(quote.round_id, self.orgs)
        except service.NotAvailable:
            raise Http404("this round has closed")
        line = self._line(listed, quote.commodity.slug)
        form = BidForm(initial=BidForm.initial_from(quote), requirements=quote.commodity.spec_requirements)
        return self._page(request, listed, line, form, quote.supplier.org, quote=quote)

    def post(self, request, quote_id):
        quote = self._quote(request, quote_id)
        try:
            listed = service.visible_round(quote.round_id, self.orgs)
        except service.NotAvailable:
            raise Http404("this round has closed")
        line = self._line(listed, quote.commodity.slug)
        org = quote.supplier.org
        form = BidForm(request.POST, requirements=quote.commodity.spec_requirements)
        if not form.is_valid():
            return self._page(request, listed, line, form, org, quote=quote, status=400)
        try:
            with transaction.atomic():
                service.revise(quote.pk, org=org, orgs=self.orgs, user=request.user, data=form.payload(replacing=True))
        except (service.NotAvailable, service.NeedsProfile, ValueError) as refused:
            form.add_error(None, str(refused))
            return self._page(request, listed, line, form, org, quote=quote, status=400)
        messages.success(request, "Your bid is revised. The buyer keeps the earlier version on record.")
        return redirect(reverse("supply_chain:market_bids"))


class WithdrawView(_SupplierView):
    def post(self, request, quote_id):
        try:
            quote = service.own_quote(quote_id, self.orgs)
        except service.NotAvailable:
            raise Http404("no such bid")
        try:
            service.withdraw(quote.pk, org=quote.supplier.org, orgs=self.orgs, user=request.user)
        except (service.NotAvailable, service.NeedsProfile, ValueError) as refused:
            messages.error(request, str(refused))
            return redirect(reverse("supply_chain:market_bids"))
        messages.success(request, "Your bid is withdrawn.")
        return redirect(reverse("supply_chain:market_bids"))


class MyBidsView(_SupplierView):
    def get(self, request):
        return _render(request, "bids.html", bids=service.own_quotes(self.orgs))


# ---- the organisation ------------------------------------------------------------


class RegisterView(View):
    def dispatch(self, request, *args, **kwargs):
        if not _signed_in(request):
            return _sign_in(request)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        orgs = membership.orgs_for(request)
        missing = [org for org in orgs if org not in _suppliers(orgs)]
        return _render(request, "register.html", form=RegisterForm(), profileless=missing)

    def post(self, request):
        from connect_labs.marketplace.identity import OrgExists

        orgs = membership.orgs_for(request)
        profile_for = request.POST.get("profile_for", "")
        if profile_for.isdigit():
            # An organisation the person already acts for (a Connect org) that
            # has no supplier profile yet: add one, and nothing else.
            org = next((o for o in orgs if o.pk == int(profile_for)), None)
            if org is None or not membership.manages(request, org):
                raise Http404("not an organisation you manage")
            SupplierProfile.objects.get_or_create(org=org)
            return redirect(reverse("supply_chain:market_organisation") + f"?org={org.pk}")

        form = RegisterForm(request.POST)
        if not form.is_valid():
            response = _render(request, "register.html", form=form, profileless=[])
            response.status_code = 400
            return response
        try:
            with transaction.atomic():
                org = self._register(request, form)
        except OrgExists:
            form.add_error(
                "name", "That organisation is already on file. Ask someone there, or your buyer, for an invitation."
            )
            response = _render(request, "register.html", form=form, profileless=[])
            response.status_code = 400
            return response
        messages.success(request, f"{org.name} is registered. Add what you sell, then bid on any open round.")
        return redirect(reverse("supply_chain:market_organisation") + f"?org={org.pk}")

    def _register(self, request, form):
        from connect_labs.marketplace.identity import mint_new_org

        data = form.cleaned_data
        org = mint_new_org(data["name"], country=data["country"])
        fill_profile(
            org,
            {
                "type": data["type"],
                "city": data.get("city", ""),
                "website": data.get("website", ""),
                "description": data.get("description", ""),
                "contacts": [form.contact()],
            },
        )
        membership.register(request.user, org)
        return org


class OrganisationView(View):
    def dispatch(self, request, *args, **kwargs):
        if not _signed_in(request):
            return _sign_in(request)
        self.orgs = membership.orgs_for(request)
        self.org = _acting_org(request, self.orgs)
        if self.org is None:
            if not self.orgs:
                return redirect(reverse("supply_chain:market_register"))
            return _render(request, "choose_org.html")
        # Read, never created here: opening the page must not turn an
        # organisation into a bidding supplier. That is a choice, made by the
        # button the page offers when there is no profile yet.
        self.profile = SupplierProfile.objects.filter(org=self.org).first()
        self.admin = membership.manages(request, self.org)
        return super().dispatch(request, *args, **kwargs)

    def _page(self, request, *, profile_form=None, offering_form=None, invite_form=None, issued=None, status=200):
        response = _render(
            request,
            "organisation.html",
            org=self.org,
            profile=self.profile,
            admin=self.admin,
            profile_form=profile_form or ProfileForm(instance=self.profile),
            offering_form=offering_form or OfferingForm(),
            invite_form=invite_form or InviteForm(),
            offerings=self.profile.offerings.all(),
            members=OrgMembership.objects.filter(org=self.org).select_related("user"),
            issued=issued,
        )
        response.status_code = status
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request):
        if self.profile is None:
            return _render(request, "no_profile.html", org=self.org, admin=self.admin)
        return self._page(request)

    def post(self, request):
        action = request.POST.get("action")
        back = redirect(reverse("supply_chain:market_organisation") + f"?org={self.org.pk}")
        if not self.admin:
            raise Http404("only an admin of this organisation can do that")
        if action == "create_profile":
            SupplierProfile.objects.get_or_create(org=self.org)
            return back
        if self.profile is None:
            raise Http404("this organisation has no supplier profile")
        if action == "offering_add":
            form = OfferingForm(request.POST)
            if not form.is_valid():
                return self._page(request, offering_form=form, status=400)
            offering = form.save(commit=False)
            offering.profile = self.profile
            offering.save()
            messages.success(request, f"Added {offering.product_name}.")
            return back
        if action == "offering_delete":
            offering_id = request.POST.get("offering", "")
            if not offering_id.isdigit():
                raise Http404("no such product")
            SupplierOffering.objects.filter(pk=int(offering_id), profile=self.profile).delete()
            return back
        if action == "profile":
            form = ProfileForm(request.POST, instance=self.profile)
            if not form.is_valid():
                return self._page(request, profile_form=form, status=400)
            form.save()
            country = form.cleaned_data.get("country")
            if country and not self.org.country:
                self.org.country = country
                self.org.save(update_fields=["country", "updated_at"])
            messages.success(request, "Profile saved.")
            return back
        if action == "invite":
            form = InviteForm(request.POST)
            if not form.is_valid():
                return self._page(request, invite_form=form, status=400)
            invite, raw = membership.issue_invite(
                self.org, email=form.cleaned_data.get("email", ""), issued_by=request.user
            )
            issued = request.build_absolute_uri(reverse("supply_chain:market_invite", args=[raw]))
            return self._page(request, issued=issued)
        raise Http404("unknown action")


INVITE_SESSION_KEY = "market_invite_token"


class OpenInviteView(View):
    """The link someone was sent. Swaps the token into the session and moves on.

    The token must not travel further than this one request: not into the
    sign-in page's `next=` (where it would sit in a query string the audit log
    keeps), not into a Location header, not into the next page's URL. So the
    first thing this does is put it in the session and redirect to an address
    that carries nothing. The audit record of this request names the path with
    the token redacted, as the update-link page does.
    """

    def get(self, request, token):
        audit = get_audit_context()
        if audit is not None:
            audit.path = reverse("supply_chain:market_invite", kwargs={"token": "redacted"})
        if membership.find_invite(token) is None:
            return _invalid_invite(request)
        request.session[INVITE_SESSION_KEY] = token
        return _no_store(redirect(reverse("supply_chain:market_invite_accept")))


def _no_store(response):
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"
    return response


def _invalid_invite(request):
    response = _render(request, "invite_invalid.html")
    response.status_code = 404
    return _no_store(response)


class AcceptInviteView(View):
    """Accepting an invitation, at an address with no token in it."""

    def _invite(self, request):
        return membership.find_invite(request.session.get(INVITE_SESSION_KEY))

    def get(self, request):
        invite = self._invite(request)
        if invite is None:
            return _invalid_invite(request)
        return _no_store(_render(request, "invite.html", invite=invite))

    def post(self, request):
        if not _signed_in(request):
            return _sign_in(request)
        invite = self._invite(request)
        if invite is None:
            return _invalid_invite(request)
        try:
            membership.accept_invite(invite, request.user)
        except ValueError:
            return _invalid_invite(request)
        request.session.pop(INVITE_SESSION_KEY, None)
        messages.success(request, f"You now act for {invite.org.name} on the marketplace.")
        return redirect(reverse("supply_chain:market_organisation") + f"?org={invite.org.pk}")
