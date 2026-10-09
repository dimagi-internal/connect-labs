"""Coaching about ONE case: what a case's visits say, in one of four stories.

A worker coaching conversation is about their indicators (``coach_briefing.py``). A
CASE conversation is about one beneficiary -- in KMC, one baby -- and starts from the
case's own visits. Labs reads the visits (``case_visits.py``), works out the case's
STORY here, and writes the coach a briefing in a fixed shape (``render_case_briefing``).
Owner request 2026-10-09 (ACE spec ``docs/superpowers/specs/2026-10-09-case-coaching-design.md``).

The four stories (``STORIES``). A case whose weights hold anything to check is a
WEIGHT_CHECK and nothing else -- on such a series the noise is the story. Otherwise
the first of DANGER_SIGN, FALTERING, THRIVING that holds is the case's:

=====================  =======================================================  =====================================
key                    when Labs says so                                        label (what the coach is told)
=====================  =======================================================  =====================================
``CASE_DANGER_SIGN``   a visit records a danger sign and the baby was not       Danger sign recorded, no referral
                       referred (``referred`` answered no)
``CASE_WEIGHT_CHECK``  an impossible step between two weighings (the KMC      A weighing that is hard to believe
                       registry's: outside -20..45 g/kg/day of their mean
                       weight, 1-90 days apart), or the same weight on three
                       weighings in a row, or a weight outside 800-5,000 g
``CASE_FALTERING``     gain below 5 g/kg/day over the last two intervals, and   Weight has stalled and skin-to-skin
                       skin-to-skin hours lower at the latest visit that         time is falling
                       recorded them than at the one before
``CASE_THRIVING``      every interval of at least ``MIN_JUDGED_DAYS`` days at   Baby is growing well
                       15 g/kg/day or more, and the latest weight at least
                       20% above the first
=====================  =======================================================  =====================================

Growth velocity is grams gained per kilogram of the EARLIER weight per day -- the
measure WHO uses for small babies, where 15-20 g/kg/day is healthy growth. An
interval of fewer than ``MIN_JUDGED_DAYS`` days is too short to judge growth from (a
registration weighing and a visit the next day, scale noise of a few grams), so the
thriving rule reads only the longer ones; the implausibility rules read EVERY interval
of a day or more, because a 495 g jump in one day is exactly what they exist to catch.

Everything here is a pure function of the visit rows: the classifier, the facts it
states, the briefing text and the danger-sign wording are tested on their own.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

from connect_labs.workflow import coach_briefing

# ---------------------------------------------------------------------------
# The stories
# ---------------------------------------------------------------------------

DANGER = "CASE_DANGER_SIGN"
WEIGHT_CHECK = "CASE_WEIGHT_CHECK"
FALTERING = "CASE_FALTERING"
THRIVING = "CASE_THRIVING"

#: Precedence: a case's story is the first of these that holds.
STORIES = (DANGER, WEIGHT_CHECK, FALTERING, THRIVING)

#: What the coach is told each story is (the briefing's ``Topic:`` line). A contract
#: with the coach bot's Case cards (ACE ``lib/coach-briefing.ts``).
LABELS = {
    THRIVING: "Baby is growing well",
    WEIGHT_CHECK: "A weighing that is hard to believe",
    FALTERING: "Weight has stalled and skin-to-skin time is falling",
    DANGER: "Danger sign recorded, no referral",
}

#: Healthy growth for a small baby, g/kg/day (WHO).
HEALTHY_LOW, HEALTHY_HIGH = 15, 20
HEALTHY_TEXT = f"healthy growth is {HEALTHY_LOW}–{HEALTHY_HIGH} g/kg/day"

THRIVING_MIN_RATE = 15.0
THRIVING_MIN_RISE = 0.20
#: The shortest interval the thriving rule judges growth over.
MIN_JUDGED_DAYS = 3
#: An IMPOSSIBLE weight step is the programme's own (KMC registry, measure
#: ``pct_impossible_weight_changes``, ``semantic/registry/kmc/properties.yml``): a change
#: between consecutive weighings 1-90 days apart, per kg of their MEAN weight per day,
#: outside -20..45 g/kg/day. ``rules_from_constants`` reads the bound registry's own
#: constants when the caller has them; these mirror them otherwise. (The indicator counts
#: steps in a baby's first 21 days; a CASE is checked over its whole series -- a later
#: impossible step is as much a weighing to check, and must not pass as "thriving".)
IMPOSSIBLE_LO = -20.0
IMPOSSIBLE_HI = 45.0
IMPOSSIBLE_GAP_MIN = 1
IMPOSSIBLE_GAP_MAX = 90


@dataclass(frozen=True)
class Rules:
    impossible_lo: float = IMPOSSIBLE_LO
    impossible_hi: float = IMPOSSIBLE_HI
    gap_min: int = IMPOSSIBLE_GAP_MIN
    gap_max: int = IMPOSSIBLE_GAP_MAX


def rules_from_constants(constants: dict | None) -> Rules:
    """The weight-check rules from a registry's ``constants`` (``IMPOSSIBLE_LO``,
    ``IMPOSSIBLE_HI``, ``IMPOSSIBLE_GAP_MIN``, ``IMPOSSIBLE_GAP_MAX``); a missing or
    unreadable one keeps the mirrored default."""
    c = constants or {}

    def num(key, default, cast=float):
        try:
            return cast(c[key])
        except (KeyError, TypeError, ValueError):
            return default

    return Rules(
        impossible_lo=num("IMPOSSIBLE_LO", IMPOSSIBLE_LO),
        impossible_hi=num("IMPOSSIBLE_HI", IMPOSSIBLE_HI),
        gap_min=num("IMPOSSIBLE_GAP_MIN", IMPOSSIBLE_GAP_MIN, int),
        gap_max=num("IMPOSSIBLE_GAP_MAX", IMPOSSIBLE_GAP_MAX, int),
    )


SAME_WEIGHT_RUN = 3
#: A weight outside this range (grams) is not a KMC baby's weight: a weighing to check.
PLAUSIBLE_MIN_G, PLAUSIBLE_MAX_G = 800, 5000
FALTERING_RATE = 5.0

#: The finder's default recency window: a story's evidence must fall this many days
#: before the latest visit in its opportunity's data, or later.
DEFAULT_WINDOW_DAYS = 30

# ---------------------------------------------------------------------------
# Danger signs: Labs' own words for each, grounded in WHO IMCI's signs of possible
# serious illness in a young infant. Never a diagnosis: what was recorded, and why a
# baby with it should be seen at a health facility.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sign:
    key: str
    name: str  # lower-case, for running text ("pus in the eyes, skin or belly button")
    title: str  # for the card ("Pus in the eyes, skin or belly button")
    why: str  # one plain line: why it needs a health facility


#: Most serious first: the order a card lists them in.
SIGNS: tuple[Sign, ...] = (
    Sign(
        "convulsions",
        "convulsions (fits)",
        "Convulsions (fits)",
        "A baby who has had a fit needs a health facility now.",
    ),
    Sign(
        "chest_indrawing",
        "severe chest indrawing",
        "Chest pulling in with each breath",
        "A sign of possible serious illness, such as a chest infection.",
    ),
    Sign(
        "lethargic",
        "floppy or unusually sleepy",
        "Floppy or hard to wake",
        "A baby who moves only when touched, or not at all, may be seriously ill.",
    ),
    Sign("low_oxygen", "low oxygen", "Low oxygen", "The baby may not be getting enough oxygen."),
    Sign("bluish", "blue lips or face", "Blue lips or face", "The baby may not be getting enough oxygen."),
    Sign("fever", "fever", "Fever", "In a young baby, fever can be a sign of serious infection."),
    Sign(
        "hypothermia",
        "low body temperature",
        "Cold body",
        "A small baby who is too cold can become seriously ill.",
    ),
    Sign("fast_breathing", "fast breathing", "Fast breathing", "Breathing fast can be a sign of a chest infection."),
    Sign(
        "slow_breathing",
        "slow breathing",
        "Slow breathing",
        "Breathing too slowly is a sign of possible serious illness.",
    ),
    Sign(
        "noisy_breathing",
        "noisy breathing",
        "Noisy breathing",
        "Grunting or noisy breathing can mean the baby is struggling.",
    ),
    Sign(
        "low_heart_rate",
        "slow heart rate",
        "Slow heart rate",
        "A slow heartbeat in a young baby needs checking at a facility.",
    ),
    Sign(
        "poor_feeding", "not feeding well", "Not feeding well", "A baby who stops feeding well may be seriously ill."
    ),
    Sign(
        "pus",
        "pus in the eyes, skin or belly button",
        "Pus in eyes, skin or belly button",
        "Pus is a sign of infection, which can spread quickly in a small baby.",
    ),
    Sign(
        "jaundice",
        "yellow skin or eyes",
        "Yellow skin or eyes",
        "Strong yellowing in a young baby needs checking at a facility.",
    ),
)
SIGN_BY_KEY = {s.key: s for s in SIGNS}
_SIGN_ORDER = {s.key: i for i, s in enumerate(SIGNS)}

#: What the card says to do, for every danger sign.
DANGER_ACTIONS = (
    "Visit the family as soon as you can",
    "Check the baby for the sign again",
    "If the sign is still there, refer the baby to a health facility",
)
DANGER_WHY = "Danger signs in a young baby can mean serious illness. A health facility should see the baby."

#: The weighing checklist the WEIGHT_CHECK card shows (and the coach walks through).
WEIGHING_CHECKLIST = (
    "Set the scale to zero",
    "Weigh baby without clothes",
    "Read the number when baby is still",
    "Write it down straight away",
)


# ---------------------------------------------------------------------------
# Where a programme's visits keep each fact
# ---------------------------------------------------------------------------

#: KMC's paths. A workflow carries its own as ``config.case_coaching`` (the KMC
#: templates declare these, so their instances inherit them on read); a workflow with
#: none has no case coaching.
KMC_CASE_COACHING: dict[str, Any] = {
    "programme": "Kangaroo Mother Care",
    "entity_noun": "baby",
    # Which visits count: all but rejected ones (a pending visit's danger sign is news).
    "exclude_statuses": ["rejected"],
    "registration_forms": ["Child Registration Form", "Register KMC Beneficiary"],
    # The baby's case id, by form design (kmc_programme_metrics.BABY_CASE_ID_FIELD).
    "case_id_paths": ["form.kmc_beneficiary_case_id", "form.child_case_id", "form.case.@case_id"],
    "case_id_paths_by_form": {"Child Registration Form": ["form.subcase_0.case.@case_id"]},
    # The weight a visit recorded, in grams (kilograms if below 20).
    "weight_paths": ["form.anthropometric.child_weight_visit"],
    # Read only on a registration form: follow-ups carry the case's copy of it.
    "registration_weight_paths": [
        "form.child_details.birth_weight_reg.child_weight_reg",
        "form.subcase_0.case.update.child_weight_reg",
    ],
    "birth_weight_paths": ["form.child_weight_birth", "form.child_details.birth_weight_group.child_weight_birth"],
    "skin_to_skin_paths": ["form.kmc_24-hour_recall.total_kmc_hours", "form.KMC_24-Hour_Recall.total_kmc_hours"],
    # The visit's danger-sign checklist: the first of these groups the form has.
    "danger_groups": ["form.danger_signs_checklist", "form.child_details.Danger_Signs_Checklist"],
    # Inside the group: each sign's yes/no question (first path present) ...
    "danger_questions": {
        "pus": ["pus_grp.pus_in_eyes_skin_or_on_belly_button", "pus_in_eyes_skin_or_on_belly_button"],
        "convulsions": ["conv_grp.Convulsions_or_seizures", "Convulsions_or_seizures", "convulsions_or_seizures"],
        "jaundice": ["jaundice_grp.jaundice", "jaundice"],
        "lethargic": ["lethargic_grp.floppy_or_lethargic", "floppy_or_lethargic"],
        "poor_feeding": ["poor_feed_grp.poor_feeding_not_eating", "poor_feeding_not_eating"],
        "chest_indrawing": ["chest_indraw_grp.Severe_chest_indrawing", "Severe_chest_indrawing"],
        "bluish": ["blue_lips_face_grp.Bluish_face_or_lips", "Bluish_face_or_lips"],
        "noisy_breathing": ["noisy_breathing_grp.Noisy_breathing", "Noisy_breathing"],
    },
    # ... and the warning labels the form shows when a reading crosses a threshold
    # (present = shown). A label whose own question was answered "no" is ignored.
    "danger_labels": {
        "fever": "danger_sign_label.fever",
        "hypothermia": "danger_sign_label.hypothermia",
        "low_oxygen": "danger_sign_label.hypoxia",
        "fast_breathing": "danger_sign_label.high_breath_count",
        "slow_breathing": "danger_sign_label.low_breath_count",
        "low_heart_rate": "danger_sign_label.low_heart_rate",
        "jaundice": "danger_sign_label.jaundice",
        "poor_feeding": "danger_sign_label.poor_feeding__not_eating",
        "pus": "danger_sign_label.pus_in_eyes_skin_or_on_belly_button",
        "convulsions": "danger_sign_label.convulsionsseizures",
    },
    "referred_path": "child_referred",
}


def config_of(definition_config: dict | None) -> dict | None:
    """A workflow's case-coaching config (``config.case_coaching``), or None when it has
    none -- such a workflow offers no case coaching."""
    raw = (definition_config or {}).get("case_coaching")
    return raw if isinstance(raw, dict) and raw else None


# ---------------------------------------------------------------------------
# A case, from its visit rows
# ---------------------------------------------------------------------------


@dataclass
class Visit:
    date: dt.date
    visit_id: str = ""
    form: str = ""
    registration: bool = False
    weight_g: float | None = None
    skin_to_skin_h: float | None = None
    #: Danger-sign keys recorded at this visit, most serious first.
    signs: tuple[str, ...] = ()
    #: Whether the visit carried a danger-sign checklist at all.
    checklist: bool = False
    #: True / False when asked, None when the form did not ask.
    referred: bool | None = None


@dataclass
class Case:
    opportunity_id: int
    case_id: str
    name: str
    username: str
    visits: list[Visit] = field(default_factory=list)
    birth_weight_g: float | None = None
    #: The weight-check rules this case is judged by (the bound registry's, if known).
    rules: Rules = field(default_factory=Rules)

    @property
    def registered(self) -> dt.date | None:
        reg = [v.date for v in self.visits if v.registration]
        return min(reg) if reg else (self.visits[0].date if self.visits else None)

    @property
    def key(self) -> str:
        return f"{self.opportunity_id}::{self.case_id}"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def grams(value: Any) -> float | None:
    """A weight in grams: a value under 20 is read as kilograms (a KMC baby weighs
    0.5-5 kg; a form records one or the other)."""
    v = _num(value)
    if v is None or v <= 0:
        return None
    return v * 1000 if v < 20 else v


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _yes(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("yes", "true", "1"):
        return True
    if text in ("no", "false", "0"):
        return False
    return None


def visit_from_row(row: dict, config: dict) -> Visit | None:
    """One visit from a loader row (``case_visits.py``): the extracted values under
    their config names. None when the row has no date."""
    date = _date(row.get("visit_date"))
    if date is None:
        return None
    form = str(row.get("form_name") or "")
    # A registration form by name, or any form whose name says so: only there is the
    # registration weight THIS visit's weight (follow-ups carry the case's copy of it).
    registration = form in (config.get("registration_forms") or []) or "registration" in form.lower()
    weight = grams(row.get("weight"))
    if weight is None and registration:
        weight = grams(row.get("registration_weight"))
    hours = _num(row.get("skin_to_skin"))
    answers = row.get("danger_questions") or {}
    labels = row.get("danger_labels") or {}
    signs = {k for k, v in answers.items() if _yes(v) is True}
    # A label the form showed counts, unless its own question was answered "no".
    signs |= {k for k, v in labels.items() if v not in (None, "") and _yes(answers.get(k)) is not False}
    signs = {s for s in signs if s in SIGN_BY_KEY}
    return Visit(
        date=date,
        visit_id=str(row.get("visit_id") or ""),
        form=form,
        registration=registration,
        weight_g=weight,
        skin_to_skin_h=hours if hours is not None and hours >= 0 else None,
        signs=tuple(sorted(signs, key=_SIGN_ORDER.__getitem__)),
        checklist=bool(row.get("has_checklist")),
        referred=_yes(row.get("referred")),
    )


def cases_from_rows(rows: list[dict], config: dict) -> list[Case]:
    """Every case in ``rows``, its visits oldest first. A case is (opportunity, case id);
    its name and worker are its latest visit's."""
    by_key: dict[tuple[int, str], Case] = {}
    rules = rules_from_constants(config.get("constants"))
    for row in rows:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            continue
        visit = visit_from_row(row, config)
        if visit is None:
            continue
        key = (int(row.get("opportunity_id") or 0), case_id)
        case = by_key.get(key)
        if case is None:
            case = by_key[key] = Case(opportunity_id=key[0], case_id=case_id, name="", username="", rules=rules)
        case.visits.append(visit)
        bw = grams(row.get("birth_weight"))
        if bw is not None and case.birth_weight_g is None:
            case.birth_weight_g = bw
        # The latest visit names the case and its worker.
        if not case.visits[:-1] or visit.date >= max(v.date for v in case.visits[:-1]):
            case.name = str(row.get("entity_name") or case.name or case_id)
            case.username = str(row.get("username") or case.username)
    for case in by_key.values():
        # Registration first on a shared day: it is where the series starts.
        case.visits.sort(key=lambda v: (v.date, not v.registration, v.visit_id))
    return list(by_key.values())


