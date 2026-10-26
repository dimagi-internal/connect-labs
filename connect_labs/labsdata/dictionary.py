"""A data space's dictionary: what every field of its visits means, and what an export
does with it.

Every function takes an ``OpenSpace`` -- a saved space ``spaces.open`` let the person
into -- so nothing here can be reached around the space's own rules. Reading is for
anyone who can open the space; changing a setting, a meaning or a merge is for its
managers (``spaces.require_manager``), in their own session; every change is recorded
(``DictionaryChange``).

**Drop unless allowed.** An entry is exported only once a person has reviewed it
(``effective``): until then it is dropped whatever its setting says. A scan suggests a
setting for every field from what kind of value it holds (``labs.deid.classify``, the
same rules the synthetic cloner uses), and a reviewer accepts or changes it.

**A scan is structure only.** It walks the space's cached visits and records each
field's exact paths, the deliver units and app builds it was seen in, how many visits
carried it and what kind of value it holds. It holds values in memory to judge the kind
and stores none of them. A person runs it from the page (the app-change check): it is
also what notices a new app build's new or vanished fields (``DictionaryFlag``).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from django.db import transaction
from django.utils import timezone

from connect_labs.labs.deid import classify, fieldscan, transforms

from . import fields as field_index
from . import spaces
from .models import DataDictionary, DictionaryChange, DictionaryEntry, DictionaryFlag, DictionarySource

#: Distinct values held per field while scanning: enough to tell a closed list from free
#: text (``classify.MAX_CATEGORY_VALUES``), never the whole column.
_VALUES_HELD = classify.MAX_CATEGORY_VALUES + 10
#: App builds kept per source: the newest ones.
_BUILDS_KEPT = 50

#: The columns of ``visits`` (``engine.VISIT_COLUMNS``) as entries: kind, suggested setting
#: and params, whether it can single someone out in combination, what it means.
VISIT_COLUMN_ENTRIES: dict[str, tuple[str, str, dict, bool]] = {
    "opportunity_id": (classify.CATEGORICAL, "keep", {}, False),
    "opportunity_name": (classify.CATEGORICAL, "keep", {}, False),
    "llo": (classify.CATEGORICAL, "keep", {}, False),
    "visit_id": (classify.ID, "pseudonymise", {"namespace": "visit"}, False),
    "visit_date": (classify.DATE, "keep", {}, True),
    "username": (classify.ID, "pseudonymise", {"namespace": "worker"}, True),
    "status": (classify.CATEGORICAL, "keep", {}, False),
    "deliver_unit": (classify.CATEGORICAL, "keep", {}, False),
    "entity_id": (classify.CASE_ID, "pseudonymise", {"namespace": "case"}, False),
    "entity_name": (classify.IDENTIFIER, "drop", {}, False),
    "flagged": (classify.CATEGORICAL, "keep", {}, False),
    "review_status": (classify.CATEGORICAL, "keep", {}, False),
    "date_created": (classify.DATE, "month", {}, True),
    "reason": (classify.FREE_TEXT, "drop", {}, False),
    "location": (classify.GPS, "drop", {}, True),
    "flag_reason": (classify.FREE_TEXT, "drop", {}, False),
    "status_modified_date": (classify.DATE, "drop", {}, False),
    "review_created_on": (classify.DATE, "drop", {}, False),
    "justification": (classify.FREE_TEXT, "drop", {}, False),
    "completed_work": (classify.FREE_TEXT, "drop", {}, False),
    "completed_work_id": (classify.ID, "pseudonymise", {"namespace": "completed_work"}, False),
    "deliver_unit_id": (classify.CATEGORICAL, "keep", {}, False),
    "images": (classify.MEDIA, "drop", {}, False),
    "user_visit_id": (classify.ID, "pseudonymise", {"namespace": "visit"}, False),
}


class DictionaryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def column_key(column: str) -> str:
    return f"@{column}"


def effective(entry: DictionaryEntry | dict) -> str:
    """What an export does with this entry: its setting once reviewed, else drop."""
    if isinstance(entry, dict):
        return entry["setting"] if entry.get("reviewed") else transforms.DROP_SETTING
    return entry.setting if entry.reviewed else transforms.DROP_SETTING


# --- reading --------------------------------------------------------------------------


def for_space(opened: spaces.OpenSpace) -> DataDictionary:
    """The space's dictionary, made (with an entry per ``visits`` column) the first time."""
    if not opened.saved:
        raise DictionaryError("not_saved", "Save this selection as a data space before giving it a dictionary.")
    dictionary, made = DataDictionary.objects.get_or_create(space=opened.space)
    if made:
        _add_column_entries(dictionary)
    return dictionary


