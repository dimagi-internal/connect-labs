# supply-chc-copacks

The CHC co-pack procurement played end to end on the live `/supply/` screens.
The recipe is `docs/walkthroughs/supply-chc-copacks.recipe.yaml`, and canopy-web owns the story (the narrative lock).

- **Programme:** labs-only 10601, registered through `synthetic_create_labs_only` (opportunity 10070).
- **Setup:** `ensure_demo.py` runs `seed_remote.py` on the labs worker over ECS exec. It needs `AWS_PROFILE=labs`. The setup purges 10601 and seeds it again through `call_operation`. Then it writes `.realized.json`, which is gitignored because it holds the update link's raw token.
- **Auth:** this is core labs, not the `/oes/` form login. Render with a labs session, either `--storage-state ~/.ace/labs-session.json` or, when other narratives are rendering at the same time, a session minted just for this one. The programme context lives in the server session, so two renders sharing a session switch each other's programme mid-take. `/supply/u/<token>/` needs no login.
- **Needs deployed:** the seed uses `payment_terms`, `award_create(decided_on=…)`, a round line's `components` and release-rated stores. Run it only against a labs that has them.

What each scene performs:

- Amara places the order and pays.
- Tunde confirms the order and the payment, then receives and inspects the batch, all through the link.
- Amara subscribes to the low-stock alert and records Sahel's stock take as an override.
- The checks page and the resupply plan then show the result.
- Tunde records Sahel's collection through the link.

Every date is relative to the render day, including the ones the scenes type. The chain took about a week in the world and is filmed in minutes, so each act is entered with the day it happened (`placed_on`, `paid_on`, `payment_received_on`, `received_on` in `.realized.json`): signed and confirmed six days ago, paid five days ago, the payment seen arriving four days ago, the goods received yesterday, the stock take and the release today. The order page and the link page read those days, not the minute they were typed.

Amara's low-stock alert goes to her own address by name (an invented `.example` address, which never delivers), not to the login the render uses.

On any day this gives:

- a consumption rate of 3,033 co-packs a month;
- 170 cartons after the stock take, which is 2.80 months against a minimum of 3;
- a send quantity of 194 cartons;
- 364 cartons and 6.00 months after the collection;
- the co-pack order paid in advance, 120 USD recoverable for the 4 refused cartons;
- the warehouse rated on its releases (376 cartons over 90 days before the take, 570 after it), inside its 2–6 month band after the collection.