# ---------------------------------------------------------------------------
# Words and numbers, as the briefing writes them
# ---------------------------------------------------------------------------


def day(d: dt.date) -> str:
    """``17 May 2026``."""
    return f"{d.day} {d.strftime('%b')} {d.year}"


def day_short(d: dt.date) -> str:
    """``17 May``."""
    return f"{d.day} {d.strftime('%b')}"


def between(a: dt.date, b: dt.date) -> str:
    """``17 and 18 May``; ``25 May and 1 Jun``; across years in full."""
    if a.year != b.year:
        return f"{day(a)} and {day(b)}"
    if a.month == b.month:
        return f"{a.day} and {day_short(b)}"
    return f"{day_short(a)} and {day_short(b)}"


def g(value: float) -> str:
    """``1,350``: whole grams with a thousands comma."""
    return f"{int(round(value)):,}"


def hours(value: float) -> str:
    return str(int(round(value))) if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def days_text(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def join_words(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Weighing:
    date: dt.date
    grams: float
    index: int  # position in the case's visits


@dataclass(frozen=True)
class Interval:
    a: Weighing
    b: Weighing

    @property
    def days(self) -> int:
        return (self.b.date - self.a.date).days

    @property
    def gain(self) -> float:
        return self.b.grams - self.a.grams

    @property
    def rate(self) -> float | None:
        """g/kg/day over the earlier weight; None for a same-day pair."""
        return self.gain / (self.a.grams / 1000) / self.days if self.days >= 1 else None

    @property
    def step_rate(self) -> float | None:
        """g/kg/day over the pair's MEAN weight -- the registry's impossible-step measure."""
        mean = (self.a.grams + self.b.grams) / 2
        return self.gain / (mean / 1000) / self.days if self.days >= 1 and mean > 0 else None


@dataclass
class Story:
    key: str
    #: The facts behind it, as one or two sentences (the briefing's "What the data shows").
    facts: str
    #: The date the story's evidence points to: what the finder's recency reads.
    evidence_date: dt.date
    #: What a picture of it highlights (story-specific; see ``coach_charts.types``).
    detail: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return LABELS[self.key]


def weighings(case: Case) -> list[Weighing]:
    return [Weighing(v.date, v.weight_g, i) for i, v in enumerate(case.visits) if v.weight_g is not None]


def _danger(case: Case) -> Story | None:
    flagged = [v for v in case.visits if v.signs and v.referred is False]
    if not flagged:
        return None
    v = flagged[-1]
    names = [SIGN_BY_KEY[s].name for s in v.signs]
    facts = f"On {day(v.date)} the visit recorded {join_words(names)}, and the baby was not referred."
    earlier = len(flagged) - 1
    if earlier:
        facts += (
            f" {earlier} earlier visit{'s' if earlier != 1 else ''} also recorded a danger sign without a referral."
        )
    return Story(
        DANGER,
        facts,
        v.date,
        {
            "date": v.date.isoformat(),
            "signs": list(v.signs),
            "visit_index": case.visits.index(v),
            "flagged": len(flagged),
        },
    )


def _weight_check(ws: list[Weighing], rules: Rules = Rules()) -> Story | None:
    found: list[Story] = []
    for w in ws:
        if not PLAUSIBLE_MIN_G <= w.grams <= PLAUSIBLE_MAX_G:
            found.append(
                Story(
                    WEIGHT_CHECK,
                    f"Weight was recorded as {g(w.grams)} g on {day(w.date)}; a baby in KMC weighs "
                    f"{g(PLAUSIBLE_MIN_G)}–{g(PLAUSIBLE_MAX_G)} g.",
                    w.date,
                    {"kind": "range", "from": w.index, "to": w.index, "label": f"{g(w.grams)} g?", "severity": 1000.0},
                )
            )
    for a, b in zip(ws, ws[1:]):
        iv = Interval(a, b)
        rate = iv.step_rate
        if rate is None or not rules.gap_min <= iv.days <= rules.gap_max:
            continue
        if rate > rules.impossible_hi:
            found.append(
                Story(
                    WEIGHT_CHECK,
                    f"Weight rose {g(iv.gain)} g in {days_text(iv.days)} between {between(a.date, b.date)}, "
                    f"about {int(round(rate))} g/kg/day; {HEALTHY_TEXT}, and the programme counts more than "
                    f"{int(rules.impossible_hi)} as impossible.",
                    b.date,
                    {
                        "kind": "jump",
                        "from": a.index,
                        "to": b.index,
                        "label": f"+{g(iv.gain)} g in {days_text(iv.days)}?",
                        "severity": rate / rules.impossible_hi,
                    },
                )
            )
        elif rate < rules.impossible_lo:
            found.append(
                Story(
                    WEIGHT_CHECK,
                    f"Weight fell {g(-iv.gain)} g in {days_text(iv.days)} between {between(a.date, b.date)}, "
                    f"about {int(round(-rate))} g/kg/day lost; the programme counts a loss faster than "
                    f"{int(-rules.impossible_lo)} g/kg/day as impossible.",
                    b.date,
                    {
                        "kind": "drop",
                        "from": a.index,
                        "to": b.index,
                        "label": f"−{g(-iv.gain)} g in {days_text(iv.days)}?",
                        "severity": rate / rules.impossible_lo,
                    },
                )
            )
    run_start = 0
    for i in range(1, len(ws) + 1):
        if i < len(ws) and abs(ws[i].grams - ws[run_start].grams) < 0.5:
            continue
        if i - run_start >= SAME_WEIGHT_RUN:
            first, last = ws[run_start], ws[i - 1]
            found.append(
                Story(
                    WEIGHT_CHECK,
                    f"Weight was recorded as exactly {g(first.grams)} g on {i - run_start} weighings in a row, "
                    f"from {day(first.date)} to {day(last.date)}.",
                    last.date,
                    {
                        "kind": "same",
                        "from": first.index,
                        "to": last.index,
                        "label": f"{g(first.grams)} g {i - run_start} times?",
                        "severity": 1.0,
                    },
                )
            )
        run_start = i
    # The most striking finding is the one to talk about (a 495 g jump in a day over a
    # 44 g/kg/day week); on a tie, the most recent.
    return max(found, key=lambda s: (s.detail["severity"], s.evidence_date)) if found else None


def _faltering(case: Case, ws: list[Weighing]) -> Story | None:
    if len(ws) < 3:
        return None
    a, b = ws[-3], ws[-1]
    days = (b.date - a.date).days
    if days < 1:
        return None
    rate = (b.grams - a.grams) / (a.grams / 1000) / days
    if rate >= FALTERING_RATE:
        return None
    recorded = [v for v in case.visits if v.skin_to_skin_h is not None]
    if len(recorded) < 2 or not recorded[-1].skin_to_skin_h < recorded[-2].skin_to_skin_h:
        return None
    prev, last = recorded[-2], recorded[-1]
    facts = (
        f"Weight went from {g(a.grams)} g on {day_short(a.date)} to {g(b.grams)} g on {day_short(b.date)}, "
        f"about {int(round(rate))} g/kg/day; {HEALTHY_TEXT}. Skin-to-skin fell from {hours(prev.skin_to_skin_h)} h "
        f"on {day_short(prev.date)} to {hours(last.skin_to_skin_h)} h on {day_short(last.date)}."
    )
    return Story(FALTERING, facts, max(b.date, last.date), {"from": a.index, "to": b.index, "rate": round(rate, 1)})


def _thriving(ws: list[Weighing]) -> Story | None:
    if len(ws) < 2:
        return None
    first, last = ws[0], ws[-1]
    if last.grams < first.grams * (1 + THRIVING_MIN_RISE):
        return None
    judged = [Interval(a, b) for a, b in zip(ws, ws[1:]) if (b.date - a.date).days >= MIN_JUDGED_DAYS]
    if not judged or any(iv.rate < THRIVING_MIN_RATE for iv in judged):
        return None
    # The average of the judged intervals' own rates, weighted by their length: the
    # same measure the rule reads, so it can never state less than the 15 g/kg/day it
    # requires. (Over the FIRST weight the linear average overstates it as the baby
    # grows: 1,155 -> 2,560 g in 30 days reads 41.)
    rate = sum(iv.rate * iv.days for iv in judged) / sum(iv.days for iv in judged)
    rise = int(round(100 * (last.grams - first.grams) / first.grams))
    facts = (
        f"Weight rose from {g(first.grams)} g on {day_short(first.date)} to {g(last.grams)} g on "
        f"{day_short(last.date)} (+{rise}%), about {int(round(rate))} g/kg/day on average; {HEALTHY_TEXT}."
    )
    return Story(THRIVING, facts, last.date, {"rise_pct": rise, "rate": round(rate, 1)})


def classify(case: Case) -> list[Story]:
    """The stories the case's visits support, most urgent first; the first is THE case's
    story. Empty when none holds.

    A weight series with anything to check is ONLY a weight check: the other stories
    read the weights (or, for a danger sign, are told beside them), so on a series with
    a weighing that is hard to believe the noise is the story -- a "faltering" baby at
    3,213 -> 2,881 -> 3,300 g, or a "danger sign" baby recorded at 250 g, is first a
    weighing to check (synthetic KMC cohort, 2026-10-09)."""
    ws = weighings(case)
    check = _weight_check(ws, case.rules)
    if check is not None:
        return [check]
    found = [_danger(case), _faltering(case, ws), _thriving(ws)]
    return [s for s in found if s is not None]


def story_of(case: Case, wanted: str | None = None) -> Story | None:
    """The case's story -- or ``wanted``, if the visits support it; None otherwise."""
    stories = classify(case)
    if wanted:
        return next((s for s in stories if s.key == wanted), None)
    return stories[0] if stories else None


# ---------------------------------------------------------------------------
# The briefing
# ---------------------------------------------------------------------------

NOT_RECORDED = "not recorded"


def about_line(case: Case) -> str:
    """One line on the case: birth weight, date registered, number of visits, the last."""
    bw = f"birth weight {g(case.birth_weight_g)} g" if case.birth_weight_g else f"birth weight {NOT_RECORDED}"
    reg = case.registered
    registered = f"registered {day(reg)}" if reg else f"registered {NOT_RECORDED}"
    n = len(case.visits)
    last = f", the last on {day(case.visits[-1].date)}" if case.visits else ""
    return f"{bw[0].upper()}{bw[1:]}; {registered}; {n} visit{'s' if n != 1 else ''}{last}."


def visit_line(v: Visit) -> str:
    weight = f"weight {g(v.weight_g)} g" if v.weight_g is not None else f"weight {NOT_RECORDED}"
    s2s = (
        f"skin-to-skin {hours(v.skin_to_skin_h)} h in the last 24 h"
        if v.skin_to_skin_h is not None
        else f"skin-to-skin {NOT_RECORDED}"
    )
    if v.signs:
        signs = ", ".join(SIGN_BY_KEY[s].name for s in v.signs)
    else:
        signs = "none" if v.checklist else NOT_RECORDED
    referred = {True: "yes", False: "no", None: "not asked"}[v.referred]
    return f"- {day(v.date)}: {weight}; {s2s}; danger signs: {signs}; referred: {referred}"


def earlier_line(earlier: dict | None) -> str | None:
    """``Earlier coaching on this case: 2 Oct 2026 — <label>; agreed: <step | none>``, from
    what the CALLER worked out (an agent reading earlier runs); Labs looks nothing up.
    None when there is no earlier conversation to mention."""
    if not isinstance(earlier, dict) or not earlier.get("date"):
        return None
    when = _date(earlier.get("date"))
    label = str(earlier.get("label") or "").strip()
    label = LABELS.get(label, label) or "a coaching conversation"
    agreed = str(earlier.get("agreed") or "").strip() or "none"
    return f"Earlier coaching on this case: {day(when) if when else earlier['date']} — {label}; agreed: {agreed}"


def render_case_briefing(*, programme: str, worker: str, case: Case, story: Story, earlier: dict | None = None) -> str:
    """The case briefing, in the shape the coach bot parses (ACE ``renderCaseBriefing``).
    ``earlier`` adds the follow-up line (``earlier_line``)."""
    follow_up = earlier_line(earlier)
    lines = [
        coach_briefing.HEADER,
        f"Programme: {programme}",
        f"Worker: {worker}",
        f"Case: {case.name or case.case_id}",
        f"About this case: {about_line(case)}",
        f"Topic: {story.label} [{story.key}]",
        f"What the data shows: {story.facts}",
        *([follow_up] if follow_up else []),
        "Visits, oldest first:",
        *(visit_line(v) for v in case.visits),
        coach_briefing.FOOTER,
    ]
    return "\n".join(lines)


def is_case_briefing(text: str | None) -> bool:
    return coach_briefing.is_briefing(text) and "\nCase: " in (text or "") and "\nTopic: " in (text or "")


def case_briefing_summary(text: str) -> dict:
    """A case briefing as the person confirming it reads it: the case, the story and the facts."""
    out: dict[str, Any] = {}
    for line in (text or "").splitlines():
        for prefix, key in (("Case: ", "case"), ("Topic: ", "topic"), ("What the data shows: ", "facts")):
            if line.startswith(prefix):
                out[key] = line[len(prefix) :].strip()
    topic = out.get("topic") or ""
    if topic.endswith("]") and "[" in topic:
        out["topic"], out["story"] = topic[: topic.rindex("[")].strip(), topic[topic.rindex("[") + 1 : -1]
    return out


def story_from_briefing(text: str) -> str | None:
    return case_briefing_summary(text).get("story")


# ---------------------------------------------------------------------------
# The finder: which cases in a scope have a story worth coaching on, recent first
# ---------------------------------------------------------------------------


def find(
    cases: list[Case],
    *,
    stories: list[str] | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    latest_by_opp: dict[int, dt.date] | None = None,
) -> list[dict]:
    """Every (case, story) whose evidence falls within ``window_days`` of the latest
    visit in its opportunity's data, as plain dicts, most urgent story first, then most
    recent. ``latest_by_opp`` defaults to the latest visit among ``cases`` per
    opportunity. A case appears at most once, with its own story."""
    wanted = set(stories or STORIES)
    if latest_by_opp is None:
        latest_by_opp = {}
        for c in cases:
            if c.visits:
                d = c.visits[-1].date
                latest_by_opp[c.opportunity_id] = max(d, latest_by_opp.get(c.opportunity_id, d))
    out = []
    for c in cases:
        latest = latest_by_opp.get(c.opportunity_id)
        if latest is None:
            continue
        since = latest - dt.timedelta(days=window_days)
        # One story per case: its own (the first that holds).
        for n, s in enumerate(classify(c)[:1]):
            if s.key not in wanted or s.evidence_date < since:
                continue
            out.append(
                {
                    "opportunity_id": c.opportunity_id,
                    "case_id": c.case_id,
                    "case_name": c.name or c.case_id,
                    "username": c.username,
                    "story": s.key,
                    "label": s.label,
                    "primary": n == 0,
                    "facts": s.facts,
                    "evidence_date": s.evidence_date.isoformat(),
                    "days_before_latest": (latest - s.evidence_date).days,
                    "visits": len(c.visits),
                }
            )
    out.sort(key=lambda r: (STORIES.index(r["story"]), r["days_before_latest"], not r["primary"], r["case_name"]))
    return out