def _add_column_entries(dictionary: DataDictionary) -> None:
    from .engine import VISIT_COLUMNS

    meanings = {name: meaning for name, _type, meaning in VISIT_COLUMNS}
    DictionaryEntry.objects.bulk_create(
        [
            DictionaryEntry(
                dictionary=dictionary,
                key=column_key(column),
                label=column,
                column=column,
                kind=kind,
                meaning=meanings.get(column, ""),
                setting=setting,
                params=params,
                suggested_setting=setting,
                suggested_params=params,
                quasi=quasi,
            )
            for column, (kind, setting, params, quasi) in VISIT_COLUMN_ENTRIES.items()
        ]
    )


def entry_dict(entry: DictionaryEntry) -> dict:
    return {
        "key": entry.key,
        "label": entry.label,
        "column": entry.column or None,
        "kind": entry.kind,
        "meaning": entry.meaning,
        "level": entry.level,
        "setting": entry.setting,
        "params": entry.params or {},
        "suggested_setting": entry.suggested_setting,
        "suggested_params": entry.suggested_params or {},
        "quasi": entry.quasi,
        "reviewed": entry.reviewed,
        "reviewed_by": getattr(entry.reviewed_by, "username", None),
        "reviewed_at": entry.reviewed_at.isoformat() if entry.reviewed_at else None,
        "effective": effective(entry),
        "sources": [
            {
                "path": s.path,
                "deliver_unit": s.deliver_unit,
                "builds": s.builds,
                "visits": s.visits,
                "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            }
            for s in entry.sources.all()
        ],
    }


def describe(opened: spaces.OpenSpace) -> dict:
    """The whole dictionary as the page shows it: entries, open flags, recent changes."""
    dictionary = for_space(opened)
    entries = dictionary.entries.select_related("reviewed_by").prefetch_related("sources")
    flags = dictionary.flags.filter(resolved_at__isnull=True).select_related("entry")
    changes = dictionary.changes.select_related("user")[:100]
    listed = [entry_dict(e) for e in entries]
    return {
        "space_id": opened.id,
        "registry_id": dictionary.registry_id,
        "scanned_at": dictionary.scanned_at.isoformat() if dictionary.scanned_at else None,
        "scanned_visits": dictionary.scanned_visits,
        "is_manager": opened.is_manager,
        "counts": {
            "entries": len(listed),
            "reviewed": sum(1 for e in listed if e["reviewed"]),
            "exported": sum(1 for e in listed if e["effective"] != transforms.DROP_SETTING),
        },
        "entries": listed,
        "flags": [
            {
                "id": f.pk,
                "kind": f.kind,
                "label": f.get_kind_display(),
                "entry": f.entry.key if f.entry else None,
                "detail": f.detail,
                "created_at": f.created_at.isoformat(),
            }
            for f in flags
        ],
        "changes": [
            {
                "entry": c.entry_key,
                "action": c.action,
                "before": c.before,
                "after": c.after,
                "user": getattr(c.user, "username", None),
                "at": c.at.isoformat(),
            }
            for c in changes
        ],
        "settings": [{"value": s, "label": transforms.SETTING_LABELS[s]} for s in transforms.SETTINGS],
        "kinds": list(classify.KINDS),
    }


def snapshot(dictionary: DataDictionary) -> list[dict]:
    """The entries as an export applies them: frozen into the export when it is asked for,
    so a setting changed while it builds does not change what it does."""
    out = []
    for e in dictionary.entries.prefetch_related("sources"):
        out.append(
            {
                "key": e.key,
                "label": e.label,
                "column": e.column or None,
                "kind": e.kind,
                "meaning": e.meaning,
                "level": e.level,
                "setting": e.setting,
                "params": e.params or {},
                "quasi": e.quasi,
                "reviewed": e.reviewed,
                "sources": [
                    {"path": s.path, "deliver_unit": s.deliver_unit, "builds": s.builds} for s in e.sources.all()
                ],
            }
        )
    return out


# --- the scan (structure only) ------------------------------------------------------------


def walk_visits(opps, *, date_from=None, date_to=None):
    """``(form_json, deliver_unit)`` for every cached visit of ``opps`` (one generation each),
    streamed. The scan's and the export's one way to read forms for structure."""
    for o in opps:
        rows = field_index._current_rows(o.id)
        if date_from:
            rows = rows.filter(visit_date__gte=date_from)
        if date_to:
            rows = rows.filter(visit_date__lte=date_to)
        yield from rows.values_list("form_json", "deliver_unit").iterator(chunk_size=500)


def observe(opps, *, date_from=None, date_to=None) -> dict:
    """What the forms carry: per exact ``(path, deliver_unit)``, the visits that filled it and
    the app builds it came from; per normalised key, a bounded sample of values (to judge
    the kind, then dropped). Nothing returned here is stored but paths, counts and kinds."""
    seen: dict[tuple[str, str], dict] = defaultdict(lambda: {"visits": 0, "builds": set()})
    values: dict[str, Counter] = defaultdict(Counter)
    visits = 0
    for form_json, deliver_unit in walk_visits(opps, date_from=date_from, date_to=date_to):
        visits += 1
        build = fieldscan.app_build(form_json)
        counted = set()
        for path, value in fieldscan.walk(form_json or {}):
            if value in (None, ""):
                continue
            dotted = fieldscan.dotted(path)
            source = (dotted, deliver_unit or "")
            if source not in counted:
                counted.add(source)
                seen[source]["visits"] += 1
                if build is not None:
                    seen[source]["builds"].add(build)
            sample = values[fieldscan.normalise(dotted)]
            text = value if not isinstance(value, str) else value[: classify.MAX_REPLAYABLE_VALUE_LEN * 4]
            if text in sample or len(sample) < _VALUES_HELD:
                sample[text] += 1
    kinds = {key: classify.classify(key, sample) for key, sample in values.items()}
    return {"visits": visits, "sources": dict(seen), "kinds": kinds}


_RANK = {k: i for i, k in enumerate(classify.KINDS)}  # identifier first: most sensitive


def _more_sensitive(new: str, old: str) -> bool:
    return _RANK.get(new, 0) < _RANK.get(old, len(_RANK))


@transaction.atomic
def scan(opened: spaces.OpenSpace, *, user) -> dict:
    """Walk the space's cached visits and bring the dictionary up to date: a new entry for
    every new field (unreviewed, so dropped), every spelling and app build recorded, flags
    for what a person should look at. Anyone who can open the space may run it: it reads
    structure only, and changes no setting a person made."""
    dictionary = for_space(opened)
    observed = observe(opened.selection.opportunities)
    now = timezone.now()
    entries = {e.key: e for e in dictionary.entries.all()}
    known = {
        (s.entry_id, s.path, s.deliver_unit): s for s in DictionarySource.objects.filter(entry__dictionary=dictionary)
    }
    open_flags = {
        (f.kind, f.entry_id, tuple(sorted(f.detail.get("paths", []))))
        for f in dictionary.flags.filter(resolved_at__isnull=True)
    }
    paths_of: dict[int, set[str]] = defaultdict(set)
    for entry_id, path, _du in known:
        paths_of[entry_id].add(path)
    created: set[str] = set()
    added = {"entries": 0, "sources": 0, "flags": 0}

    def flag(kind, entry, detail):
        sig = (kind, entry.pk if entry else None, tuple(sorted(detail.get("paths", []))))
        if sig in open_flags:
            return
        open_flags.add(sig)
        DictionaryFlag.objects.create(dictionary=dictionary, entry=entry, kind=kind, detail=detail)
        added["flags"] += 1

    # A spelling someone has placed (by a scan, or by merging two entries) stays where they
    # put it: look an observed path up by the entry that already holds it, then by any entry
    # holding a spelling it normalises to, and only then by its own key.
    by_pk = {e.pk: e for e in entries.values()}
    entry_of: dict[str, DictionaryEntry] = {}
    for entry_id, path, _du in known:
        if entry_id in by_pk:
            entry_of.setdefault(path, by_pk[entry_id])
            entry_of.setdefault(fieldscan.normalise(path), by_pk[entry_id])

    for (path, deliver_unit), info in sorted(observed["sources"].items()):
        key = fieldscan.normalise(path)
        kind = observed["kinds"].get(key, classify.EMPTY)
        entry = entry_of.get(path) or entry_of.get(key) or entries.get(key)
        if entry is None:
            setting, params = classify.default_setting(path, kind)
            entry = DictionaryEntry.objects.create(
                dictionary=dictionary,
                key=key,
                label=path,
                kind=kind,
                setting=setting,
                params=params,
                suggested_setting=setting,
                suggested_params=params,
                quasi=classify.is_quasi_identifying(path, kind),
            )
            entries[key] = entry
            entry_of[path] = entry_of[key] = entry
            created.add(key)
            added["entries"] += 1
            flag(DictionaryFlag.NEW_FIELD, entry, {"paths": [path], "kind": kind})
        elif not entry.column and _more_sensitive(kind, entry.kind):
            # A kind only ever rises. One that does undoes the review: what was allowed for
            # a category is not allowed for a name, so it is dropped until looked at again.
            if entry.reviewed:
                flag(DictionaryFlag.KIND_CHANGED, entry, {"paths": [path], "was": entry.kind, "now": kind})
            entry.kind = kind
            entry.suggested_setting, entry.suggested_params = classify.default_setting(path, kind)
            entry.reviewed_by = entry.reviewed_at = None
            entry.save(update_fields=["kind", "suggested_setting", "suggested_params", "reviewed_by", "reviewed_at"])
        builds = sorted(info["builds"])[-_BUILDS_KEPT:]
        source = known.get((entry.pk, path, deliver_unit))
        if source is None:
            if entry.key not in created and not entry.column and path not in paths_of[entry.pk]:
                flag(DictionaryFlag.NEW_SPELLING, entry, {"paths": [path], "deliver_unit": deliver_unit})
            source = DictionarySource.objects.create(
                entry=entry, path=path, deliver_unit=deliver_unit, builds=builds, visits=info["visits"], last_seen=now
            )
            known[(entry.pk, path, deliver_unit)] = source
            paths_of[entry.pk].add(path)
            added["sources"] += 1
        else:
            source.builds = sorted(set(source.builds or []) | set(builds))[-_BUILDS_KEPT:]
            source.visits = info["visits"]
            source.last_seen = now
            source.save(update_fields=["builds", "visits", "last_seen"])

    # Fields the newest build of a form no longer sends: seen before, absent from it.
    newest: dict[str, int] = {}
    for (_path, du), info in observed["sources"].items():
        if info["builds"]:
            newest[du] = max(newest.get(du, 0), max(info["builds"]))
    in_newest = {(p, du) for (p, du), info in observed["sources"].items() if newest.get(du) in info["builds"]}
    for (entry_id, path, du), source in known.items():
        if du in newest and source.builds and (path, du) not in in_newest and max(source.builds) < newest[du]:
            entry = next((e for e in entries.values() if e.pk == entry_id), None)
            flag(
                DictionaryFlag.GONE,
                entry,
                {"paths": [path], "deliver_unit": du, "last_build": max(source.builds), "newest_build": newest[du]},
            )

    dictionary.scanned_at = now
    dictionary.scanned_visits = observed["visits"]
    dictionary.save(update_fields=["scanned_at", "scanned_visits", "updated_at"])
    DictionaryChange.objects.create(
        dictionary=dictionary, action="scan", after={**added, "visits": observed["visits"]}, user=user
    )
    return {"visits": observed["visits"], **added}


def untagged(dictionary: DataDictionary, sources_seen) -> list[dict]:
    """The ``(path, deliver_unit)`` pairs in ``sources_seen`` that no entry knows: an export
    over them is refused until someone scans and reviews them."""
    known = set(DictionarySource.objects.filter(entry__dictionary=dictionary).values_list("path", "deliver_unit"))
    return [{"path": p, "deliver_unit": du} for p, du in sorted(sources_seen) if (p, du) not in known]


# --- changes (managers only) ----------------------------------------------------------------


def _entry(dictionary: DataDictionary, key: str) -> DictionaryEntry:
    entry = dictionary.entries.filter(key=key).first()
    if entry is None:
        raise DictionaryError("no_such_entry", "There is no such field in this dictionary.")
    return entry


def _state(entry: DictionaryEntry) -> dict:
    return {
        "setting": entry.setting,
        "params": entry.params or {},
        "meaning": entry.meaning,
        "level": entry.level,
        "quasi": entry.quasi,
        "reviewed": entry.reviewed,
    }


@transaction.atomic
def update_entry(opened: spaces.OpenSpace, key: str, change: dict, *, user) -> dict:
    """Change one entry's setting, params, meaning, level or quasi flag (a manager). Saving
    a setting marks the entry reviewed by this person: it is from then on exported as set."""
    spaces.require_manager(opened)
    dictionary = for_space(opened)
    entry = _entry(dictionary, key)
    before = _state(entry)
    if "setting" in change:
        setting = change["setting"]
        try:
            params = transforms.validate_params(setting, change.get("params"))
        except ValueError as e:
            raise DictionaryError("bad_setting", str(e)) from e
        if entry.column == "entity_name" or entry.kind in (classify.IDENTIFIER, classify.MEDIA):
            if setting == transforms.KEEP:
                raise DictionaryError(
                    "identifies",
                    "This field names or pictures someone: it can be dropped, coded or scrubbed, never kept as is.",
                )
        entry.setting, entry.params = setting, params
        entry.reviewed_by, entry.reviewed_at = user, timezone.now()
    if "meaning" in change:
        entry.meaning = str(change["meaning"] or "")[:4000]
    if "level" in change:
        if change["level"] not in (DictionaryEntry.VISIT, DictionaryEntry.CASE):
            raise DictionaryError("bad_level", "A field is about a visit or a case.")
        entry.level = change["level"]
    if "quasi" in change:
        entry.quasi = bool(change["quasi"])
    entry.save()
    DictionaryChange.objects.create(
        dictionary=dictionary, entry_key=key, action="update", before=before, after=_state(entry), user=user
    )
    return entry_dict(entry)


@transaction.atomic
def accept_suggestions(opened: spaces.OpenSpace, keys: list[str], *, user) -> int:
    """Review several entries at once, each at the setting the classifier suggests (a manager)."""
    spaces.require_manager(opened)
    dictionary = for_space(opened)
    n = 0
    for entry in dictionary.entries.filter(key__in=list(keys or [])):
        before = _state(entry)
        entry.setting, entry.params = entry.suggested_setting, dict(entry.suggested_params or {})
        entry.reviewed_by, entry.reviewed_at = user, timezone.now()
        entry.save()
        DictionaryChange.objects.create(
            dictionary=dictionary, entry_key=entry.key, action="accept", before=before, after=_state(entry), user=user
        )
        n += 1
    return n


@transaction.atomic
def merge(opened: spaces.OpenSpace, into: str, other: str, *, user) -> dict:
    """One question asked under two names (a rename between app versions): ``other``'s
    spellings move into ``into``, which takes the stricter of the two settings and goes
    back to unreviewed, since what it holds has changed (a manager)."""
    spaces.require_manager(opened)
    dictionary = for_space(opened)
    if into == other:
        raise DictionaryError("same_entry", "Choose two different fields to merge.")
    keep, gone = _entry(dictionary, into), _entry(dictionary, other)
    if keep.column or gone.column:
        raise DictionaryError("column", "A visits column cannot be merged with a form field.")
    before = {"into": _state(keep), "other": {**_state(gone), "key": gone.key}}
    for source in gone.sources.all():
        if keep.sources.filter(path=source.path, deliver_unit=source.deliver_unit).exists():
            source.delete()
        else:
            source.entry = keep
            source.save(update_fields=["entry"])
    keep.setting = transforms.stricter(keep.setting, gone.setting)
    keep.params = keep.params if keep.setting != gone.setting else gone.params
    if _more_sensitive(gone.kind, keep.kind):
        keep.kind = gone.kind
    keep.quasi = keep.quasi or gone.quasi
    keep.meaning = keep.meaning or gone.meaning
    keep.reviewed_by = keep.reviewed_at = None
    keep.save()
    gone.delete()
    DictionaryChange.objects.create(
        dictionary=dictionary, entry_key=into, action="merge", before=before, after=_state(keep), user=user
    )
    return entry_dict(keep)


def resolve_flag(opened: spaces.OpenSpace, flag_id, *, user) -> None:
    """Mark a flag looked at (a manager)."""
    spaces.require_manager(opened)
    dictionary = for_space(opened)
    flag = dictionary.flags.filter(pk=flag_id, resolved_at__isnull=True).first()
    if flag is None:
        raise DictionaryError("no_such_flag", "There is no such open flag.")
    flag.resolved_by, flag.resolved_at = user, timezone.now()
    flag.save(update_fields=["resolved_by", "resolved_at"])
    DictionaryChange.objects.create(
        dictionary=dictionary,
        entry_key=flag.entry.key if flag.entry else "",
        action="resolve_flag",
        after={"flag": flag.kind, "detail": flag.detail},
        user=user,
    )


def set_registry(opened: spaces.OpenSpace, registry_id, *, user) -> None:
    """Name the semantic registry whose properties inherit from these fields (a manager)."""
    spaces.require_manager(opened)
    dictionary = for_space(opened)
    before = {"registry_id": dictionary.registry_id}
    try:
        dictionary.registry_id = int(registry_id) if registry_id not in (None, "") else None
    except (TypeError, ValueError) as e:
        raise DictionaryError("bad_registry", "A registry is named by its id.") from e
    dictionary.save(update_fields=["registry_id", "updated_at"])
    DictionaryChange.objects.create(
        dictionary=dictionary,
        action="registry",
        before=before,
        after={"registry_id": dictionary.registry_id},
        user=user,
    )
