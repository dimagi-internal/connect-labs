"""Scenario groups: the rules for keeping scenarios together under one question, and for comparing them.

A group (``models.ScenarioGroup``) is what is being thought about; its scenarios are the answers tried, in order. This
module is the one place the rules live, for the pages and for an agent, as ``saved`` is for single scenarios:

* A group is private until its owner shares it; only the owner changes it or the order of its scenarios. A shared group
  shows the viewer only the scenarios in it they may see.
* ``compare_group`` runs every scenario through the same sizing the pages use, sets the headlines side by side
  (``compare``), and says what moved since each scenario's snapshot. Computed headlines are cached by data version and
  release, so a re-compare after a deploy or a data load recomputes and the same page twice does not.
"""

from __future__ import annotations

import hashlib
import json

from django.core.cache import cache
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from connect_labs.effectiveness import compare, explore, people, saved, sizing
from connect_labs.effectiveness.models import SavedScenario, ScenarioGroup
from connect_labs.effectiveness.saved import SavedError, signed_in

MAX_NAME = 200
HEADLINE_TTL = 6 * 60 * 60


def _visible(user):
    if not signed_in(user):
        return ScenarioGroup.objects.filter(shared=True)
    return ScenarioGroup.objects.filter(Q(owner=user) | Q(shared=True))


def get(user, pk: int) -> ScenarioGroup:
    group = _visible(user).select_related("owner").filter(pk=pk).first()
    if group is None:
        raise SavedError("That group is not available to you.", 404)
    return group


def _owned(user, pk: int) -> ScenarioGroup:
    group = get(user, pk)
    if group.owner_id != user.pk:
        raise SavedError("Only the person who made a group can change it.", 403)
    return group


def members(user, group: ScenarioGroup) -> list[SavedScenario]:
    """The group's scenarios the viewer may see, in order."""
    return list(saved._visible(user).filter(group=group).select_related("owner").order_by("position", "pk"))


def as_dict(group: ScenarioGroup, viewer, *, with_scenarios: bool = True) -> dict:
    out = {
        "id": group.pk,
        "name": group.name,
        "question": group.question,
        "summary": group.summary,
        "shared": group.shared,
        "mine": signed_in(viewer) and group.owner_id == viewer.pk,
        "owner": getattr(group.owner, "name", "") or group.owner.get_username(),
        "path": reverse("cea:group", args=[group.pk]),
        "updated_at": group.updated_at.isoformat(),
    }
    if with_scenarios:
        out["scenarios"] = [
            {
                "id": s.pk,
                "name": s.name,
                "note": s.note,
                "position": s.position,
                "derived_from": s.derived_from_id,
                "open_path": f"{reverse('cea:workspace')}?scenario={s.pk}",
            }
            for s in members(viewer, group)
        ]
    return out


def listing(user) -> list[dict]:
    """The groups the viewer owns, then the ones others have shared, each with a count and its scenario names."""
    rows = list(_visible(user).select_related("owner"))
    return [
        as_dict(g, user) for g in sorted(rows, key=lambda g: (not (signed_in(user) and g.owner_id == user.pk), g.name))
    ]


def save(
    user,
    name: str,
    *,
    question: str | None = None,
    summary: str | None = None,
    shared: bool | None = None,
    overwrite: bool = False,
    scenario_ids: list[int] | None = None,
) -> tuple[ScenarioGroup, bool]:
    """Make a group, or (with ``overwrite``) change the one of that name. ``scenario_ids`` puts the user's own
    scenarios in it in that order (a scenario in no group before, or another of the user's, moves here). Returns
    ``(group, created)``."""
    if not signed_in(user):
        raise SavedError("Sign in to make a group.", 403)
    name = (name or "").strip()
    if not name:
        raise SavedError("Give the group a name.")
    if len(name) > MAX_NAME:
        raise SavedError(f"A name is at most {MAX_NAME} characters.")
    group = ScenarioGroup.objects.filter(owner=user, name=name).first()
    created = group is None
    if group and not overwrite:
        raise SavedError(f"You already have a group called {name!r}.", 409, id=group.pk)
    if created:
        group = ScenarioGroup(owner=user, name=name)
    for field, value in (("question", question), ("summary", summary)):
        if value is not None:
            setattr(group, field, value.strip())
    if shared is not None:
        group.shared = bool(shared)
    group.save()
    if scenario_ids is not None:
        arrange(user, group, scenario_ids)
    return group, created


