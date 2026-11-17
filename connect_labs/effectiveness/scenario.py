"""The scenario: the properties of what someone is working on, as one object.

Every page of the cost-effectiveness explorer is a view of the same few choices: which places, which programmes
(models), what the models are assumed to say, and the bar they are held to. They were kept apart, once per page and
under different link keys. A ``Scenario`` is those choices in one place, read from a link and written back to one, so a
change made on one page is the same change on the next and any state is a link someone can send.

* **Places**: an indicator over a threshold in some countries (``indicator=all`` means everywhere with data), at the
  grain of a ``country`` or a ``territory`` (a state or province).
* **Programmes**: one or more models.
* **Target**: how many people to reach. With one, the best places are filled first (``market.plan``), within any
  **limits** (the smallest place worth working in, the most countries).
* **Trust**: how far to believe a place's own figures, from 100% (all of it) down to 0% (a territory takes its
  country's, a country takes the model's): the way to see whether a plan survives noisy data.
* **Dials**: three plain changes to a model, the same on every page: ``extra`` (dollars a person a year the model does
  not count; below zero when delivery is cheaper), ``effect`` (% of the model's effect on deaths) and ``reach`` (the
  most of a place's cases the programme can reach, in %). Link key ``<model>_<dial>``. A model whose practical reach is
  below everyone starts lower (``DIAL_DEFAULTS``).
* **Assumptions**: any of a model's own inputs, changed. Link key ``i.<model>.<input>``. ``place_risk=0`` switches off
  scaling a model's baseline risk to the place (``market.PLACE_RISK``).
* **Bar**: the multiple of GiveWell's benchmark a programme must clear.

Only what differs from the default is written to a link, so a default scenario is the bare page. Pure: no Django, no
database. Whether an indicator or a country exists is the baseline's to say (``people``); this module keeps what it
was given.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from connect_labs.effectiveness import engine, explore

EVERYWHERE = "all"
DEFAULT_MODEL = "chc"
GRAINS = ("country", "territory")
DEFAULT_GRAIN = "country"

#: The three dials, in the order a page shows them. ``extra`` is dollars (and may be negative: delivery cheaper than
#: the model); ``effect`` and ``reach`` are percent.
DIALS: tuple[dict, ...] = (
    {
        "key": "extra",
        "label": "Extra cost per person",
        "unit": "$ a year, below zero if cheaper",
        "default": 0.0,
        "signed": True,
        "hint": "Cost the model does not count, bought outside the programme; below zero when delivery costs less",
    },
    {
        "key": "effect",
        "label": "Effect",
        "unit": "% of the model's",
        "default": 100.0,
        "signed": False,
        "hint": "How much of the model's effect on deaths you believe",
    },
    {
        "key": "reach",
        "label": "Most you can reach",
        "unit": "% of the cases in a place",
        "default": 100.0,
        "signed": False,
        "hint": "The most of a place's cases the programme can reach: nobody reaches all of them",
    },
)
KNOBS = tuple(d["key"] for d in DIALS)
KNOB_DEFAULT = {d["key"]: d["default"] for d in DIALS}
SIGNED = {d["key"] for d in DIALS if d["signed"]}

#: A programme that cannot practically reach everyone starts at the most it can. Kangaroo Care enrols small babies at
#: home through visits: half of a place's small babies at best (owner, 17 Nov 2026).
DIAL_DEFAULTS: dict[str, dict[str, float]] = {"kc": {"reach": 50.0}}

#: Place choices a link may carry besides the ones above: the baseline reads them, a scenario only keeps them.
PLACE_EXTRAS = ("method", "resolution", "year", "target_year", "admin_level", "rollup")


def default_dial(model: str, knob: str) -> float:
    """A dial's starting value for a model."""
    return DIAL_DEFAULTS.get(model, {}).get(knob, KNOB_DEFAULT[knob])


def dial_defaults() -> dict[str, dict[str, float]]:
    """Every model's starting value for every dial, for a page to compare against."""
    return {m: {k: default_dial(m, k) for k in KNOBS} for m in engine.MODELS}


