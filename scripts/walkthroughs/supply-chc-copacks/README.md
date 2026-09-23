# supply-chc-copacks

The CHC co-pack procurement played end to end on the live `/supply/` screens.
The recipe is `docs/walkthroughs/supply-chc-copacks.recipe.yaml`, and canopy-web owns the story (the narrative lock).

- **Programme:** labs-only 10601, registered through `synthetic_create_labs_only` (opportunity 10070).
- **Setup:** `ensure_demo.py` runs `seed_remote.py` on the labs worker over ECS exec. It needs `AWS_PROFILE=labs`. The setup purges 10601 and seeds it again through `call_operation`. Then it writes `.realized.json`, which is gitignored because it holds the update link's raw token.
- **Auth:** this is core labs, not the `/oes/` form login. Render with a labs session, either `--storage-state ~/.ace/labs-session.json` or, when other narratives are rendering at the same time, a session minted just for this one. The programme context lives in the server session, so two renders sharing a session switch each other's programme mid-take. `/supply/u/<token>/` needs no login.
- **Needs deployed:** the update-link CSRF fix (`Referrer-Policy: same-origin`). Before it, every submission on the link is a 403, so scenes 6, 7 and 13 cannot act.

What each scene performs:

- Amara places the order and pays.
- Tunde confirms the order and the payment, then receives and inspects the batch, all through the link.
- Amara subscribes to the low-stock alert and records Sahel's stock take as an override.
- The checks page and the resupply plan then show the result.
- Tunde records Sahel's collection through the link.

Every seeded date is relative to the render day. On any day this gives:

- a consumption rate of 3,033 co-packs a month;
- 170 cartons after the stock take, which is 2.80 months against a minimum of 3;
- a send quantity of 194 cartons;
- 364 cartons and 6.00 months after the collection.
