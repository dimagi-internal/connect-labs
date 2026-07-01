"""Card provider contract for the pages app."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CardPayload:
    """Uniform payload every provider returns; consumed by the client renderers."""

    title: str
    card_type: str
    status: str | None = None
    metrics: list[dict] = field(default_factory=list)
    body: str | None = None
    cta: dict | None = None
    render_code: str | None = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CardProvider:
    """Base class. Subclasses set key/label/target_kind and implement get_card_data."""

    key: str = ""
    label: str = ""
    target_kind: str = ""  # e.g. "opportunity", "program", "workflow"

    def entitled(self, request, target: dict) -> bool:
        """Return True if the viewer may see a card for this target. Default: allow."""
        return True

    def get_card_data(self, request, target: dict, options: dict) -> CardPayload:
        raise NotImplementedError
