# Filling in organisation HQs with an AI sweep

The marketplace network map puts one dot per organisation at its **head office**. The importer (`marketplace_import`) turns a town name into coordinates with a gazetteer (`pulse/hq_location.py`). It is deliberately simple. **Deciding which town is the head office is research, not parsing**, and it is done by an AI sweep whose answers land in the LLO Directory where people can see and correct them.

The rule the map follows: **when the HQ can be resolved, the dot goes there, whatever country it is in.** That holds even when the HQ country is not one the organisation works in (a London office for Sierra Leone work is shown in London).

## Where the answers live

Three columns at the end of the directory's **Organizations** tab. The importer finds them by header text, so they can sit anywhere:

| Column | Holds | Read by the importer? |
|---|---|---|
| **HQ City** | `Town, Country`, e.g. `Maiduguri, Nigeria` | Yes. It wins over Office Address. |
| **HQ Basis** | Confidence (`high` / `medium` / `low`) and one sentence of evidence | No, it is for people |
| **HQ Source** | The URL, `directory Office Address`, or `Connect delivery area` | No, it is for people |

**A value a person typed always wins.** The sweep only fills blank HQ City cells. To correct an answer, overwrite HQ City and set HQ Basis to `human: <why>`; the next sweep will leave it alone.

## When to run it

- After a batch of new organisations lands in the directory (a new EOI round imported).
- When the map shows many hollow "country only" dots.
- It is safe to re-run at any time, since it only fills blanks.

## How to run it (Claude Code, about 30–45 minutes for 240 orgs)

1. **Export the inputs.** Read the Organizations tab as `ace@dimagi-ai.com`. For each row **with a blank HQ City**, keep: name, short name, Countries of Operation, Regions/States, Primary Sector, Website, Office Address, Organization Notes, and the sheet row number.
   - **Organisation data only.** Drop the "Email addresses from Contacts sheet" column, and replace any email address inside Office Address or Notes with `[email removed]`. People's names, emails and phones never go into the sweep.
2. **Add the delivery clue** for organisations that have delivered on Connect: the top 3 towns their visits centre on, as `connect_delivery_towns`. This comes from the per-opportunity distance sheet, or by sampling `user_visits` GPS per opportunity, taking the median point and finding the nearest town of 5,000+ people. It is a fallback clue, not the answer: where an organisation works is not where its office is.
3. **Split into batches of ~30** and dispatch one research subagent per batch, in parallel, each with the prompt below plus its input and output file paths.
4. **Validate every answer before writing.** Run each `hq_city` through `connect_labs.pulse.hq_location.resolve(countries, "", "", hq_city=<answer>)` and require `precision == "city"`. The town part is matched by exact name, so a small town works as long as the gazetteer lists it. An answer that does not resolve usually names a neighbourhood or a spelling the gazetteer lacks. Fix it by hand (use the city, or GeoNames' spelling) rather than adding a rule to the code.
5. **Write to the sheet**, into blank HQ City cells only. Locate the three columns by header, never by letter.
6. **Run the importer**: the `Run Labs management command` GitHub workflow with `marketplace_import`. It prints the tier counts (`N city, N region, N country`).
7. **Spot-check** the low-confidence answers, and every delivered organisation whose HQ is far from its delivery area. The distance sheet flags those.

## Practical limits

- **Web search is capped at 200 searches per Claude Code session**, shared by all of its subagents. A full first pass over ~240 organisations uses most of that, because most rows resolve from Office Address with no search at all. For the second, deeper pass over the blanks, start a fresh session or raise `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`.
- **Run two passes.** The first pass gives each organisation ~4 tool calls. The second pass takes only the blanks, gives each ~8 calls with extended search, and hands the agent the first pass's notes so it doesn't repeat them.
- **Some real places aren't in the gazetteer**: villages, and refugee settlements under another name. Rename the answer to the nearest town the gazetteer has, and keep the true place in HQ Basis ("Office is in Mboko, Fizi Territory; Uvira is the nearest town the map knows").

## The first sweep (2026-10-05)

| | Organisations |
|---|---|
| High confidence | 163 |
| Medium | 37 |
| Low (mostly inferred from where the org delivers) | 10 |
| Still blank | 29 |

The 29 blanks are mostly organisations with no address, no website and no Connect delivery. The only way to fill them is to ask the organisation.

The sweep also corrected the old parser in a few places:
- **Comité d'Entraide Familiale:** its "Lusambo" is a village in Fizi, South Kivu, not the city of Lusambo 640 km away.
- **Wounded Healers:** the old parser read a Nairobi post-office box; the organisation's website gives Ruiru.
- **Gardens for Health:** the directory gives a US PO box, but the programmes and office are in Kigali.

## The research prompt

```
You are finding the HEAD OFFICE town of each organisation in a batch from the
Connect LLO Directory (local implementing organisations — NGOs, CBOs, health
organisations, mostly in Africa and South Asia). The answer places each
organisation's dot on a network map.

For each organisation, work through the evidence in this order and stop when
you have a confident answer:

1. office_address names a town or city — use it. A neighbourhood or district
   of a city means that city ("Gombe, Kinshasa" -> Kinshasa; "Kawempe" ->
   Kampala). A street named after a town is not that town ("Mumias Rd,
   Nairobi" -> Nairobi).
2. The organisation's own website — contact page, footer, About page. A
   parked or for-sale domain is not evidence.
3. Web search: "<name> <country> NGO office", "<short name> <country> head
   office". Registries, LinkedIn/Facebook pages, GlobalGiving, devex,
   developmentaid.org and funder grant lists all count. The result must be
   clearly the SAME organisation (name and country match).
4. Only if nothing above works: if connect_delivery_towns is non-empty, answer
   the top delivery town with confidence "low" and basis "inferred from Connect
   delivery area". Never do this for an org with no delivery towns.

The head office can be in a different country from where the org works —
report where the office actually is.

Rules:
- NEVER invent. If you cannot find it, leave hq_city empty and say so.
- Organisation data only — no person's name, email or phone.
- Name the town as an English gazetteer would, plus the country:
  "Dar es Salaam, Tanzania". The city, not a neighbourhood, ward or street.
- Confidence: high = an address or official page names the town; medium =
  good secondary sources agree; low = delivery-area inference or weak/old
  evidence.
- At most ~4 tool calls per organisation.

Output a JSON array, one object per input organisation:
{"sheet_row", "name", "hq_city", "confidence", "basis", "source"}
```

## What not to do

- **Don't add rules to `hq_location.py` to rescue individual addresses.** That's how it accumulated postcode tables and district aliases. If an answer won't resolve, fix the answer in the sheet.
- **Don't let the sweep overwrite a human answer.** Fill blanks only.
- **Don't send contact data.** Organisation names, websites and addresses are public organisation data; the Contacts tab is not.
