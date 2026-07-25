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

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
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

function formatNumber(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString();
}

function daysUntil(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (isNaN(then.getTime())) return null;
  return Math.ceil((then - new Date()) / (1000 * 60 * 60 * 24));
}