def number(value: Any, default: float | None = None, *, positive: bool = False, signed: bool = False):
    """A finite number from a link value (non-negative unless ``signed``), or ``default``."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v) or (v < 0 and not signed) or (positive and v <= 0):
        return default
    return v


def _s(v: float) -> str:
    """A number as it is written in a link: ``2`` rather than ``2.0``."""
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _words(raw: str) -> list[str]:
    return [w for w in (raw or "").replace(" ", ",").split(",") if w]


@dataclass(frozen=True)
class Scenario:
    """What someone is working on. Dials and assumptions hold only what differs from the default."""

    models: tuple[str, ...] = (DEFAULT_MODEL,)
    indicator: str = EVERYWHERE
    threshold: float | None = None
    iso: tuple[str, ...] = ()
    bar: float | None = None
    target: float | None = None
    min_place: float | None = None
    max_countries: int | None = None
    trust: float = 100.0
    place_risk: bool = True
    grain: str = DEFAULT_GRAIN
    dials: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    overrides: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    place_extras: Mapping[str, str] = field(default_factory=dict)

    # ---- reading

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> Scenario:
        """The scenario a link describes. Anything unrecognised degrades to the default; an assumption that is not an
        input of its model raises ``explore.ExploreError`` with words, as the model explorer does."""
        models: list[str] = []
        for m in _words(params.get("models", "")):
            if m in engine.MODELS and m not in models:
                models.append(m)
        dials: dict[str, dict[str, float]] = {}
        overrides: dict[str, dict[str, float]] = {}
        for m in engine.MODELS:
            changed = {}
            for knob in KNOBS:
                start = default_dial(m, knob)
                v = number(params.get(f"{m}_{knob}"), start, signed=knob in SIGNED)
                if v != start:
                    changed[knob] = v
            if changed:
                dials[m] = changed
            prefix = f"i.{m}."
            raw = {k[len(prefix) :]: v for k, v in params.items() if k.startswith(prefix)}
            if raw:
                overrides[m] = explore.clean_overrides(m, raw)
        grain = (params.get("grain") or DEFAULT_GRAIN).strip()
        most = number(params.get("max_countries"), positive=True)
        trust = number(params.get("trust"), 100.0)
        return cls(
            models=tuple(models) or (DEFAULT_MODEL,),
            indicator=(params.get("indicator") or EVERYWHERE).strip(),
            threshold=number(params.get("threshold")),
            iso=tuple(w.upper() for w in _words(params.get("iso", ""))),
            bar=number(params.get("bar"), positive=True),
            target=number(params.get("target"), positive=True),
            min_place=number(params.get("min_place"), positive=True),
            max_countries=max(1, int(most)) if most else None,
            trust=min(trust, 100.0),
            place_risk=str(params.get("place_risk", "1")).strip().lower() not in ("0", "false", "off", "no"),
            grain=grain if grain in GRAINS else DEFAULT_GRAIN,
            dials=dials,
            overrides=overrides,
            place_extras={k: str(params[k]) for k in PLACE_EXTRAS if params.get(k)},
        )

    # ---- what it says

    @property
    def everywhere(self) -> bool:
        return self.indicator == EVERYWHERE

    def dial(self, model: str, knob: str) -> float:
        return self.dials.get(model, {}).get(knob, default_dial(model, knob))

    def dials_for(self, model: str) -> dict[str, float]:
        """All three dials for a model, the unchanged ones at the model's starting values."""
        return {k: self.dial(model, k) for k in KNOBS}

    def overrides_for(self, model: str) -> dict[str, float]:
        return dict(self.overrides.get(model, {}))

    def resolved_bar(self, default: float) -> float:
        return self.bar if self.bar is not None else default

    def changes(self) -> dict[str, int]:
        """How far the scenario is from the models as published, for the panel's "N changed" line.

        Only the programmes in play count: a dial left on a model that is switched off does not change what is shown.
        Switching off place-scaled risk counts as an assumption, when a programme in play has it."""
        from connect_labs.effectiveness import market  # the one place that says which models scale to the place

        scaled_off = int(not self.place_risk and any(m in market.PLACE_RISK for m in self.models))
        return {
            "dials": sum(len(self.dials.get(m, {})) for m in self.models),
            "assumptions": sum(len(self.overrides.get(m, {})) for m in self.models)
            + scaled_off
            + int(self.trust != 100.0),
        }

    # ---- writing

    def place_params(self) -> dict[str, str]:
        """The place choices as the baseline reads them (``people.question_for``)."""
        out = dict(self.place_extras)
        if self.grain == "territory":
            # a state or province each, never a country folded into one row
            out["rollup"] = "0"
            out.setdefault("admin_level", "1")
        out["indicator"] = self.indicator
        if self.threshold is not None:
            out["threshold"] = _s(self.threshold)
        if self.iso:
            out["iso"] = ",".join(self.iso)
        return out

    def to_params(self) -> dict[str, str]:
        """The link: only what differs from the default, so ``from_params(to_params())`` is this scenario."""
        out = {"models": ",".join(self.models)}
        if not self.everywhere:
            out["indicator"] = self.indicator
            if self.threshold is not None:
                out["threshold"] = _s(self.threshold)
        if self.iso:
            out["iso"] = ",".join(self.iso)
        if self.bar is not None:
            out["bar"] = _s(self.bar)
        if self.target is not None:
            out["target"] = _s(self.target)
        if self.min_place is not None:
            out["min_place"] = _s(self.min_place)
        if self.max_countries is not None:
            out["max_countries"] = str(self.max_countries)
        if self.trust != 100.0:
            out["trust"] = _s(self.trust)
        if not self.place_risk:
            out["place_risk"] = "0"
        if self.grain != DEFAULT_GRAIN:
            out["grain"] = self.grain
        out.update(self.place_extras)
        for m, changed in self.dials.items():
            out.update({f"{m}_{k}": _s(v) for k, v in changed.items()})
        for m, changed in self.overrides.items():
            out.update({f"i.{m}.{k}": _s(v) for k, v in changed.items()})
        return out

    def to_state(self, default_bar: float, default_threshold: float | None = None) -> dict:
        """Every choice written out, for a page to start from: the page changes it and writes the link back."""
        return {
            "models": list(self.models),
            "indicator": self.indicator,
            "threshold": self.threshold if self.threshold is not None else default_threshold,
            "iso": ",".join(self.iso),
            "bar": self.resolved_bar(default_bar),
            "target": self.target,
            "min_place": self.min_place,
            "max_countries": self.max_countries,
            "trust": self.trust,
            "place_risk": self.place_risk,
            "grain": self.grain,
            "dials": {m: self.dials_for(m) for m in engine.MODELS},
            "overrides": {m: dict(v) for m, v in self.overrides.items()},
            "extras": dict(self.place_extras),
        }
