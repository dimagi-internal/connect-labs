"""Scenario groups: keeping scenarios under one question, walking between them, and setting them side by side."""

import json

import pytest
from django.urls import reverse

from connect_labs.effectiveness import compare, saved, scenario_groups
from connect_labs.effectiveness.scenario import Scenario

# ------------------------------------------------------------------------------------------------ pure: compare


def sc(**params):
    return Scenario.from_params({"models": "kc", "grain": "territory", "target": "1000000", **params})


class TestWhatAScenarioSets:
    def test_the_model_as_published_sets_nothing_beyond_its_target_and_grain(self):
        assert compare.sets(sc()) == ["Places: territories", "Target: 1,000,000 people"]

    def test_only_what_differs_from_the_model_is_said(self):
        got = compare.sets(sc(place_risk="0", max_conflict="10", cost_basis="observed", trust="80"))
        assert "Risk: the model's one figure everywhere" in got
        assert "Limits: conflict at most 10" in got
        assert "Price: from delivered programmes where there are some" in got
        assert "Trust in place figures: 80%" in got

    def test_the_difference_between_two_scenarios_is_in_words(self):
        assert compare.differences(sc(), sc(max_remote="40")) == ["Limits: none → remote at most 40"]
        assert compare.differences(sc(), sc()) == []


class TestComparing:
    def item(self, i, deaths, cost, snap=None, **params):
        return {
            "id": i,
            "name": f"S{i}",
            "scenario": sc(**params),
            "headline": {"deaths": deaths, "cost": cost, "cost_per_death": cost / deaths, "multiple": deaths / 1000},
            "snapshot": snap,
        }

    def test_the_best_of_each_result_is_marked_and_each_is_measured_against_the_reference(self):
        got = compare.compare([self.item(1, 1000, 100.0), self.item(2, 1200, 130.0, max_remote="40")])
        one, two = got["scenarios"]
        assert (
            two["delta"]["deaths"] == pytest.approx(20.0) and "deaths" in two["best"] and "deaths" not in one["best"]
        )
        assert "cost_per_death" in one["best"]  # lower is better: 100/1000 beats 130/1200
        assert two["differs"] == ["Limits: none → remote at most 40"] and one["differs"] == []
        assert two["differs_from"] == {"id": 1, "name": "S1"} and one["differs_from"] is None

    def test_a_scenario_is_read_against_the_one_it_was_made_from_when_that_is_in_the_group(self):
        a, b = self.item(1, 1000, 100.0), self.item(2, 1100, 100.0, max_remote="40")
        c = self.item(3, 1200, 100.0, max_remote="40", max_conflict="10")
        c["derived_from"] = 2
        got = compare.compare([a, b, c])["scenarios"][2]
        assert got["differs_from"]["id"] == 2 and got["differs"] == [
            "Limits: remote at most 40 → conflict at most 10; remote at most 40"
        ]

    def test_when_scenarios_are_alike_on_a_result_nobody_is_best_on_it(self):
        got = compare.compare([self.item(1, 1000, 100.0), self.item(2, 1000, 100.0, max_remote="40")])["scenarios"]
        assert got[0]["best"] == [] and got[1]["best"] == []

    def test_a_tie_for_the_top_is_nobodys_win_but_a_clear_leader_is_marked(self):
        got = compare.compare([self.item(1, 900, 100.0), self.item(2, 1000, 100.0), self.item(3, 1000, 100.0)])
        assert "deaths" not in got["scenarios"][1]["best"] and "deaths" not in got["scenarios"][2]["best"]
        lead = compare.compare([self.item(1, 900, 100.0), self.item(2, 1000, 100.0)])["scenarios"][1]
        assert "deaths" in lead["best"]

    def test_another_scenario_can_be_the_reference(self):
        got = compare.compare([self.item(1, 1000, 100.0), self.item(2, 1200, 130.0)], reference=2)
        assert got["reference"] == 2 and got["scenarios"][0]["delta"]["deaths"] == pytest.approx(-16.666, abs=0.01)

    def test_a_number_that_moved_since_its_snapshot_says_what_it_was(self):
        snap = {"deaths": 1000, "cost": 100.0, "multiple": 1.0}
        got = compare.compare([self.item(1, 1100, 100.0, snap=snap)])["scenarios"][0]
        assert got["drift"]["deaths"] == (1000, 1100) and "cost" not in got["drift"]

    def test_no_snapshot_is_not_drift_and_an_empty_group_compares_to_nothing(self):
        assert compare.compare([self.item(1, 1000, 100.0)])["scenarios"][0]["drift"] is None
        assert compare.compare([])["scenarios"] == []


