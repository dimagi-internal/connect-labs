"""Saving and opening scenarios: the rules, then the HTTP layer and the pages that open one."""

import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from connect_labs.effectiveness import saved
from connect_labs.effectiveness.models import SavedScenario

pytestmark = pytest.mark.django_db

LINK = {"models": "chc,kc", "indicator": "u5mr", "threshold": "80", "chc_reach": "50", "i.chc.cost": "3"}


@pytest.fixture
def ann(django_user_model):
    return django_user_model.objects.create_user(username="ann", password="x")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", password="x")


def refuses(status, fn, *a, **k):
    with pytest.raises(saved.SavedError) as e:
        fn(*a, **k)
    assert e.value.status == status
    return e.value


class TestSaving:
    def test_it_keeps_the_canonical_link_and_what_it_was_saved_against(self, ann):
        record, created = saved.save(ann, "Sahel", {**LINK, "chc_extra": "0", "kc_reach": "50"})
        assert created and record.params == LINK  # what is at the default is not kept
        assert record.saved_against["commit"] and set(record.saved_against["versions"]) == {"chc", "kc"}

    def test_a_name_you_already_have_is_replaced_only_when_asked(self, ann):
        first, _ = saved.save(ann, "Sahel", LINK)
        err = refuses(409, saved.save, ann, "Sahel", {"models": "mbw"})
        assert err.extra["id"] == first.pk and SavedScenario.objects.get(pk=first.pk).params == LINK
        again, created = saved.save(ann, "Sahel", {"models": "mbw"}, overwrite=True, note="why", shared=True)
        assert not created and again.pk == first.pk
        assert again.params == {"models": "mbw"} and again.note == "why" and again.shared

    def test_two_people_can_use_the_same_name(self, ann, bob):
        saved.save(ann, "Sahel", LINK)
        saved.save(bob, "Sahel", {"models": "kc"})
        assert SavedScenario.objects.filter(name="Sahel").count() == 2

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201])
    def test_a_name_is_needed_and_bounded(self, ann, name):
        refuses(400, saved.save, ann, name, LINK)

    def test_a_scenario_that_could_not_be_opened_is_not_kept(self, ann):
        err = refuses(400, saved.save, ann, "Bad", {"i.chc.nonsense": "1"})
        assert "nonsense" in err.message and not SavedScenario.objects.exists()

    def test_it_takes_numbers_as_well_as_strings(self, ann):
        record, _ = saved.save(ann, "Numbers", {"models": "chc", "chc_reach": 50, "bar": 8.5})
        assert record.params == {"models": "chc", "bar": "8.5", "chc_reach": "50"}

    def test_nobody_signed_out_saves(self):
        refuses(403, saved.save, AnonymousUser(), "x", LINK)


class TestWhoSees:
    def test_a_private_scenario_is_not_found_by_anyone_else(self, ann, bob):
        record, _ = saved.save(ann, "Mine", LINK)
        refuses(404, saved.get, bob, record.pk)
        refuses(404, saved.get, AnonymousUser(), record.pk)
        assert saved.get(ann, record.pk).pk == record.pk

    def test_a_shared_scenario_is_read_by_everyone_and_deleted_only_by_its_owner(self, ann, bob):
        record, _ = saved.save(ann, "Open", LINK, shared=True)
        assert saved.get(bob, record.pk).pk == record.pk and saved.get(AnonymousUser(), record.pk).pk == record.pk
        refuses(403, saved.delete, bob, record.pk)
        saved.delete(ann, record.pk)
        refuses(404, saved.get, bob, record.pk)

    def test_deleting_what_you_cannot_see_is_not_found(self, ann, bob):
        record, _ = saved.save(ann, "Mine", LINK)
        refuses(404, saved.delete, bob, record.pk)

    def test_the_list_is_yours_then_what_others_shared(self, ann, bob):
        saved.save(ann, "A private", LINK)
        saved.save(ann, "A shared", LINK, shared=True)
        saved.save(bob, "B private", LINK)
        saved.save(bob, "B shared", LINK, shared=True)
        got = saved.listing(ann)
        assert sorted(r["name"] for r in got["mine"]) == ["A private", "A shared"]
        assert [r["name"] for r in got["shared"]] == ["B shared"] and got["shared"][0]["mine"] is False
        assert saved.listing(AnonymousUser())["mine"] == []

    def test_a_record_says_who_saved_it_and_where_it_opens(self, ann, bob):
        record, _ = saved.save(ann, "Open", LINK, shared=True)
        got = saved.as_dict(record, bob)
        assert got["owner"] == "ann" and got["mine"] is False
        assert got["open_path"] == f"/labs/cea/workspace/?scenario={record.pk}"


