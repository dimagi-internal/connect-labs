# DDD context — connect-labs

Pointers only. Product decisions and demo facts live with the code they describe,
where they are versioned with it. Don't add product context here: it goes stale
here and is invisible to everyone not running DDD. Put a new product decision in
the product's own doc (below) or a design spec, and a demo fact in the demo README.

- **base_url:** https://labs.connect.dimagi.com
- **Auth:** the labs browser session at `~/.ace/labs-session.json`, seeded out of band
  via `/ace:labs-login`. Specs carry no `auth` block; the recorder gets
  `--storage-state ~/.ace/labs-session.json`. The cookie's local expiry (~12h) is
  enforced by Playwright even while the server session lives, so an all-red
  preflight with no hint means re-mint the session, not a broken recipe.
- **Publishing and loop settings:** `.canopy/ddd/config.yaml` (canopy-web `connect`
  workspace, `objective: product`, deploy gate).
- **Narratives:** `docs/walkthroughs/` (recipes, why-briefs) and
  `scripts/walkthroughs/<narrative>/` (setup). Earlier runs' outcomes are on
  canopy-web, read by canopy (`scripts.ddd.prior_runs`).
- **Supply product decisions (don't re-ask):** `connect_labs/supply_chain/PRODUCT_DECISIONS.md`.
- **Supply demo data (which clone to film, what is invented):** `connect_labs/supply_chain/demo/README.md`.
- **Repo rules (public repo, deploy only from main):** `CLAUDE.md`.
