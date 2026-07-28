"""Persona switching for local walkthrough capture. DEBUG only.

A DDD render drives four narratives through four different personas, and every
one of them has to reach the app as a logged-in session. Supply authenticates
with an ordinary Django login form, so the alternative is teaching the capture
harness to fill and submit a form before each run — which makes the login page
part of every recording and puts the demo password in four recipe files.

This mirrors ``connect_labs/labs/views_test_auth.py`` exactly, including its
guard: outside ``DEBUG`` it is a hard 403 before anything else happens. Supply
is deployed on a public host with open registration, so that guard is the whole
reason this is acceptable — ``config/settings/labs_aws.py`` runs with
``DEBUG=False``, and `tests/test_dev_auth.py` asserts the refusal rather than
trusting the setting.

It logs in ONLY personas the demo seed created, by exact email, and never
creates a user. There is no path here to an account the seed did not already
make.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.http import JsonResponse
from django.shortcuts import redirect

from .demo.data import PARTNER_ORG, STAFF, SUPPLIER_LOGIN

User = get_user_model()


def _personas():
    """persona key -> the seeded email it logs in as.

    Derived from the demo data rather than restated, so a persona that stops
    being seeded stops being reachable here in the same commit.
    """
    people = {
        "ada": STAFF[0][0],  # procurement admin
        "tomas": STAFF[1][0],  # reviewer
        "hauwa": STAFF[2][0],  # Nigeria observer
        "dale": STAFF[3][0],  # funder
        "amina": SUPPLIER_LOGIN[0],  # Savanna Nutrients
        "zara": PARTNER_ORG[4],  # Komadugu Health Initiative
    }
    return people


def dev_login(request):
    """Log in as a seeded demo persona. Local capture only."""
    if not settings.DEBUG:
        return JsonResponse({"error": "Only available in DEBUG mode"}, status=403)

    personas = _personas()
    persona = (request.GET.get("persona") or "").strip().lower()
    email = personas.get(persona)
    if not email:
        return JsonResponse(
            {"error": f"unknown persona {persona!r}", "available": sorted(personas)},
            status=400,
        )

    user = User.objects.filter(username=email).first()
    if user is None:
        return JsonResponse(
            {"error": f"{email} does not exist. Run: python manage.py seed_supply_demo"},
            status=404,
        )

    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(request.GET.get("next") or "/supply/")
