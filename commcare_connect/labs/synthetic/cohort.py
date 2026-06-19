"""Cohort spec: a declarative YAML describing a synthetic clone cohort.

One file drives both phases — you hand the same spec to ``profile_cohort`` (Phase 1,
safe mode) and ``generate_cohort`` (Phase 2). It names the source opportunities, the
shared labs-only program, and where the bundles live::

    program_id: 10010            # optional — auto-allocated + written back if omitted
    program_name: "KMC (Synthetic)"
    org_name: "Dimagi-KMC (Synthetic)"
    bundle_root: "gdrive:"       # Phase 1 records the resolved gdrive:<folder_id> here
    opportunity_ids: [523, 524, 675, 874, 938, 1234, 1236, 1487, 1488, 1739, 1790]

Phase 1 resolves ``bundle_root`` (creating a Drive run folder when it is bare
``gdrive:``) and writes the concrete value back, so Phase 2 reads from exactly where
Phase 1 wrote. Phase 2 likewise records the allocated ``program_id`` back into the spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class CohortSpec:
    opportunity_ids: list[int]
    program_name: str = "Synthetic"
    org_name: str = "Synthetic"
    program_id: int | None = None
    bundle_root: str = "gdrive:"

    @classmethod
    def from_dict(cls, data: dict) -> CohortSpec:
        data = data or {}
        ids = data.get("opportunity_ids")
        if not ids:
            raise ValueError("cohort spec needs a non-empty 'opportunity_ids' list")
        return cls(
            opportunity_ids=[int(x) for x in ids],
            program_name=str(data.get("program_name", "Synthetic")),
            org_name=str(data.get("org_name", "Synthetic")),
            program_id=int(data["program_id"]) if data.get("program_id") is not None else None,
            bundle_root=str(data.get("bundle_root", "gdrive:")),
        )

    @classmethod
    def from_yaml(cls, text: str) -> CohortSpec:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"cohort spec YAML parse error: {exc}") from exc
        return cls.from_dict(data)

    def to_yaml(self) -> str:
        # Stable field order so a written-back spec stays readable + diff-friendly.
        return yaml.safe_dump(
            {
                "program_id": self.program_id,
                "program_name": self.program_name,
                "org_name": self.org_name,
                "bundle_root": self.bundle_root,
                "opportunity_ids": self.opportunity_ids,
            },
            sort_keys=False,
        )


def load_cohort_spec(path) -> CohortSpec:
    return CohortSpec.from_yaml(Path(path).read_text())


def save_cohort_spec(path, spec: CohortSpec) -> None:
    Path(path).write_text(spec.to_yaml())
