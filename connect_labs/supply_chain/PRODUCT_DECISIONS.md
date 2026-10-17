# Supply: product decisions

Decisions Jonathan has made about how Supply (`/supply/`) behaves. Each one closes a
question that kept coming back, so don't re-open it in a design, a fix or a demo
finding. Each says why, so it can be changed on purpose rather than eroded by a
"cleanup". Change this file in the same PR as any change that departs from it.

The longer design history is in `docs/superpowers/specs/` (start with
`2026-09-11-rutf-procurement-design.md` §0-1, §5.6 basis flags, §11 append-only
quotes, §17 buyer of record, and `2026-09-26-supply-sophie-history-design.md`).

## Who uses it

- **The programme's own staff are the only users inside the system.** Sophie, the
  programme manager and buyer of record, is the worked example. Suppliers and the
  freight forwarder email her. An AI (ACE, Claude over MCP, or the canopy panel)
  records the facts they send through the same operations a person uses. The AI
  is never a persona, and nothing is designed for it as a user.
- **The goal is a product a programme manager would use for a real procurement**,
  not a demo. Demos of Supply are probes: product findings decide what changes.

## Words

- **It is a _tender_, never a "round"** (#2037).

## Screens

- **Status first, on the labs design system** (#2173).
- **Tables, not email.** The record is read and corrected in place. Drafted mail is
  one small part. A problem that is only about drafted emails is low severity.
- **Every stored value is editable where it is read** (`supply_chain/cells.py`,
  #2244). A one-value correction never needs a trip to a form. One value per cell,
  sheets full width, records as rows: in the comparison, a quote is a row and a
  fact is a column (#2250).
- **Data, not verdicts** (2026-10-07). The product does not decide which quotes are
  "comparable": no comparable counts, chips or tiles, and nothing (award, ranking)
  gated on one. The tables make every figure plain and sortable. Show the landed
  price where its inputs exist, and name the missing input where they don't.
  Interpreting the figures is the AI's job when the person asks, through the
  canopy panel on the tender and comparison pages (`supply:read`, #2283). A request
  for the product to decide comparability is out of scope. A figure that is
  unclear, unsortable or wrong is a real defect.

## Tenders and awards

- **"On us" / "On suppliers" come from six coarse rules**, and each item names its
  rule. Judgement calls belong to the AI layer, not to more hard-coded rules.
- **Duty follows the tender's duty terms by Incoterm.** Clearing and freight
  estimates live on the tender, and freight is ours under EXW/FCA/FOB (#2170,
  #2179).
- **A reminder counts only once it is marked sent** (#2179).
- **An award before the deadline is allowed.** The Award line shows what is still
  open (days to the deadline, suppliers who stayed silent, replies owed), and the
  award records it (#2200).
- **Moves and quote facts stay separate.** "On us N" / "Us N" count moves only. A
  fact missing from a quote is never a move and is not counted there (2026-10-04).

## Stock

- **Stock is planned in days, from 7 days of use** (#2311, replacing 30 days). A rate
  needs `resupply.MINIMUM_WINDOW_DAYS` days of dispensing, and every figure says
  how many days it rests on. The Stock page shows demand per day and days of stock
  against the band, in days. Before 7 days, say when the estimate will arrive and
  give the rate so far, never a bare "unknown".

## Synthetic clones

- **A clone started over MCP must equal one made with the page's default button**
  (#2153).
