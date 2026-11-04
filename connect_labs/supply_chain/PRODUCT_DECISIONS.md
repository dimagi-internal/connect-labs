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

- **The forecast's course is the EXPECTED course once enough children have finished;
  the protocol is its upper limit and the early fallback** (2026-11-04, Jonathan
  Jackson, on Sophie Feintuch's RUTF feedback; revisits "the protocol leads",
  #2386). The protocol's 168 sachets is the most a child can have (12 visits); using
  it for everyone overstates need once dozens of children have exited. From
  `forecast.K_MIN` children who completed, the course is their median, never above
  the protocol. Every forecast says which it used and how many children it rests on.
  The programme can hold it at the protocol (`course_basis: protocol` on the case
  rule). The daily ration likewise defaults to the protocol's, and the programme may
  lay the plan at what visits show children are given (`dose_basis: delivered`);
  that actual rate is always shown beside it. "Not seen for N days = out of
  treatment" is the forecast's own rule (`lost_after_days`), programme-settable, and
  is not the definition of default.

## Synthetic clones

- **A clone started over MCP must equal one made with the page's default button**
  (#2153).

## Stock issues, custodians and expiry (Sophie's RUTF feedback, 2026-11)

- **A problem with stock is a `StockIssue`, not a free-text `loss` note.** Reporting one posts the
  loss at once, linked to the issue (`Movement.stock_issue`); a recovery or replacement posts the
  stock back linked to the same issue; a write-off posts nothing more. Older `loss` movements with
  only a note stay readable and are listed on the Issues page.
- **A partner's Issues page is the same page filtered by `?partner=<org id>`.** A store belongs to
  the organisation that runs it, else the nearest one above it in the network.
  `stock.services.issues.open_issue_summary` (and the `stock_issue_summary` operation) is the one
  count of open issues for every page.
- **A custodian is history, not an edit.** Naming a new one ends the one before it that day.
- **Expiry warnings are a check (`stock_expiring`), with the lead time on the product**
  (`Commodity.expiry_warning_days`, 90 days to begin with), so alerts pick it up like any other.

## Partners: how each distributes, and who runs which place (Sophie's RUTF feedback, 2026-11)

- **Each partner states how it distributes** (`PartnerPlan`: through facilities, straight to field workers, or
  a mix) and its network is built from that (`partner_plan_set` with `build_network`): a warehouse the partner
  runs, a minimum and maximum stock at each place (lower nearer the field worker), field workers under the
  right point. A hand-set band is never overwritten.
- **A change of model is a dated change of plan, never field workers quietly moved.** The plan before ends the
  day the new one starts; field workers move only when asked (`move_workers`), and the plan's note names each
  one moved. Without it, the ones who would move are listed in the result.
- **"Run by: not recorded" is fixed at setup**: `partner_places_assign` names the partner for places that name
  none, and never takes a place another partner already runs unless told to.

## Handouts above the field worker (Sophie's RUTF feedback, "Recording handovers above the FLW")

- **A handout is planned, then confirmed, and only the confirmed amount moves stock.** A planned
  `Distribution` posts nothing; its lines count as committed until someone says what was given
  (`distribution_confirm`). The ledger posts the quantity GIVEN, never the plan's. A run's status
  (planned / partly confirmed / confirmed) is derived from its lines, not stored.
- **Three figures per line, never merged:** planned (`quantity`), given (`quantity_given`) and
  received (`quantity_received`, said by the receiver). A gap between any two is shown, not corrected.
- **A message is read deterministically** (`distribution/messages.py`): one "Place - N cartons"
  per line. Anything it cannot read or cannot place clearly is handed back for a person; it never
  guesses and never adds up two lines for one place. The message is kept on the handout
  (`message_text`); a photo is kept as a `Document` and its numbers are typed by a person. There is no
  OCR or language model on this path: an AI that reads a photo over MCP calls `distribution_confirm`.
- **"What to issue" comes through one function** (`distribution/issue_plan.py`), today the forecast's
  coming-week need less what the worker holds. Repoint that function, not the screens, when the plan
  is computed elsewhere.
- **Confirming through an update link uses the existing link, with no new sign-in.** A link covering a
  store (or a place on a handout) offers "Confirm what you handed out"; a link covering the place it
  went to (a facility, or a field worker's own holding on a link sent to their Connect username)
  offers "Confirm what you received". Each is shown only when there is something to confirm, goes through
  the same operation as the screens, and is recorded as the link's organisation's word.


## The plan: what to issue, break-even, who sees what (Sophie's RUTF feedback, 2026-11)

- **Supply adopts the CBI dashboards' method, fed by its own records.** What to issue a field worker is
  the week's follow-ups and expected new children, plus the buffer, less what Supply's records say they
  hold (never the phone's balance, never a typed-in starting stock); the break-even sets the stock left
  after what children in treatment are owed against a full course for each child still to come up to
  the target. All of it is the forecast's figures (`stock_forecast`) chosen and worded by
  `plan/service.py`; there is no second forecast.
- **Three numbers are the partner's, saved as the plan (`StockPlan`)**: new children per worker a week
  (default: each worker's own recent rate), buffer in extra days (default 0, for everyone or one place)
  and the target number of children (default 500; 2,500 in phase 3). A change is an attributed
  `stock_plan_set` call; the default is shown beside it and the history lists who changed what.
- **Pages are split by person and rhythm** (`/supply/plan/`: supervisor, weekly; `plan/stores/`: store
  manager; `plan/programme/`: running organisation) over one operation, `stock_plan_view`, so a figure
  cannot differ between them. Every number says where it came from.
- **Not counted, and said so on the page**: appetite tests and household leakage (no source in Supply).