# ---------------------------------------------------------------------------------------------- the service


@pytest.fixture
def ann(django_user_model):
    return django_user_model.objects.create_user(username="ann", password="x")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", password="x")


def make(user, name, group=None, **kw):
    return saved.save(user, name, {"models": "kc", "target": "1000000", **kw.pop("params", {})}, group=group, **kw)[0]


@pytest.mark.django_db
class TestGroups:
    def test_a_group_holds_scenarios_in_order_and_a_scenario_knows_its_neighbours(self, ann):
        g, created = scenario_groups.save(ann, "A million", question="How do we reach a million?", shared=True)
        a, b, c = (make(ann, n, g.pk) for n in "abc")
        assert created and [s.position for s in (a, b, c)] == [0, 1, 2]
        mid = saved.as_dict(b, ann)["group"]
        assert (mid["position"], mid["count"]) == (2, 3)
        assert mid["previous"]["id"] == a.pk and mid["next"]["id"] == c.pk
        assert saved.as_dict(a, ann)["group"]["previous"] is None

    def test_a_scenario_can_be_derived_from_another_and_says_so(self, ann):
        a = make(ann, "base")
        b = make(ann, "branch", derived_from=a.pk)
        assert saved.as_dict(b, ann)["derived_from"]["id"] == a.pk

    def test_a_group_name_is_unique_per_person_and_only_an_owned_group_takes_a_scenario(self, ann, bob):
        g, _ = scenario_groups.save(ann, "G")
        with pytest.raises(saved.SavedError) as e:
            scenario_groups.save(ann, "G")
        assert e.value.status == 409
        with pytest.raises(saved.SavedError) as e:
            make(bob, "x", g.pk)
        assert e.value.status == 403

    def test_arranging_orders_the_listed_scenarios_and_releases_the_rest(self, ann):
        g, _ = scenario_groups.save(ann, "G")
        a, b, c = (make(ann, n, g.pk) for n in "abc")
        scenario_groups.arrange(ann, g, [c.pk, a.pk])
        order = [s.name for s in scenario_groups.members(ann, g)]
        assert order == ["c", "a"]
        b.refresh_from_db()
        assert b.group is None

    def test_someone_elses_scenarios_cannot_be_put_in_your_group(self, ann, bob):
        g, _ = scenario_groups.save(ann, "G")
        theirs = make(bob, "theirs")
        with pytest.raises(saved.SavedError):
            scenario_groups.arrange(ann, g, [theirs.pk])

    def test_a_private_group_is_not_found_by_others_and_a_shared_one_shows_only_what_they_may_see(self, ann, bob):
        g, _ = scenario_groups.save(ann, "G", shared=False)
        with pytest.raises(saved.SavedError) as e:
            scenario_groups.get(bob, g.pk)
        assert e.value.status == 404
        scenario_groups.update(ann, g.pk, shared=True)
        make(ann, "open", g.pk, shared=True)
        make(ann, "closed", g.pk, shared=False)
        assert [s.name for s in scenario_groups.members(bob, scenario_groups.get(bob, g.pk))] == ["open"]

    def test_only_the_owner_edits_a_group_and_deleting_it_keeps_its_scenarios(self, ann, bob):
        g, _ = scenario_groups.save(ann, "G", shared=True)
        s = make(ann, "s", g.pk)
        with pytest.raises(saved.SavedError) as e:
            scenario_groups.update(bob, g.pk, summary="mine now")
        assert e.value.status == 403
        scenario_groups.delete(ann, g.pk)
        s.refresh_from_db()
        assert s.group is None

    def test_resaving_a_different_scenario_drops_its_old_snapshot(self, ann):
        s = make(ann, "s")
        s.snapshot = {"headline": {"deaths": 1}, "at": "x"}
        s.save()
        saved.save(ann, "s", {"models": "kc", "target": "2000000"}, overwrite=True)
        s.refresh_from_db()
        assert s.snapshot == {}


@pytest.fixture
def fake_headline(monkeypatch):
    """Skip the sizing: a headline that depends on the scenario's ceilings, so scenarios differ."""

    def headline(record):
        caps = len(record.params.get("max_remote", "")) + len(record.params.get("max_conflict", ""))
        deaths = 20_000 - 100 * caps
        return {
            "deaths": deaths,
            "cost": 60e6,
            "cost_per_death": 60e6 / deaths,
            "multiple": deaths / 1500,
            "people": 1e6,
            "places": 100,
            "countries": 20,
            "deaths_p05": deaths - 2000,
            "deaths_p95": deaths + 2000,
            "overlap": 0.9,
            "regional_proxy_people": 5e5,
            "births_off_people": 0,
            "feasible": True,
            "left_out": {},
        }

    monkeypatch.setattr(scenario_groups, "headline_for", headline)


