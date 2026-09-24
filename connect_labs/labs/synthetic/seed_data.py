"""Read the OES demo's facts from Drive.

THIS REPOSITORY IS PUBLIC. The demo names real partner organisations and
carries their real volumes and prices, so those facts live in a Drive folder
and this module is the only way in. Nothing here holds a partner name, a
quantity or a price.

The schema check is deliberately loud: a seeder that half-runs on a malformed
document leaves a demo environment whose figures nobody can defend, which is
worse than one that did not run at all.
"""

from __future__ import annotations

import json

REQUIRED_SECTIONS = ("orgs", "commodities", "rounds")
DEFAULT_FILENAME = "oes-demo.json"


class SeedDataError(ValueError):
    """The Drive document is absent, unreadable, or not the shape we seed from."""


def load_seed_data(folder_id: str, filename: str = DEFAULT_FILENAME, client=None) -> dict:
    """The seed document from `folder_id`, validated.

    `client` is injected by the tests; in production it is the same
    `DriveClient` the synthetic fixture store uses, so the credentials and
    the shared-drive handling are already solved.
    """
    if client is None:  # pragma: no cover - exercised only against real Drive
        from connect_labs.labs.synthetic.gdrive import DriveClient

        client = DriveClient()

    listing = client.list_folder(folder_id)
    file_id = listing.get(filename)
    if file_id is None:
        raise SeedDataError(f"no {filename} in Drive folder {folder_id} (found: {sorted(listing)})")

    try:
        document = json.loads(client.download_file(file_id).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SeedDataError(f"{filename} in folder {folder_id} is not readable JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise SeedDataError(f"{filename} must hold a JSON object, not {type(document).__name__}")

    missing = [section for section in REQUIRED_SECTIONS if section not in document]
    if missing:
        raise SeedDataError(f"{filename} is missing required section(s): {', '.join(missing)}")

    for org in document["orgs"]:
        if "connect_organization_id" not in org:
            raise SeedDataError(
                f"org {org.get('slug', '?')!r} has no connect_organization_id — the demo points at "
                "real Connect organisations, so a lookalike org is a defect, not a fallback"
            )

    return document
