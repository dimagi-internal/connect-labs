/* Shared UI primitives — globals consumed by every tab_*.jsx file.
   KeyFigures is the OCHA-style stat row that also carries the Phase-3
   dashboards, so it lives here rather than in any one tab. */

const { useState, useEffect, useMemo, useCallback, useRef } = React;

/* `asOf` stamps the page with the instant its figures describe.

   No surface carried one, while rows carried dates of Jul 26 / Jul 30 / Jul 31 /
   ETA Aug 2. An auditor cannot cite a disbursement or a coverage figure without
   knowing what moment it was true of — and the absence is exactly what let a
   consignment dated Jul 31 sit there marked "Delivered" on the 30th without
   anything on the screen contradicting it. With the stamp present, a
   future-dated row is self-evidently wrong instead of merely unnoticed. */
function Page({ title, lede, actions, asOf, children }) {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          {lede ? <p className="page-lede">{lede}</p> : null}
          {asOf ? (
            <p className="page-asof muted small">
              Figures as of {formatDate(asOf)}. Anything dated later has not
              happened yet.
            </p>
          ) : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

function Card({ title, subtitle, actions, children, className }) {
  return (
    <section className={`card ${className || ''}`}>
      {title || actions ? (
        <header className="card-head">
          <div>
            <h2>{title}</h2>
            {subtitle ? <div className="card-sub">{subtitle}</div> : null}
          </div>
          {actions ? <div className="card-actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className="card-body">{children}</div>
    </section>
  );
}

/* The figure row, with a hierarchy.

   Every tile used to render identically, so a static housekeeping count
   ("14 qualified suppliers") had exactly the same size, weight and colour as
   a site with nothing left to give a child today ("0 wk thinnest cover").
   A reader's eye landed nowhere, and on a projector nothing said which number
   was the live risk.

   A figure may now declare `tone` — critical | at-risk | ok — which is the
   ONLY thing that colours a number on these screens, and `lead`, which makes
   it the one tile the row is built around. Everything without either stays
   quiet on purpose: most counts are context, not news. */
function KeyFigures({ figures }) {
  return (
    <div className="keyfigures">
      {figures.map((fig) => (
        <div
          className={`keyfigure${fig.lead ? ' keyfigure-lead' : ''}${
            fig.tone ? ` tone-${fig.tone}` : ''
          }`}
          key={fig.label}
        >
          <div className="keyfigure-value">{fig.value}</div>
          <div className="keyfigure-label">
            {fig.label}
            {fig.method ? (
              <InfoNote label={fig.label} text={fig.method} />
            ) : null}
          </div>
          {fig.hint ? <div className="keyfigure-hint">{fig.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}

/* How a figure was made, available rather than asserted.

   The method behind every derived number on these screens was written out as
   a paragraph of ~11px grey text under the thing it described — four of them
   on the funder page alone. That is the honest instinct and the wrong shape:
   it made explanation the dominant visual texture of the product, and three
   independent reviews reported the load-bearing text as too small to read.

   The method has to be REACHABLE, not shouted. It lives behind an "i" the
   reader opens when they want to check, which is exactly when they want it. */
/* The coverage banding, in ONE place.

   Three surfaces rendered `>= 80 ? good : >= 50 ? warn : bad` independently, and
   all three painted over-delivery the same green as near-complete cover — so a
   district at 145.8% of need and one at 91% read as the same outcome, on the one
   axis where this product editorialises. Over 100% is a positioning question, not
   a success, so it gets its own neutral tone. */
function coverageTone(pct) {
  if (pct === null || pct === undefined) return 'muted';
  if (pct > 100) return 'accent';
  if (pct >= 80) return 'good';
  if (pct >= 50) return 'warn';
  return 'bad';
}

/* The banding rule above, in words, for the card that uses it.

   Colour is a verdict, and this product's whole thesis is that a verdict must be
   stated so it can be challenged. These pills coloured 0% and 34% red, 67.6%
   amber and 91% green against a threshold written down nowhere on any surface —
   an interpretation stamped on the measure with no way to contest it. Any card
   that renders a coverageTone pill states this rule beside it. */
const COVERAGE_BANDS =
  'Bands: below 50% red, 50–79% amber, 80–100% green, above 100% marked as ' +
  'over-positioned rather than as a success — more stock than the caseload ' +
  'needs is a question about where it should have gone, not an achievement.';

/* The banding rule as a legend line with its reasoning one click away.

   Printed in full it was one of two dense grey paragraphs stacked under the
   table, and the pair of them read as small print nobody finishes — which is
   the failure mode a disclosure model exists to avoid. The thresholds are the
   part a reader needs while looking at a pill; why over-positioning is not a
   success is the part they need only if they disagree. */
function CoverageBandsNote() {
  return (
    <p className="muted small method-note">
      Bands: below 50% red · 50–79% amber · 80–100% green · above 100%{' '}
      <em>over</em>
      <InfoNote label="the coverage bands" text={COVERAGE_BANDS} />
    </p>
  );
}

/* A national rate as a true ratio of the national sums.

   Averaging the rows' own percentages weights a small district equally with a
   large one — it would give Gombe's 91% the same say as Borno's 34% across five
   times the caseload. */
function nationalPercent(rows, numeratorKey) {
  const caseload = rows.reduce((n, r) => n + (r.caseload || 0), 0);
  if (!caseload) return '—';
  const top = rows.reduce((n, r) => n + (r[numeratorKey] || 0), 0);
  return `${Math.round((top / caseload) * 1000) / 10}%`;
}

function InfoNote({ label, text }) {
  const [open, setOpen] = useState(false);
  // Fixed-position coordinates, computed from the trigger when it opens.
  // The popover was absolutely positioned inside the card, and .card clips
  // (overflow: hidden) — so a method note opened near a card's bottom edge cut
  // its own text mid-sentence, on the one element whose job is to carry the
  // method in full. position: fixed escapes every clipping ancestor.
  const [pos, setPos] = useState(null);

  // ...but a fixed popover is pinned to the VIEWPORT, so it does not travel
  // with the content it explains. Left open, it floats over whatever the reader
  // scrolls to next — a note about Borno's caseload sat over an unrelated
  // expiry row three screens later, and over the closing sentence of a
  // walkthrough, still asserting a derivation for figures it had nothing to do
  // with. An explainer that outlives its subject is worse than no explainer.
  //
  // So: any scroll dismisses it, as does Escape or a click anywhere else.
  // Capture-phase scroll listening catches scrolling inside a modal body or a
  // table wrap, not just the document.
  useEffect(() => {
    if (!open) return undefined;
    const close = () => setOpen(false);
    const onKey = (ev) => {
      if (ev.key === 'Escape') setOpen(false);
    };
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    document.addEventListener('click', close);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
      document.removeEventListener('click', close);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span className="infonote">
      <button
        type="button"
        className={`infonote-btn${open ? ' open' : ''}`}
        aria-label={`How ${label} is calculated`}
        aria-expanded={open}
        onClick={(ev) => {
          ev.stopPropagation();
          if (!open) {
            const r = ev.currentTarget.getBoundingClientRect();
            const width = Math.min(320, window.innerWidth * 0.74);
            // Flip above the trigger when there is not room below it.
            //
            // Anchored below unconditionally, a note opened on a figure near the
            // foot of its card spilled a couple of hundred pixels into the NEXT
            // card and sat on live data — a caseload method covering the two
            // coverage percentages it was meant to explain, and a cost-per-course
            // method covering the table beneath it. An explainer that destroys
            // the figures around it is worse than one that has to be scrolled to.
            //
            // The height is not known until it renders, so this uses the
            // stylesheet's max-height as the worst case; the note is at most
            // that tall, so a flip decided on it never spills either way.
            const MAX_H = 300;
            const below = window.innerHeight - r.bottom - 12;
            const flip = below < MAX_H && r.top > below;
            setPos({
              top: flip ? undefined : r.bottom + 6,
              bottom: flip ? window.innerHeight - r.top + 6 : undefined,
              left: Math.max(
                8,
                Math.min(r.left - 8, window.innerWidth - width - 12),
              ),
            });
          }
          setOpen(!open);
        }}
      >
        i
      </button>
      {open && pos ? (
        <span
          className="infonote-body"
          style={{
            position: 'fixed',
            top: pos.top,
            bottom: pos.bottom,
            left: pos.left,
          }}
          onClick={(ev) => ev.stopPropagation()}
        >
          {text}
        </span>
      ) : null}
    </span>
  );
}

const STATUS_TONES = {
  in_transit: 'info',
  planned: 'neutral',
  delivered: 'good',
  // Confirmed is the state that decides which rows count.
  //
  // It rendered as chip-accent — rgba(13,122,95,0.14) on accent-dark — beside
  // delivered's chip-good at #dcfce7 on #166534. Two pale greens, and the
  // distinction between them is which consignments a coverage figure and a
  // disbursement are allowed to include. `verified` is a filled chip, so the
  // stronger state looks stronger rather than looking like the same state.
  confirmed: 'verified',
  draft: 'neutral',
  open: 'good',
  closed: 'neutral',
  submitted: 'info',
  qualified: 'good',
  rejected: 'bad',
  active: 'good',
  expired: 'bad',
  revoked: 'bad',
  published: 'good',
  awarded: 'accent',
};

const STATUS_LABELS = {
  draft: 'Draft',
  open: 'Open',
  closed: 'Closed',
  submitted: 'Submitted',
  qualified: 'Qualified',
  rejected: 'Rejected',
  active: 'Active',
  expired: 'Expired',
  revoked: 'Revoked',
  published: 'Published',
  awarded: 'Awarded',
  planned: 'Planned',
  in_transit: 'In transit',
  delivered: 'Delivered',
  confirmed: 'Confirmed',
};

function StatusChip({ status, label }) {
  const tone = STATUS_TONES[status] || 'neutral';
  return (
    <span className={`chip chip-${tone}`}>
      {label || STATUS_LABELS[status] || status}
    </span>
  );
}

/* A published tender whose bid deadline has passed is not still collecting bids.
   It rendered as "Published" indefinitely — a tender three days past its own
   deadline reading as open, which is the first thing an auditor queries. The
   lifecycle field is untouched (bidding closing is a date passing, not a
   transition somebody makes); this is the display deriving what the date already
   says, the same way "late" is derived elsewhere in this app. */
function SolicitationStatusChip({ rfp }) {
  const closed =
    rfp.status === 'published' &&
    rfp.bid_deadline &&
    daysUntil(rfp.bid_deadline) !== null &&
    daysUntil(rfp.bid_deadline) < 0;
  if (closed) {
    return (
      <span className="cell-with-note">
        <span className="chip chip-info">Bidding closed</span>
        <InfoNote
          label="this status"
          text="The solicitation is published and its bid deadline has passed, so it is no longer collecting bids — it is waiting on evaluation and award. The underlying record is still Published; bidding closing is a date passing rather than a decision anybody takes, so it is derived here rather than stored."
        />
      </span>
    );
  }
  return <StatusChip status={rfp.status} />;
}

function Badge({ children, tone }) {
  return <span className={`chip chip-${tone || 'neutral'}`}>{children}</span>;
}

function EmptyState({ title, hint }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {hint ? <div className="empty-hint">{hint}</div> : null}
    </div>
  );
}

/* A column may declare `total: (rows) => node` to render a footer cell.
   `totalsLabel` names the row in the first column that declares no total.

   Two separate findings needed this. A funder adding the DISBURSED column by
   hand got $547k against a $546k headline — every cell was rounded to the
   nearest thousand independently, so the column of roundings did not sum to the
   rounding of the sum, on a page whose subject is reconciliation. And the
   coverage card, whose stated job is "where do I put my own resources", made
   the reader add three rows to learn the national figure. Both are the same
   omission: a table that invites addition should do the addition. */
function DataTable({
  columns,
  rows,
  empty,
  emptyHint,
  rowKey,
  onRowClick,
  totalsLabel,
  defaultSort,
  rowClass,
}) {
  // A declared default sort renders its caret from the first paint, so the
  // order a reader is looking at is attributable. Without one, a table arrived
  // in whatever order the payload happened to be built in and the headers
  // claimed no ordering at all — which reads as unordered, not as "sorted by
  // the thing you would expect".
  const [sort, setSort] = useState(
    defaultSort
      ? { key: defaultSort.key, dir: defaultSort.dir || 1 }
      : { key: null, dir: 1 },
  );

  const sorted = useMemo(() => {
    if (!sort.key) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const value = col.sortValue || col.value;
    return [...rows].sort((a, b) => {
      const av = value(a);
      const bv = value(b);
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (av > bv ? 1 : -1) * sort.dir;
    });
  }, [rows, sort, columns]);

  if (!rows.length) {
    // An empty state that says what to do next, not just that there is
    // nothing. "No tokens yet." in the middle of a blank panel tells a reader
    // the screen loaded and nothing else.
    return (
      <EmptyState title={empty || 'Nothing to show yet.'} hint={emptyHint} />
    );
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                className={col.sortable === false ? '' : 'sortable'}
                onClick={() =>
                  col.sortable === false
                    ? null
                    : setSort((s) => ({
                        key: col.key,
                        dir: s.key === col.key ? -s.dir : 1,
                      }))
                }
              >
                {col.label}
                {sort.key === col.key ? (
                  <span className="sort-caret">
                    {sort.dir > 0 ? ' ▲' : ' ▼'}
                  </span>
                ) : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={rowKey ? rowKey(row) : i}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`${onRowClick ? 'clickable' : ''}${
                rowClass ? ` ${rowClass(row)}` : ''
              }`.trim()}
            >
              {columns.map((col) => (
                <td key={col.key}>
                  {/* `sorted`, not `rows`: a renderer that compares against its
                      neighbour (see repeatsAbove) has to see the order actually
                      on screen, or it subdues the wrong cells the moment a
                      reader sorts by a different column. */}
                  {col.render ? col.render(row, i, sorted) : col.value(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {columns.some((c) => c.total) ? (
          <tfoot>
            <tr className="totals-row">
              {columns.map((col, i) => (
                <td key={col.key}>
                  {col.total
                    ? col.total(rows)
                    : i === 0
                    ? totalsLabel || 'Total'
                    : null}
                </td>
              ))}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

/* True when this row's value repeats the row above it, in the CURRENT order.

   A value repeated down 18 rows is noise that reads as one block; subduing the
   repeat restores the row boundaries without removing anything, so the cell
   stays copyable and stays correct when the table is re-sorted. */
function repeatsAbove(rows, index, pick) {
  if (!rows || index <= 0) return false;
  return pick(rows[index - 1]) === pick(rows[index]);
}

function Modal({ title, children, onClose, footer, wide }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className={`modal ${wide ? 'modal-wide' : ''}`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2>{title}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer ? <footer className="modal-foot">{footer}</footer> : null}
      </div>
    </div>
  );
}

function FormRow({ label, hint, error, children }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint ? <div className="field-hint">{hint}</div> : null}
      {error ? <div className="field-error">{error}</div> : null}
    </div>
  );
}

function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(onDismiss, 4500);
    return () => clearTimeout(t);
  }, [toast, onDismiss]);

  if (!toast) return null;
  return (
    <div className={`toast toast-${toast.tone || 'info'}`} onClick={onDismiss}>
      {toast.message}
    </div>
  );
}

function CategoryPills({ categories }) {
  if (!categories || !categories.length)
    return <span className="muted">—</span>;
  return (
    <span className="pill-row">
      {categories.map((c) => (
        <span className="pill" key={c}>
          {categoryLabel(c)}
        </span>
      ))}
    </span>
  );
}

function ExpiryChip({ iso }) {
  const days = daysUntil(iso);
  if (days === null) return <span className="muted">—</span>;
  if (days < 0) return <Badge tone="bad">Expired</Badge>;
  if (days <= 60) return <Badge tone="warn">{days}d left</Badge>;
  return <span>{formatDate(iso)}</span>;
}
