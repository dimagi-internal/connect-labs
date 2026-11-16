"""The scenario: the properties of what someone is working on, as one object.

Every page of the cost-effectiveness explorer is a view of the same few choices: which places, which programmes
(models), what the models are assumed to say, and the bar they are held to. They were kept apart, once per page and
under different link keys. A ``Scenario`` is those choices in one place, read from a link and written back to one, so a
change made on one page is the same change on the next and any state is a link someone can send.

* **Places**: an indicator over a threshold in some countries (``indicator=all`` means everywhere with data).
* **Programmes**: one or more models.
* **Dials**: three plain changes to a model, the same on every page: ``extra`` (dollars a person a year the model does
  not count), ``effect`` (% of the model's effect on deaths) and ``reach`` (% of the people in the places who are
  reached). Link key ``<model>_<dial>``.
* **Assumptions**: any of a model's own inputs, changed. Link key ``i.<model>.<input>``.
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

#: The three dials, in the order a page shows them. ``extra`` is dollars; ``effect`` and ``reach`` are percent.
DIALS: tuple[dict, ...] = (
    {
        "key": "extra",
        "label": "Extra cost per person",
        "unit": "$ a year",
        "default": 0.0,
        "hint": "Cost the model does not count, bought outside the programme",
    },
    {
        "key": "effect",
        "label": "Effect",
        "unit": "% of the model's",
        "default": 100.0,
        "hint": "How much of the model's effect on deaths you believe",
    },
    {
        "key": "reach",
        "label": "People reached",
        "unit": "% of those in the places",
        "default": 100.0,
        "hint": "The share of the people in the places the programme reaches",
    },
)
KNOBS = tuple(d["key"] for d in DIALS)
KNOB_DEFAULT = {d["key"]: d["default"] for d in DIALS}

#: Place choices a link may carry besides the ones above: the baseline reads them, a scenario only keeps them.
PLACE_EXTRAS = ("method", "resolution", "year", "target_year", "admin_level", "rollup")


def number(value: Any, default: float | None = None, *, positive: bool = False) -> float | None:
    """A finite, non-negative number from a link value, or ``default``."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v) or v < 0 or (positive and v == 0):
        return default
    return v


def _s(v: float) -> str:
    """A number as it is written in a link: ``2`` rather than ``2.0``."""
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _words(raw: str) -> list[str]:
    return [w for w in (raw or "").replace(" ", ",").split(",") if w]


@dataclass(frozen=True)
class Scenario:
    """What someone is working on. Dials and assumptions hold only what differs from the model."""

    models: tuple[str, ...] = (DEFAULT_MODEL,)
    indicator: str = EVERYWHERE
    threshold: float | None = None
    iso: tuple[str, ...] = ()
    bar: float | None = None
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
                v = number(params.get(f"{m}_{knob}"), KNOB_DEFAULT[knob])
                if v != KNOB_DEFAULT[knob]:
                    changed[knob] = v
            if changed:
                dials[m] = changed
            prefix = f"i.{m}."
            raw = {k[len(prefix) :]: v for k, v in params.items() if k.startswith(prefix)}
            if raw:
                overrides[m] = explore.clean_overrides(m, raw)
        return cls(
            models=tuple(models) or (DEFAULT_MODEL,),
            indicator=(params.get("indicator") or EVERYWHERE).strip(),
            threshold=number(params.get("threshold")),
            iso=tuple(w.upper() for w in _words(params.get("iso", ""))),
            bar=number(params.get("bar"), positive=True),
            dials=dials,
            overrides=overrides,
            place_extras={k: str(params[k]) for k in PLACE_EXTRAS if params.get(k)},
        )

    # ---- what it says

    @property
    def everywhere(self) -> bool:
        return self.indicator == EVERYWHERE

    def dial(self, model: str, knob: str) -> float:
        return self.dials.get(model, {}).get(knob, KNOB_DEFAULT[knob])

    def dials_for(self, model: str) -> dict[str, float]:
        """All three dials for a model, the unchanged ones at their defaults."""
        return {k: self.dial(model, k) for k in KNOBS}

    def overrides_for(self, model: str) -> dict[str, float]:
        return dict(self.overrides.get(model, {}))

    def resolved_bar(self, default: float) -> float:
        return self.bar if self.bar is not None else default

    def changes(self) -> dict[str, int]:
        """How far the scenario is from the models as published, for the panel's "N changed" line.

        Only the programmes in play count: a dial left on a model that is switched off does not change what is shown.
        """
        return {
            "dials": sum(len(self.dials.get(m, {})) for m in self.models),
            "assumptions": sum(len(self.overrides.get(m, {})) for m in self.models),
        }

    # ---- writing

    def place_params(self) -> dict[str, str]:
        """The place choices as the baseline reads them (``people.question_for``)."""
        out = dict(self.place_extras)
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
            "dials": {m: self.dials_for(m) for m in engine.MODELS},
            "overrides": {m: dict(v) for m, v in self.overrides.items()},
            "extras": dict(self.place_extras),
        }