@pytest.mark.django_db
class TestComparingAGroup:
    def group(self, ann):
        g, _ = scenario_groups.save(ann, "A million", question="Q?", summary="What we know.", shared=True)
        make(ann, "one", g.pk, shared=True)
        make(ann, "two", g.pk, shared=True, params={"max_remote": "40"})
        return g

    def test_the_scenarios_come_back_side_by_side(self, ann, fake_headline):
        data = scenario_groups.compare_group(ann, self.group(ann))
        names = [s["name"] for s in data["scenarios"]]
        assert names == ["one", "two"] and data["group"]["summary"] == "What we know."
        assert data["scenarios"][1]["differs"] == ["Limits: none → remote at most 40"]

    def test_rebaselining_stores_what_each_gives_and_later_drift_shows_against_it(
        self, ann, fake_headline, monkeypatch
    ):
        g = self.group(ann)
        first = scenario_groups.compare_group(ann, g, rebaseline=True)
        assert all(s["baselined_at"] for s in first["scenarios"])
        calm = scenario_groups.compare_group(ann, g)
        assert all(not s["drift"] for s in calm["scenarios"])  # unchanged since the baseline
        monkeypatch.setattr(scenario_groups, "headline_for", lambda r: {"deaths": 1.0, "cost": 1.0})
        moved = scenario_groups.compare_group(ann, g)["scenarios"][0]["drift"]
        assert moved["deaths"][1] == 1.0

    def test_only_the_owner_can_rebaseline(self, ann, bob, fake_headline):
        g = self.group(ann)
        with pytest.raises(saved.SavedError) as e:
            scenario_groups.compare_group(bob, g, rebaseline=True)
        assert e.value.status == 403


# ------------------------------------------------------------------------------------------------ the pages


@pytest.mark.django_db
class TestPages:
    @pytest.fixture
    def seen(self, client, ann, fake_headline):
        g, _ = scenario_groups.save(
            ann, "A million", question="How do we reach a million?", summary="Notes.", shared=True
        )
        make(ann, "one", g.pk, shared=True, note="Why one.")
        make(ann, "two", g.pk, shared=True, params={"max_remote": "40"}, note="Why two.")
        client.force_login(ann)
        return g

    def test_the_group_page_sets_the_scenarios_side_by_side(self, client, seen):
        body = client.get(reverse("cea:group", args=[seen.pk]), {"live": "1"}).content.decode()
        for want in (
            "How do we reach a million?",
            "What we know so far",
            "one",
            "two",
            "Why two.",
            "Deaths averted a year",
            "Limits",
            "none → remote at most 40",
            'data-testid="group-compare"',
        ):
            assert want in body

    def test_a_reference_can_be_chosen_and_json_is_the_same_answer(self, client, seen):
        got = client.get(
            reverse("cea:group", args=[seen.pk]), {"format": "json", "ref": seen.scenarios.last().pk}
        ).json()
        assert got["reference"] == seen.scenarios.last().pk and "scenario" not in got["scenarios"][0]

    def test_the_list_page_names_each_group_and_its_scenarios(self, client, seen):
        body = client.get(reverse("cea:groups")).content.decode()
        assert "A million" in body and 'data-testid="group-card"' in body
        assert "Compare the 2 scenarios" in body and body.count("Open in Workspace") == 2

    def test_a_group_is_made_edited_baselined_and_deleted_over_http(self, client, ann, seen):
        made = client.post(
            reverse("cea:groups"), json.dumps({"name": "Other", "question": "Q"}), content_type="application/json"
        )
        assert made.status_code == 201
        gid = made.json()["id"]
        path = reverse("cea:group", args=[gid])
        edited = client.patch(path, json.dumps({"summary": "Decided X."}), content_type="application/json").json()
        assert edited["summary"] == "Decided X."
        assert client.post(reverse("cea:group_baseline", args=[seen.pk])).status_code == 200
        assert client.delete(path).json() == {"deleted": gid}

    def test_a_private_group_is_a_404_page_for_someone_else(self, client, ann, bob, seen):
        scenario_groups.update(ann, seen.pk, shared=False)
        client.force_login(bob)
        assert client.get(reverse("cea:group", args=[seen.pk])).status_code == 404

    def test_the_workspace_shows_the_scenario_its_group_and_its_neighbours_above_the_numbers(self, client, seen):
        second = seen.scenarios.order_by("position").last()
        body = client.get(reverse("cea:workspace"), {"scenario": second.pk}).content.decode()
        assert 'data-testid="scenario-header"' in body
        assert "A million" in body and "scenario 2 of 2" in body and "Why two." in body
        assert "Limits: remote at most 40" in body and "&larr; Previous" in body and "Compare all 2" in body


