"""ace's canopy test vault key: unlock a sealed (private) conversation in the panel, and keep
the key's secrets out of everything the harness writes.

A Data page conversation runs on sensitive-data runners, so canopy seals its stored words to
its people. Live replies arrive in the clear (in transit); history -- a reload, scroll-back --
shows a "locked" banner until the person unlocks their vault key in the panel. ace's key is
passphrase-only (canopy-web's agent "test vault key"); its two values live ONLY in
``~/.ace/.env`` as ``ACE_CANOPY_TEST_VAULT_PASSPHRASE`` and ``ACE_CANOPY_TEST_VAULT_SECRET_KEY``.
They are typed into canopy's own unlock form, inside its frame, and never printed, logged,
screenshotted (the form is gone before any screenshot) or written: ``scrub`` replaces them in
every file of a run.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILES = (Path.home() / ".ace" / ".env",)
NAMES = ("ACE_CANOPY_TEST_VAULT_PASSPHRASE", "ACE_CANOPY_TEST_VAULT_SECRET_KEY")
WRONG = "do not open your vault key"
REDACTED = "[redacted:vault]"


def _read() -> dict:
    found = {n: os.environ[n] for n in NAMES if os.environ.get(n)}
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            key, _, val = line.partition("=")
            if key.strip() in NAMES and key.strip() not in found:
                val = val.strip().strip('"').strip("'")
                if val:
                    found[key.strip()] = val
    return found


def available() -> bool:
    return all(_read().get(n) for n in NAMES)


def scrub(text: str) -> str:
    for value in _read().values():
        if value and len(value) >= 4:
            text = text.replace(value, REDACTED)
    return text


def scrub_dir(run_dir: Path) -> int:
    """Replace the vault values in every text file under ``run_dir``. Returns files changed."""
    changed = 0
    for path in run_dir.rglob("*"):
        if path.suffix not in {".json", ".md", ".txt", ".html", ".log"} or not path.is_file():
            continue
        text = path.read_text(errors="replace")
        clean = scrub(text)
        if clean != text:
            path.write_text(clean)
            changed += 1
    return changed


def unlock(frame, page, timeout_ms: int = 30_000) -> dict:
    """If the panel shows its sealed banner, unlock with ace's test key. Never raises.

    Returns ``{banner: bool, unlocked: bool, error: str}`` -- no secret in it.
    """
    out = {"banner": False, "unlocked": False, "error": ""}
    try:
        banner = frame.locator('[data-testid="sealed-banner"]')
        if not banner.count():
            return out
        out["banner"] = True
        values = _read()
        if not all(values.get(n) for n in NAMES):
            out["error"] = "ace's test vault values are not in ~/.ace/.env"
            return out
        banner.locator('[data-testid="sealed-unlock"]').first.click()
        form = frame.locator('form[data-testid="vault-unlock"]')
        form.wait_for(timeout=timeout_ms)
        form.locator('input[aria-label="Vault passphrase"]').fill(values[NAMES[0]])
        secret = form.locator('input[aria-label="Secret Key"]')
        if secret.count() and secret.first.is_visible():
            secret.first.fill(values[NAMES[1]])
        form.locator('[data-testid="vault-unlock-passphrase"]').click()
        deadline = timeout_ms / 1000
        waited = 0.0
        while waited < deadline:
            page.wait_for_timeout(1000)
            waited += 1
            text = frame.evaluate("() => document.body.innerText")
            if WRONG in text:
                out["error"] = "canopy: that passphrase and Secret Key do not open the vault key"
                break
            if (
                not frame.locator('form[data-testid="vault-unlock"]').count()
                and not frame.locator('[data-testid="sealed-banner"]').count()
            ):
                out["unlocked"] = True
                break
        else:
            out["error"] = "the unlock form did not close"
        if frame.locator('form[data-testid="vault-unlock"]').count():
            # Never leave the filled form on screen for a screenshot.
            for sel in ('input[aria-label="Vault passphrase"]', 'input[aria-label="Secret Key"]'):
                field = frame.locator(f'form[data-testid="vault-unlock"] {sel}')
                if field.count():
                    field.first.fill("")
    except Exception as e:  # noqa: BLE001 -- an unlock problem is reported, never fatal
        out["error"] = scrub(f"{type(e).__name__}: {e}")[:300]
    return out
