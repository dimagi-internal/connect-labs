"""Saved scenarios: a person's named place to come back to.

A scenario (``effectiveness.scenario``) is the choices behind what someone is looking at: places, programmes, dials,
assumptions and the bar. A saved one keeps that as the canonical link, never the numbers, so opening it reads it with
today's models and is always the answer the pages would give for that link. ``saved_against`` records which model
specs it was saved against, so a page can say when the models have moved since.
"""

from django.conf import settings
from django.db import models


class ScenarioGroup(models.Model):
    """The scenarios that are all about one question, kept together so they can be navigated and compared.

    A group is the thing being thought about ("how do we reach a million Kangaroo Care cases at the best
    cost-effectiveness?"), not a folder: ``question`` says what it is, ``summary`` is the living account of what the
    scenarios showed, what was decided and what is still open, and the scenarios in it (``SavedScenario.group``, in
    ``position`` order) are the answers tried. Picking the work up again starts from the summary.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scenario_groups")
    name = models.CharField(max_length=200)
    #: What we are trying to think about, in a sentence or two.
    question = models.TextField(blank=True)
    #: What the scenarios showed, what was decided, what is open: written to be read cold by whoever continues.
    summary = models.TextField(blank=True)
    #: Open to every signed-in Labs user to read; only the owner changes it.
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="effectiveness_group_owner_name")]

    def __str__(self) -> str:
        return self.name


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
    #: The question this scenario is one answer to, and its place among the group's scenarios (0 first).
    group = models.ForeignKey(
        ScenarioGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="scenarios"
    )
    position = models.PositiveIntegerField(default=0)
    #: The scenario this one was made from by changing something (a branch in the thinking, not a copy).
    derived_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="derived")
    #: The headline the scenario gave when it was last re-baselined (``compare.headline`` + ``at`` + ``release``), so a
    #: later run can say what moved. Empty until first compared.
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="effectiveness_scenario_owner_name")]

    def __str__(self) -> str:
        return self.name


class InvestigationsVersion(models.Model):
    """One saved copy of the Investigations Tracker's hand-edited content (``investigations.content``).

    Every save is a new row, never an edit, so the list of rows is the history and any of them can be restored.
    Saving does not publish: the public page shows only the version ``InvestigationsPage.live`` points at.
    """

    #: The content as ``investigations.content.clean`` returned it.
    data = models.JSONField()
    #: What the editor said they changed, if anything.
    note = models.CharField(max_length=200, blank=True)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"Investigations content #{self.pk}"


class InvestigationsPage(models.Model):
    """The public tracker's state, one row (pk 1): which version is public, and the visit counts it may show.

    ``live`` empty means the page is not public and ``/labs/investigations/`` is a 404, which is how it starts and
    how it is taken down. ``delivery`` is written only by the timed job (``investigations.store.refresh_delivery``),
    so an anonymous request never counts visits.
    """

    live = models.ForeignKey(InvestigationsVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    #: ``{"services": {delivery type: approved visits}, "total": n, "as_of": "YYYY-MM-DD"}``, or empty.
    delivery = models.JSONField(default=dict, blank=True)
    delivery_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return "Investigations Tracker"
