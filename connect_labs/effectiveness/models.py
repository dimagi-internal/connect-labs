"""Saved scenarios: a person's named place to come back to.

A scenario (``effectiveness.scenario``) is the choices behind what someone is looking at: places, programmes, dials,
assumptions and the bar. A saved one keeps that as the canonical link, never the numbers, so opening it reads it with
today's models and is always the answer the pages would give for that link. ``saved_against`` records which model
specs it was saved against, so a page can say when the models have moved since.
"""

from django.conf import settings
from django.db import models


class SavedScenario(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_scenarios")
    name = models.CharField(max_length=200)
    note = models.TextField(blank=True)
    #: ``Scenario.to_params()``: the link, in its canonical form (only what differs from the default).
    params = models.JSONField()
    #: ``{"commit": <vendored model source>, "versions": {model: spec version}}`` when it was saved.
    saved_against = models.JSONField(default=dict, blank=True)
    #: Open to every signed-in Labs user to read and copy; only the owner changes or deletes it.
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="effectiveness_scenario_owner_name")]

    def __str__(self) -> str:
        return self.name
