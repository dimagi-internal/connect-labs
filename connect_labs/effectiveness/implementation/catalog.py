"""Which model values which delivery, and the models' vendored per-country results."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

#: ``data/cea_models.json`` vendors each model's per-country results (the file names the commit it came from);
#: ``effectiveness_vendor`` writes it from the specs, never by hand.
DATA = Path(__file__).resolve().parents[1] / "data" / "cea_models.json"


# Connect delivery type -> the model that values it. Only programs a model was built for.
SERVICE_MODEL = {"chc": "chc", "mbw": "mbw", "kmc": "kc"}


STATUS_MODELLED = "modelled"


STATUS_NO_MODEL = "no_model"


STATUS_NO_COUNTRY = "no_country_data"


STATUS_NOTHING_DELIVERED = "nothing_delivered"


STATUS_COST_POOLED = "cost_pooled"


STATUS_TEXT = {
    STATUS_MODELLED: "Modelled",
    STATUS_NO_MODEL: "No model",
    STATUS_NO_COUNTRY: "No model for this country",
    STATUS_NOTHING_DELIVERED: "Nothing delivered yet",
    STATUS_COST_POOLED: "Cost counted in its programme's delivery",
}


@lru_cache(maxsize=1)
def models() -> dict:
    with DATA.open(encoding="utf-8") as f:
        return json.load(f)


def iso3_for(country: str) -> str | None:
    """Pulse's alpha-2 country -> the models' alpha-3, or None outside the models' 45 countries."""
    return (models()["countries"].get((country or "").upper()) or {}).get("iso3")
