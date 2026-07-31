/* Fetch helpers. The project uses CSRF_USE_SESSIONS, so there is no csrftoken
   cookie — the token comes from the <meta name="csrf-token"> tag. */

function supplyCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

async function supplyRequest(url, options) {
  const opts = Object.assign(
    { credentials: 'same-origin', headers: {} },
    options || {},
  );
  opts.headers = Object.assign(
    { 'Content-Type': 'application/json', 'X-CSRFToken': supplyCsrfToken() },
    opts.headers,
  );
  const resp = await fetch(url, opts);
  let body = null;
  try {
    body = await resp.json();
  } catch (e) {
    body = null;
  }
  if (!resp.ok) {
    const message = (body && body.error) || `Request failed (${resp.status})`;
    const err = new Error(message);
    err.status = resp.status;
    throw err;
  }
  return body;
}

function supplyGet(url) {
  return supplyRequest(url, { method: 'GET' });
}

function supplyPost(url, payload) {
  return supplyRequest(url, {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  });
}

/* Category vocabulary, shared by every tab. */
const SUPPLY_CATEGORIES = [
  { key: 'rutf', label: 'RUTF' },
  { key: 'therapeutic_milk', label: 'Therapeutic milk' },
  { key: 'transport', label: 'Road transport' },
  { key: 'warehousing', label: 'Warehousing' },
];

function categoryLabel(key) {
  const found = SUPPLY_CATEGORIES.find((c) => c.key === key);
  return found ? found.label : key;
}

const COUNTRY_NAMES = {
  NG: 'Nigeria',
  SD: 'Sudan',
  ET: 'Ethiopia',
  BF: 'Burkina Faso',
  KE: 'Kenya',
  DJ: 'Djibouti',
  TG: 'Togo',
  GH: 'Ghana',
  FR: 'France',
  IN: 'India',
};

function countryLabel(code) {
  if (!code) return '—';
  return COUNTRY_NAMES[code] || code;
}

/* A date-only ISO string is a CALENDAR date, not an instant.

   `new Date('2026-09-15')` parses it as midnight UTC, and every render of it
   then goes through the viewer's timezone — so anywhere west of Greenwich the
   whole app read a day early. A delivery deadline stored as the 15th of
   September appeared on screen as Sep 14, and a lot due in 11 days counted 10.
   That is invisible in a UTC-hosted test and wrong on the machine of everyone
   watching a demo from the Americas.

   Date-only strings are therefore built in LOCAL time, so the calendar date
   that comes out is the one that went in. Strings carrying a time (a `T`) name
   a real instant and are left alone — converting those to local IS correct. */
function parseSupplyDate(iso) {
  if (!iso) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso));
  const d = dateOnly
    ? new Date(
        Number(dateOnly[1]),
        Number(dateOnly[2]) - 1,
        Number(dateOnly[3]),
      )
    : new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

function formatDate(iso) {
  if (!iso) return '—';
  const d = parseSupplyDate(iso);
  if (!d) return iso;
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function formatMoney(value, currency) {
  if (value === null || value === undefined) return '—';
  return `${currency || 'USD'} ${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/* "4 days ago" / "today" between two ISO dates, in whole calendar days.
   Used where a provenance stamp needs to be readable at a glance: an absolute
   date tells a reader when, a relative one tells them whether it is stale. */
function daysBetweenLabel(iso, asOfIso) {
  const then = parseSupplyDate(iso);
  const now = parseSupplyDate(asOfIso);
  if (!then || !now) return '';
  const days = Math.round((now - then) / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 0) return `in ${-days} days`;
  return `${days} days ago`;
}

function formatNumber(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString();
}

// Returns null for a missing/invalid date. Callers MUST null-check before
// comparing: `null <= 60` is true in JS, so an unguarded comparison silently
// treats "no expiry" as "expiring soon".
//
// Counted midnight to midnight in LOCAL time, so this is a difference in
// calendar days — the thing a reader means by "eleven days out". Subtracting
// two instants and rounding instead makes the answer depend on what time of
// day the page happened to be loaded.
function daysUntil(iso) {
  const then = parseSupplyDate(iso);
  if (!then) return null;
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((then - today) / (1000 * 60 * 60 * 24));
}

/* "Name · email", collapsing gracefully when either is missing. */
function contactLine(org) {
  const parts = [org.contact_name, org.contact_email].filter(Boolean);
  return parts.length ? parts.join(' · ') : '—';
}
