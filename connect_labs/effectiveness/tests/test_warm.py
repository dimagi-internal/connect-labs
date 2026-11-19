"""The places behind a scenario's answer are made ahead of time, for what pages open on and what is saved."""

import pytest
from django.conf import settings

from connect_labs.effectiveness import people, saved, warm


@pytest.fixture
def ann(django_user_model):
    return django_user_model.objects.create_user(username="ann", password="x")


@pytest.fixture
def made(monkeypatch):
    calls = []
    monkeypatch.setattr(people, "people_by_place", lambda question, grain: calls.append((question.cache_key(), grain)))
    return calls


@pytest.mark.django_db
class TestWhatIsWarmed:
    def test_a_page_opens_on_africa_at_every_grain(self, made):
        warm.warm()
        assert {grain for _, grain in made} == {"country", "territory"}

    def test_each_saved_question_is_made_once_whatever_the_dials(self, ann, made):
        for name, params in (
            ("a", {"models": "kc", "target": "1000000"}),
            ("b", {"models": "chc", "target": "5000000", "max_countries": "10"}),  # same places, other plan
            ("c", {"models": "kc", "indicator": "u5mr", "threshold": "80"}),  # a different question
        ):
            saved.save(ann, name, params)
        warm.warm()
        assert len(made) == len(set(made)) == 3  # Africa by country, by territory, and the u5mr question

    def test_a_saved_scenario_the_models_can_no_longer_read_is_skipped(self, ann, made):
        record, _ = saved.save(ann, "old", {"models": "kc"})
        type(record).objects.filter(pk=record.pk).update(params={"models": "no-such-model"})
        warm.warm()  # does not raise
        assert len(made) == 2

    def test_it_reports_what_each_took(self, made):
        assert all(set(t) == {"grain", "question", "seconds"} for t in warm.warm())


def test_the_task_runs_every_ten_minutes_and_is_registered():
    entry = settings.CELERY_BEAT_SCHEDULE["effectiveness-warm-people"]
    assert entry["task"] == "connect_labs.effectiveness.tasks.warm_people"
    from config import celery_app
    from connect_labs.effectiveness import tasks  # noqa: F401  (registers it)

    assert entry["task"] in celery_app.tasks


def test_the_places_outlive_an_answer():
    from connect_labs.labs.indicators import response_cache

    assert people.PEOPLE_TTL_SECONDS > response_cache.TTL_SECONDS