@pytest.mark.django_db
class TestTheReport:
    def test_it_prints_the_summary_and_each_scenarios_headline_and_what_moved(
        self, ann, fake_headline, monkeypatch, capsys
    ):
        from django.core.management import call_command

        g, _ = scenario_groups.save(ann, "A million", question="Q?", summary="What we know.")
        make(ann, "one", g.pk)
        call_command("cea_group_report", "A million", "--rebaseline")
        monkeypatch.setattr(scenario_groups, "headline_for", lambda r: {"deaths": 25_000.0, "cost": 60e6})
        call_command("cea_group_report", str(g.pk))
        out = capsys.readouterr().out
        assert "What we know." in out and "Baselined" in out and "(was 20,000)" in out


@pytest.mark.django_db
class TestTheGroupPageIsQuickAndReadable:
    @pytest.fixture
    def seen(self, client, ann):
        g, _ = scenario_groups.save(
            ann,
            "A million",
            question="How?",
            summary="## What it shows\nFirst para.\n- one\n- two\n## Open questions\n1. Is it so?\n2. Or not?",
            shared=True,
        )
        a = make(ann, "one", g.pk, shared=True, note="Why one. And more detail that follows.")
        a.snapshot = {
            "headline": {
                "deaths": 20_000.0,
                "cost": 6e7,
                "cost_per_death": 3000.0,
                "multiple": 13.0,
                "deaths_p05": 18_000.0,
                "deaths_p95": 22_000.0,
            },
            "at": "2026-11-17T00:00:00+00:00",
        }
        a.save()
        client.force_login(ann)
        return g

    def test_it_opens_from_the_baseline_without_computing_anything(self, client, seen, monkeypatch):
        def boom(record):
            raise AssertionError("the page must not size a scenario to open")

        monkeypatch.setattr(scenario_groups, "headline_for", boom)
        body = client.get(reverse("cea:group", args=[seen.pk])).content.decode()
        assert 'data-testid="group-cards"' in body and "20,000" in body and "Check against today" in body
        assert 'data-testid="group-charts"' in body and "baselined" in body

    def test_the_summary_is_sections_you_open_not_a_wall_of_text(self):
        from connect_labs.effectiveness.views.groups import first_sentence, sections

        got = sections("## What it shows\nFirst para.\n- one\n- two\n## Open\n1. Is it so?\n2. Or not?")
        assert [s["title"] for s in got] == ["What it shows", "Open"]
        assert got[0]["blocks"] == [{"kind": "p", "items": ["First para."]}, {"kind": "ul", "items": ["one", "two"]}]
        assert got[1]["blocks"] == [{"kind": "ol", "items": ["Is it so?", "Or not?"]}]
        assert [s["title"] for s in sections("Just prose.")] == ["What we know"]
        assert first_sentence("Why one. And more detail.") == "Why one."

    def test_the_page_offers_the_agent_where_one_may_be_offered_and_the_scopes_are_registered(self):
        from connect_labs.labs import canopy

        for page in ("cea:group", "cea:workspace", "cea:market"):
            assert canopy.PAGE_SCOPES[page] == ("targeting:read", "scenarios:read") and page in canopy.PAGE_TITLES
        assert canopy.SCOPE_TOOLS["scenarios:read"] == {"cea_scenarios", "cea_scenario_groups"}
        assert "scenarios:read" in canopy.SCOPE_LABELS


def test_a_charts_labels_drop_what_every_name_starts_with():
    from connect_labs.effectiveness.views.groups import shortened

    got = shortened(["KC 1M · 1 · flat", "KC 1M · 2 · scaled", "KC 1M · 3 · ceilings"], [1, 2, 3])
    assert got == {1: "1 · flat", 2: "2 · scaled", 3: "3 · ceilings"}
    assert shortened(["Only one"], [1]) == {1: "Only one"}
    assert shortened(["Alpha", "Beta"], [1, 2]) == {1: "Alpha", 2: "Beta"}
