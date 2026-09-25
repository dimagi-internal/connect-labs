
## 5. An organisation's own tender (added 2026-09-25)

A tender can be **published by an organisation** as its own one-off listing:

- `Tender.owner_org` (the publishing `LabsOrg`), `slug` (unique; the listing lives
  at `/supply/market/t/<slug>/`), `brief` (a few paragraphs above the products)
  and `hue` (the listing's colour, one of a fixed palette).
- **Restricted** (`visibility = private`) means visible only to the organisations
  on `Tender.invited_orgs` — a list, not an outreach log — plus any organisation
  already invited through the program's outreach. To everyone else the listing
  is the same 404 as a tender that does not exist.
- The list is managed at `/supply/market/t/<slug>/manage/` by whoever manages
  the owning organisation (its admins; its Connect members for a Connect org)
  or is on the tender's program. They can also edit the brief, colour and
  visibility there. Only organisations registered as suppliers can be invited.
- Agents: `tender_invite_org` / `tender_uninvite_org` operations; the tender
  record carries `owner_org_id`, `slug`, `brief`, `hue`, `invited_org_ids`.
- The program team sets owner, slug, brief and colour on the tender form.

A tender with no owner and no slug behaves exactly as before.
