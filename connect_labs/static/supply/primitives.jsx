/* Shared UI primitives — globals consumed by every tab_*.jsx file.
   KeyFigures is the OCHA-style stat row that also carries the Phase-3
   dashboards, so it lives here rather than in any one tab. */

const { useState, useEffect, useMemo, useCallback, useRef } = React;

function Page({ title, lede, actions, children }) {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          {lede ? <p className="page-lede">{lede}</p> : null}
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

function InfoNote({ label, text }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="infonote">
      <button
        type="button"
        className={`infonote-btn${open ? ' open' : ''}`}
        aria-label={`How ${label} is calculated`}
        aria-expanded={open}
        onClick={(ev) => {
          ev.stopPropagation();
          setOpen(!open);
        }}
      >
        i
      </button>
      {open ? <span className="infonote-body">{text}</span> : null}
    </span>
  );
}

const STATUS_TONES = {
  in_transit: 'info',
  planned: 'neutral',
  delivered: 'good',
  confirmed: 'accent',
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

function DataTable({ columns, rows, empty, emptyHint, rowKey, onRowClick }) {
  const [sort, setSort] = useState({ key: null, dir: 1 });

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
              className={onRowClick ? 'clickable' : ''}
            >
              {columns.map((col) => (
                <td key={col.key}>
                  {col.render ? col.render(row) : col.value(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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
