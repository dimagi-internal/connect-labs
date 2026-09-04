# Draft previews

Self-contained builds of marketing pages that are still in review, so they can
be opened as rendered pages from a URL before they reach labs or production.

- `connect-in-action.html` — the "Connect in Action" story page (PR #1432).
  https://dimagi-internal.github.io/connect-labs/preview/connect-in-action.html

Each file carries `<meta name="robots" content="noindex, nofollow">` and nothing
links to it. These are throwaway builds, not the source of truth: the real page
lives in `templates/prelogin/` on `main`. Delete a file here once its PR lands.

The docs workflow only rewrites `docs/` on this branch, so files here survive
documentation deploys.
