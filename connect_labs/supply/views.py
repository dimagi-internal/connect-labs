"""Page views for the OES supply satellite app.

Auth is plain Django sessions over the shared ``users.User`` table — no OAuth,
no custom middleware. Suppliers self-register (open registration); staff
accounts are seeded only.
"""
from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .models import SupplierMember, SupplierOrg
from .roles import resolve_role

User = get_user_model()

# Supply self-registration writes to the SHARED ``users.User`` table (the same
# one the labs app authenticates against), and labs derives staff/admin access
# purely from the account's e-mail domain. So an open signup that accepted a
# ``@dimagi.com`` / ``@dimagi-ai.com`` address would mint a labs "Dimagi admin"
# from an anonymous, unverified web form — and would let an attacker pre-claim a
# real Dimagi user's username before they first OAuth in. Those identities must
# only ever come through the real OAuth flow, never open registration (this
# module's own docstring: "staff accounts are seeded only"). Reject the
# privileged domains here. NOTE: this closes the privilege-escalation and
# account-takeover paths but does not make supply-created accounts non-labs
# users — see the security-audit issue for the architectural fix.
_PRIVILEGED_SIGNUP_DOMAINS = ("@dimagi.com", "@dimagi-ai.com")


def ping(request):
    return JsonResponse({"status": "ok"})


class SignupForm(forms.Form):
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput, min_length=8)
    password2 = forms.CharField(widget=forms.PasswordInput)
    org_name = forms.CharField(max_length=255)
    org_country = forms.CharField(max_length=2)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if any(email.endswith(domain) for domain in _PRIVILEGED_SIGNUP_DOMAINS):
            # Dimagi accounts are provisioned via OAuth, never open signup.
            raise forms.ValidationError("Accounts on this domain cannot be created here. Please sign in instead.")
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username=email).exists():
            raise forms.ValidationError("An account with that email address already exists.")
        return email

    def clean_org_name(self):
        name = self.cleaned_data["org_name"].strip()
        if SupplierOrg.objects.filter(legal_name__iexact=name).exists():
            raise forms.ValidationError("An organisation with that name is already registered.")
        return name

    def clean_org_country(self):
        return self.cleaned_data["org_country"].strip().upper()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") and cleaned.get("password1") != cleaned.get("password2"):
            self.add_error("password2", "The two passwords do not match.")
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


def signup_view(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(
                    username=data["email"], email=data["email"], password=data["password1"]
                )
                org = SupplierOrg.objects.create(
                    legal_name=data["org_name"],
                    country=data["org_country"],
                    contact_email=data["email"],
                )
                SupplierMember.objects.create(user=user, org=org)
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("/supply/")
    else:
        form = SignupForm()
    return render(request, "supply/signup.html", {"form": form})


def login_view(request):
    error = None
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(request, username=form.cleaned_data["email"], password=form.cleaned_data["password"])
            if user is not None:
                auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                return redirect("/supply/")
            error = "Invalid email or password."
        else:
            error = "Invalid email or password."
    else:
        form = LoginForm()
    return render(request, "supply/login.html", {"form": form, "error": error})


def logout_view(request):
    auth_logout(request)
    return redirect("/supply/login/")


def app_view(request):
    """The SPA shell: renders the role-scoped bootstrap payload inline."""
    if resolve_role(request.user) is None:
        return redirect("/supply/login/")
    from django.conf import settings

    from .api.bootstrap import build_bootstrap

    return render(
        request,
        "supply/app.html",
        {
            "bootstrap": build_bootstrap(request),
            "mapbox_token": getattr(settings, "MAPBOX_TOKEN", None) or "",
        },
    )