def update(user, pk: int, *, name=None, question=None, summary=None, shared=None, scenario_ids=None) -> ScenarioGroup:
    group = _owned(user, pk)
    if name is not None:
        name = name.strip()
        if not name:
            raise SavedError("Give the group a name.")
        clash = ScenarioGroup.objects.filter(owner=user, name=name).exclude(pk=group.pk).first()
        if clash:
            raise SavedError(f"You already have a group called {name!r}.", 409, id=clash.pk)
        group.name = name
    for field, value in (("question", question), ("summary", summary)):
        if value is not None:
            setattr(group, field, value.strip())
    if shared is not None:
        group.shared = bool(shared)
    group.save()
    if scenario_ids is not None:
        arrange(user, group, scenario_ids)
    return group


def delete(user, pk: int) -> None:
    """Delete a group. Its scenarios are kept, ungrouped."""
    _owned(user, pk).delete()


def arrange(user, group: ScenarioGroup, scenario_ids: list[int]) -> None:
    """Put these of the user's own scenarios in ``group`` in this order, and take out of it any not listed."""
    ids = [int(i) for i in scenario_ids]
    if len(set(ids)) != len(ids):
        raise SavedError("A scenario can be in the order once.")
    mine = {s.pk: s for s in SavedScenario.objects.filter(owner=user, pk__in=ids)}
    missing = [i for i in ids if i not in mine]
    if missing:
        raise SavedError(f"Scenarios {missing} are not yours, so they cannot be put in your group.", 403)
    SavedScenario.objects.filter(group=group).exclude(pk__in=ids).update(group=None)
    for position, pk in enumerate(ids):
        SavedScenario.objects.filter(pk=pk).update(group=group, position=position)


# ------------------------------------------------------------------------------------------------------ comparing


def _release() -> str:
    from django.conf import settings

    return getattr(settings, "APP_RELEASE", None) or ""


def headline_for(record: SavedScenario) -> dict:
    """The scenario's headline now, from the sizing the pages use; cached under the data version and release.
    Raises ``explore.ExploreError`` for a scenario that can no longer be run."""
    scenario = saved.scenario_of(record)
    key = (
        "cea:headline:"
        + hashlib.sha1(f"{people.data_version()}|{json.dumps(record.params, sort_keys=True)}".encode()).hexdigest()
    )
    got = cache.get(key)
    if got is None:
        got = compare.headline(sizing.market_of(scenario))
        cache.set(key, got, HEADLINE_TTL)
    return got


def compare_group(user, group: ScenarioGroup, *, reference: int | None = None, rebaseline: bool = False) -> dict:
    """The group's visible scenarios side by side. With ``rebaseline`` (owner only) each scenario's snapshot becomes
    what it gives now."""
    if rebaseline and group.owner_id != getattr(user, "pk", None):
        raise SavedError("Only the person who made a group can re-baseline it.", 403)
    items = []
    for record in members(user, group):
        item = {
            "id": record.pk,
            "name": record.name,
            "note": record.note,
            "position": record.position,
            "derived_from": record.derived_from_id,
            "open_path": f"{reverse('cea:workspace')}?scenario={record.pk}",
            "snapshot": record.snapshot.get("headline") if record.snapshot else None,
            "baselined_at": (record.snapshot or {}).get("at"),
            "models_changed": [],
        }
        try:
            item["scenario"] = saved.scenario_of(record)
            item["headline"] = headline_for(record)
            item["models_changed"] = saved.models_changed(record.saved_against, item["scenario"])
        except explore.ExploreError as exc:
            from connect_labs.effectiveness.scenario import Scenario

            item.update(scenario=Scenario(), headline={}, error=exc.message)
        if rebaseline and item["headline"]:
            record.snapshot = {"headline": item["headline"], "at": timezone.now().isoformat(), "release": _release()}
            record.save(update_fields=["snapshot", "updated_at"])
            item["snapshot"], item["baselined_at"] = item["headline"], record.snapshot["at"]
        items.append(item)
    return {"group": as_dict(group, user, with_scenarios=False), **compare.compare(items, reference)}
