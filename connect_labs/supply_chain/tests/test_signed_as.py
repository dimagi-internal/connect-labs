"""The name Supply's drafted emails are signed with: one a person sets once, never a bare login.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented. Builds on rehearsal_world.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain import identity
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import op as rop

da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program

pytestmark = pytest.mark.django_db


def _user(django_user_model, username, name="", email=None):
    return django_user_model.objects.create(name=name, username=username, email=email or f"{username}@dimagi.com")


# ---- who is signed -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, username, email, source, signed",
    [
        # Only a bare login is known: not signed with it, and the page asks.
        ("sfeintuch", "sfeintuch", "sfeintuch@dimagi.com", "handle", ""),
        ("", "sfeintuch", "sfeintuch@dimagi.com", "handle", ""),
        # A login with a break in it reads as a name.
        ("sophie.feintuch", "sophie.feintuch", "sophie.feintuch@dimagi.com", "profile", "Sophie Feintuch"),
        ("", "hauwa_bello", "hauwa@example.org", "profile", "Hauwa Bello"),
        # A real name on the profile.
        ("Hauwa Bello", "hb", "hb@example.org", "profile", "Hauwa Bello"),
        # A service account is never signed.
        ("ace", "ace", "ace@dimagi-ai.com", "none", ""),
    ],
)
def test_who_is_signed(django_user_model, name, username, email, source, signed):
    held = identity.signature(_user(django_user_model, username, name, email))
    assert (held.source, held.name) == (source, signed)
    assert held.needs_name is (source == "handle")


def test_a_name_they_set_wins_over_any_login(django_user_model):
    user = _user(django_user_model, "sfeintuch", "sfeintuch")
    assert identity.set_signed_as(user, "  Sophie   Feintuch ") == "Sophie Feintuch"
    held = identity.signature(user)
    assert (held.source, held.name, held.needs_name) == ("chosen", "Sophie Feintuch", False)


def test_a_blank_name_puts_them_back_to_their_own(django_user_model):
    user = _user(django_user_model, "sfeintuch", "sfeintuch")
    identity.set_signed_as(user, "Sophie Feintuch")
    identity.set_signed_as(user, "")
    assert identity.signature(user).source == "handle"


def test_the_name_is_each_persons_own(django_user_model):
    one, two = _user(django_user_model, "one", "one"), _user(django_user_model, "two", "two")
    identity.set_signed_as(one, "Uno Person")
    assert identity.signature(two).source == "handle"


def test_a_service_account_cannot_set_one_and_a_stored_one_is_ignored(django_user_model):
    bot = _user(django_user_model, "ace", "ace", "ace@dimagi-ai.com")
    with pytest.raises(ValueError, match="service account"):
        identity.set_signed_as(bot, "Somebody Real")
    from connect_labs.scope_config.models import ScopeConfig

    ScopeConfig.objects.create(
        scope_type="user", scope_key="ace", namespace="profile", data={"signed_as": "Somebody Real"}
    )
    assert identity.signature(bot).name == "" and identity.signature(bot).service_account


@pytest.mark.parametrize("bad", ["x" * 121, "Nul\x00l", "Esc\x1bape"])
def test_a_name_is_short_plain_text(django_user_model, bad):
    with pytest.raises(ValueError):
        identity.set_signed_as(_user(django_user_model, "sfeintuch", "sfeintuch"), bad)


def test_line_breaks_in_a_name_never_reach_the_signature(django_user_model):
    user = _user(django_user_model, "sfeintuch", "sfeintuch")
    assert identity.set_signed_as(user, "Sophie\nFeintuch\r\n") == "Sophie Feintuch"


# ---- the drafts and the pages ------------------------------------------------------------------


def _award(da, world):
    quote = rop(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": world["kanem"]["id"],
            "as_quoted_amount": "50",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "base_per_pack_stated": 150,
        },
    )
    return rop(da, "award_create", tender_id=world["tender"]["id"], quote_id=quote["id"])


@pytest.fixture
def sfeintuch(client_in_program, da, django_user_model):
    """The signed-in person, known to labs only by a login with no break in it."""
    user = django_user_model.objects.get(pk=client_in_program.session["_auth_user_id"])
    user.username, user.name, user.email = "sfeintuch", "sfeintuch", "sfeintuch@dimagi.com"
    user.save()
    da.user = user
    return user


def _sign_off(body):
    return body.split("With thanks,", 1)[1].split("\n", 3)[2]


def test_a_bare_login_is_not_signed_and_the_award_page_asks(da, world, client_in_program, sfeintuch):
    award = _award(da, world)
    body = client_in_program.get(reverse("supply_chain:award_detail", args=[award["id"]])).content.decode()
    assert 'data-testid="signed-as-prompt"' in body and "Signed as: [your name]" in body
    assert "[your name]" in _sign_off(body) and "sfeintuch" not in _sign_off(body)


def test_setting_the_name_signs_every_draft_and_the_page_shows_it(da, world, client_in_program, sfeintuch):
    award = _award(da, world)
    page = reverse("supply_chain:award_detail", args=[award["id"]])
    response = client_in_program.post(
        reverse("supply_chain:procurement_signed_as"),
        {"signed_as": "Sophie Feintuch", "next": page + "#selection-drafts"},
    )
    assert response.status_code == 302 and response["Location"] == page + "#selection-drafts"
    body = client_in_program.get(page).content.decode()
    assert 'data-testid="signed-as-prompt"' not in body
    assert 'data-testid="signed-as-name">Sophie Feintuch<' in body and "(change)" in body
    assert _sign_off(body) == "Sophie Feintuch"
    # Every Supply draft takes the same sender, over MCP and operations too.
    drafts = rop(da, "selection_drafts_render", award_id=award["id"])["drafts"]
    assert drafts and all("\nSophie Feintuch\n" in d["text"] for d in drafts)
    quote_request = rop(
        da,
        "request_render",
        tender_id=world["tender"]["id"],
        supplier_id=world["kanem"]["id"],
        commodity_slug="rutf",
    )
    assert "Sophie Feintuch" in quote_request["text"]


def test_the_tender_page_loads_and_asks_too(da, world, client_in_program, sfeintuch):
    page = client_in_program.get(reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]]))
    assert page.status_code == 200
    body = page.content.decode()
    assert 'data-testid="drafts"' in body or 'id="drafts"' in body
    assert 'data-testid="signed-as-prompt"' in body
    identity.set_signed_as(sfeintuch, "Sophie Feintuch")
    body = client_in_program.get(
        reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
    ).content.decode()
    assert 'data-testid="signed-as-name">Sophie Feintuch<' in body


def test_a_bad_name_is_refused_with_a_message_and_a_safe_redirect(client_in_program, sfeintuch):
    response = client_in_program.post(
        reverse("supply_chain:procurement_signed_as"), {"signed_as": "x" * 200, "next": "https://elsewhere.example/"}
    )
    assert response.status_code == 302 and response["Location"] == reverse("supply_chain:purchases")
    assert identity.signature(sfeintuch).source == "handle"


def test_the_service_account_still_sees_the_placeholder_and_no_prompt(da, world, client_in_program, django_user_model):
    bot = django_user_model.objects.get(pk=client_in_program.session["_auth_user_id"])
    bot.username, bot.name, bot.email = "ace", "ace", "ace@dimagi-ai.com"
    bot.save()
    da.user = bot
    award = _award(da, world)
    body = client_in_program.get(reverse("supply_chain:award_detail", args=[award["id"]])).content.decode()
    assert "[your name]" in _sign_off(body) and 'data-testid="signed-as' not in body