class TestWhenTheModelsMove:
    def test_a_scenario_saved_against_the_current_models_has_not_changed(self, ann):
        record, _ = saved.save(ann, "Now", LINK)
        assert saved.as_dict(record, ann)["models_changed"] == []

    def test_a_new_model_source_flags_the_programmes_whose_version_moved(self, ann):
        record, _ = saved.save(ann, "Old", LINK)
        record.saved_against = {
            "commit": "an-older-commit",
            "versions": {"chc": "0", "kc": record.saved_against["versions"]["kc"]},
        }
        assert saved.as_dict(record, ann)["models_changed"] == ["chc"]

    def test_a_scenario_that_no_longer_opens_says_so_instead_of_failing_the_list(self, ann):
        record = SavedScenario.objects.create(owner=ann, name="Gone", params={"i.chc.vanished": "1"})
        assert saved.as_dict(record, ann)["models_changed"] == ["chc", "kc", "mbw"]
        assert saved.listing(ann)["mine"][0]["name"] == "Gone"


class TestOverHttp:
    @pytest.fixture
    def as_ann(self, client, ann):
        client.force_login(ann)
        return client

    def post(self, client, body):
        return client.post(reverse("cea:scenarios"), json.dumps(body), content_type="application/json")

    def test_save_list_read_and_delete(self, as_ann):
        r = self.post(as_ann, {"name": "Sahel", "params": LINK, "note": "n", "shared": False})
        assert r.status_code == 201 and r.json()["created"] and r.json()["params"] == LINK
        pk = r.json()["id"]
        assert [s["id"] for s in as_ann.get(reverse("cea:scenarios")).json()["mine"]] == [pk]
        assert as_ann.get(reverse("cea:scenario", args=[pk])).json()["name"] == "Sahel"
        assert as_ann.delete(reverse("cea:scenario", args=[pk])).json() == {"deleted": pk}
        assert as_ann.get(reverse("cea:scenario", args=[pk])).status_code == 404

    def test_a_clash_is_a_409_that_names_the_one_in_the_way_and_overwrite_replaces_it(self, as_ann):
        pk = self.post(as_ann, {"name": "Sahel", "params": LINK}).json()["id"]
        clash = self.post(as_ann, {"name": "Sahel", "params": {"models": "mbw"}})
        assert clash.status_code == 409 and clash.json()["id"] == pk and "Sahel" in clash.json()["error"]
        done = self.post(as_ann, {"name": "Sahel", "params": {"models": "mbw"}, "overwrite": True})
        assert done.status_code == 200 and done.json()["params"] == {"models": "mbw"}

    def test_a_bad_request_says_why(self, as_ann):
        bad = self.post(as_ann, {"name": "x", "params": {"i.chc.nonsense": "1"}})
        assert bad.status_code == 400 and "nonsense" in bad.json()["error"]
        notjson = as_ann.post(reverse("cea:scenarios"), "{nope", content_type="application/json")
        assert notjson.status_code == 400

    def test_someone_elses_private_scenario_is_a_404_and_their_shared_one_cannot_be_deleted(self, client, ann, bob):
        mine, _ = saved.save(ann, "Mine", LINK)
        shared, _ = saved.save(ann, "Shared", LINK, shared=True)
        client.force_login(bob)
        assert client.get(reverse("cea:scenario", args=[mine.pk])).status_code == 404
        assert client.get(reverse("cea:scenario", args=[shared.pk])).status_code == 200
        assert client.delete(reverse("cea:scenario", args=[shared.pk])).status_code == 403

    def test_signed_out_it_is_the_labs_sign_in(self, client):
        assert client.get(reverse("cea:scenarios")).status_code == 302


def embedded(body: str) -> dict:
    return json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))


@pytest.mark.parametrize("page", ["cea:market", "cea:workspace"])
class TestOpeningOne:
    @pytest.fixture
    def signed_in(self, client, ann):
        client.force_login(ann)
        return client

    def test_the_page_opens_the_saved_scenario(self, signed_in, ann, page):
        record, _ = saved.save(ann, "Sahel", LINK)
        data = embedded(signed_in.get(reverse(page), {"scenario": record.pk}).content.decode())
        assert data["state"]["models"] == ["chc", "kc"] and data["state"]["dials"]["chc"]["reach"] == 50.0
        assert data["state"]["overrides"] == {"chc": {"cost": 3.0}}
        assert data["loaded"]["name"] == "Sahel" and data["error"] is None

    def test_anything_else_in_the_link_changes_it(self, signed_in, ann, page):
        record, _ = saved.save(ann, "Sahel", LINK)
        data = embedded(signed_in.get(reverse(page), {"scenario": record.pk, "chc_reach": "20"}).content.decode())
        assert data["state"]["dials"]["chc"]["reach"] == 20.0 and data["state"]["indicator"] == "u5mr"

    def test_one_you_may_not_see_opens_the_default_and_says_so(self, signed_in, bob, page):
        record, _ = saved.save(bob, "Bob's", LINK)
        data = embedded(signed_in.get(reverse(page), {"scenario": record.pk}).content.decode())
        assert data["loaded"] is None and "not available" in data["error"] and data["state"]["models"] == ["chc"]

    def test_a_nonsense_id_is_the_same_answer(self, signed_in, page):
        data = embedded(signed_in.get(reverse(page), {"scenario": "abc"}).content.decode())
        assert "not available" in data["error"]

    def test_the_page_carries_what_saving_needs(self, signed_in, page):
        data = embedded(signed_in.get(reverse(page)).content.decode())
        assert data["signed_in"] is True and data["csrf"] and data["scenarios_url"] == "/labs/cea/scenarios/"
