// Deterministic checks for the presentation defects the ACE demo judges kept
// finding on the generic indicator reports, run against a MOUNTED render (see
// indicator_render_guards.test.js). Each one used to be caught by an LLM judge
// looking at a screenshot, every time a new programme was built, because the
// fixes were made to per-programme template workflows and never reached the
// code templates new programmes start from. A check here holds the line for
// every programme at once.
//
// Pure functions over a DOM: they read classes, inline styles and SVG
// attributes, never getComputedStyle (happy-dom does not load the Tailwind
// CSS). Tailwind colours are resolved from the theme Tailwind actually ships
// (node_modules/tailwindcss/theme.css), so a palette change is measured, not
// assumed.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

// ── Colour ──────────────────────────────────────────────────────────────────

function oklchToRgb(L, C, H) {
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3,
    m = m_ ** 3,
    s = s_ ** 3;
  const lin = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return lin.map(function (c) {
    c = Math.min(1, Math.max(0, c));
    const g = c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
    return Math.round(g * 255);
  });
}

let PALETTE = null;
/** Tailwind's own palette: `gray-400` -> [r, g, b]. */
export function palette() {
  if (PALETTE) return PALETTE;
  const require = createRequire(import.meta.url);
  const css = readFileSync(require.resolve('tailwindcss/theme.css'), 'utf8');
  PALETTE = { white: [255, 255, 255], black: [0, 0, 0] };
  const re =
    /--color-([a-z]+-\d+):\s*oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\)/g;
  let m;
  while ((m = re.exec(css)))
    PALETTE[m[1]] = oklchToRgb(Number(m[2]) / 100, Number(m[3]), Number(m[4]));
  return PALETTE;
}

export function parseColour(v) {
  if (!v) return null;
  v = String(v).trim().toLowerCase();
  if (v === 'none' || v === 'transparent' || v === 'currentcolor') return null;
  let m = v.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/);
  if (m) {
    let hex = m[1];
    if (hex.length === 3)
      hex = hex
        .split('')
        .map((c) => c + c)
        .join('');
    return {
      rgb: [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)),
      a: 1,
    };
  }
  m = v.match(/^rgba?\(([^)]+)\)$/);
  if (m) {
    const p = m[1]
      .split(/[\s,/]+/)
      .filter(Boolean)
      .map(Number);
    return { rgb: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 };
  }
  if (v === 'white') return { rgb: [255, 255, 255], a: 1 };
  return null;
}

function twColour(cls, prefix) {
  // text-gray-500, bg-indigo-50/40, text-white
  const re = new RegExp(
    '^' + prefix + '-([a-z]+-\\d+|white|black)(?:/(\\d+))?$',
  );
  const m = cls.match(re);
  if (!m) return null;
  const rgb = palette()[m[1]];
  if (!rgb) return null;
  return { rgb, a: m[2] ? Number(m[2]) / 100 : 1 };
}

function over(top, under) {
  const a = top.a;
  return {
    rgb: top.rgb.map((c, i) => Math.round(c * a + under.rgb[i] * (1 - a))),
    a: 1,
  };
}

function luminance(rgb) {
  const [r, g, b] = rgb.map(function (c) {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(fg, bg) {
  const a = luminance(fg),
    b = luminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function classesOf(el) {
  const c = el.getAttribute && el.getAttribute('class');
  return c ? c.split(/\s+/).filter(Boolean) : [];
}

function styleOf(el) {
  return (el && el.style) || {};
}

function ownForeground(el) {
  const st = styleOf(el);
  if (st.color) return parseColour(st.color);
  if (el.namespaceURI === 'http://www.w3.org/2000/svg') {
    const f = el.getAttribute('fill');
    if (f) return parseColour(f);
  }
  for (const c of classesOf(el)) {
    const col = twColour(c, 'text');
    if (col) return col;
  }
  return null;
}

function ownBackground(el) {
  const st = styleOf(el);
  const inline = parseColour(st.backgroundColor || st.background);
  if (inline) return inline;
  for (const c of classesOf(el)) {
    const col = twColour(c, 'bg');
    if (col) return col;
  }
  return null;
}

function ownOpacity(el) {
  const st = styleOf(el);
  let o =
    st.opacity !== undefined && st.opacity !== '' ? Number(st.opacity) : 1;
  for (const c of classesOf(el)) {
    const m = c.match(/^opacity-(\d+)$/);
    if (m) o *= Number(m[1]) / 100;
  }
  return isNaN(o) ? 1 : o;
}

function fontPx(el) {
  for (let e = el; e && e.nodeType === 1; e = e.parentNode) {
    const st = styleOf(e);
    if (st.fontSize) return parseFloat(st.fontSize);
    const attr = e.getAttribute && e.getAttribute('font-size');
    if (attr) return parseFloat(attr);
    for (const c of classesOf(e)) {
      const m = c.match(/^text-\[(\d+(?:\.\d+)?)px\]$/);
      if (m) return Number(m[1]);
      const size = {
        'text-xs': 12,
        'text-sm': 14,
        'text-base': 16,
        'text-lg': 18,
        'text-xl': 20,
        'text-2xl': 24,
        'text-3xl': 30,
      }[c];
      if (size) return size;
    }
  }
  return 16;
}

function isBold(el) {
  for (let e = el; e && e.nodeType === 1; e = e.parentNode) {
    const st = styleOf(e);
    if (st.fontWeight) return Number(st.fontWeight) >= 700;
    const attr = e.getAttribute && e.getAttribute('font-weight');
    if (attr) return Number(attr) >= 700;
    const cls = classesOf(e);
    if (cls.includes('font-bold')) return true;
    if (cls.some((c) => /^font-(normal|medium|semibold)$/.test(c)))
      return false;
  }
  return false;
}

/**
 * Every visible text run whose contrast with what is behind it is below WCAG
 * 2.1 AA: 4.5:1, or 3:1 for large text (24px, or 18.66px bold). Text inside
 * <title> (a tooltip, drawn by the browser) and disabled controls (exempt in
 * WCAG) is not checked. Background is the nearest painted ancestor, composited
 * through translucent ones, over white.
 */
export function contrastFailures(root) {
  const out = [];
  const walker = root.ownerDocument.createTreeWalker(root, 4 /* TEXT */);
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const el = node.parentNode;
    let skip = false;
    for (let e = el; e && e.nodeType === 1; e = e.parentNode) {
      const tag = e.tagName.toLowerCase();
      if (tag === 'title' || tag === 'option' || tag === 'style') skip = true;
      if (e.hasAttribute('disabled')) skip = true;
      if (e.getAttribute('aria-hidden') === 'true') skip = true;
    }
    if (skip) continue;
    let fg = null;
    let opacity = 1;
    const bgs = [];
    for (let e = el; e && e.nodeType === 1; e = e.parentNode) {
      if (!fg) fg = ownForeground(e);
      opacity *= ownOpacity(e);
      const bg = ownBackground(e);
      if (bg) bgs.push(bg);
      if (bg && bg.a >= 1) break;
    }
    let bg = { rgb: [255, 255, 255], a: 1 };
    for (let i = bgs.length - 1; i >= 0; i--) bg = over(bgs[i], bg);
    fg = fg || { rgb: [17, 24, 39], a: 1 };
    fg = over({ rgb: fg.rgb, a: fg.a * opacity }, bg);
    const ratio = contrast(fg.rgb, bg.rgb);
    const px = fontPx(el);
    const large = px >= 24 || (px >= 18.66 && isBold(el));
    const need = large ? 3 : 4.5;
    if (ratio + 1e-9 < need)
      out.push({
        text: text.slice(0, 40),
        ratio: Math.round(ratio * 100) / 100,
        need,
        fg: 'rgb(' + fg.rgb.join(',') + ')',
        bg: 'rgb(' + bg.rgb.join(',') + ')',
      });
  }
  return out;
}

// ── Tables ──────────────────────────────────────────────────────────────────

function cellText(c) {
  return c.textContent.replace(/\s+/g, ' ').trim();
}

/**
 * Columns of a rendered <table> that read the same on every row (3+ rows):
 * the column says nothing per row, so the page should state the value once
 * and drop the column. A column whose header carries `data-constant-ok`
 * (a scorecard INDICATOR column: everyone on the same figure is a finding,
 * not a redundancy) is exempt.
 */
export function constantColumns(root) {
  const out = [];
  root.querySelectorAll('table').forEach(function (table) {
    const heads = table.querySelectorAll('thead tr');
    if (!heads.length) return;
    const head = heads[heads.length - 1];
    const ths = Array.from(head.children);
    const rows = Array.from(table.querySelectorAll('tbody > tr')).filter(
      function (tr) {
        return tr.children.length === ths.length;
      },
    );
    if (rows.length < 3) return;
    ths.forEach(function (th, i) {
      if (th.hasAttribute('data-constant-ok')) return;
      const vals = rows.map(function (tr) {
        return cellText(tr.children[i]);
      });
      if (vals[0] && vals.every((v) => v === vals[0]))
        out.push({ column: cellText(th) || '#' + i, value: vals[0] });
    });
  });
  return out;
}

// ── Status colour without a stated rule ─────────────────────────────────────

const BAND_TINTS = {
  yellow: /\bbg-amber-(50|100)\b/,
  red: /\bbg-red-(50|100)\b/,
};
const BAND_WORD = { yellow: 'Watch', red: 'Off target' };

function statedRule(root, word) {
  // A rule is the band's word next to a threshold: a comparator and a number,
  // in visible text or a hover title.
  const re = new RegExp(word, 'i');
  const rule = /[≥≤<>]\s*-?\d|\d%?\s*[–-]\s*\d/;
  const all = [root].concat(Array.from(root.querySelectorAll('*')));
  return all.some(function (e) {
    // On the chip itself: its title states the threshold, and the title or the
    // chip's own text names the band.
    const t = e.getAttribute && e.getAttribute('title');
    if (!t || !rule.test(t)) return false;
    return re.test(t) || re.test(cellText(e));
  });
}

/**
 * A Watch or Off-target colour on the page with no rule saying what puts a
 * figure there. "Watch" is a word the reader cannot decode without its band:
 * the judges flagged an unstated Watch-vs-Off-target rule on every page that
 * coloured a cell. Shown bands are read from cells tinted with the library's
 * band classes (format.ts CELL_TINT / BAND_TEXT) and from the band words.
 */
export function unstatedBandRules(root) {
  const out = [];
  Object.keys(BAND_TINTS).forEach(function (band) {
    const shown = Array.from(root.querySelectorAll('[class]')).some(
      function (e) {
        if (e.closest('[data-band-legend]')) return false;
        const t = cellText(e);
        return (
          BAND_TINTS[band].test(e.getAttribute('class')) &&
          (t === BAND_WORD[band] || /\d/.test(t))
        );
      },
    );
    if (shown && !statedRule(root, BAND_WORD[band]))
      out.push({ band: BAND_WORD[band] });
  });
  return out;
}

// ── Gap shading ─────────────────────────────────────────────────────────────

/**
 * Gap bands (`rect[data-gap]`) that cover a plotted visit (`circle`) in the
 * same chart. A gap is the time BETWEEN two visits; shading that runs through
 * a visit tells the reader nothing was observed when something was.
 */
export function gapsOverVisits(root) {
  const out = [];
  root.querySelectorAll('svg').forEach(function (svg) {
    const circles = Array.from(svg.querySelectorAll('circle')).map(
      function (c) {
        return Number(c.getAttribute('cx'));
      },
    );
    svg.querySelectorAll('rect[data-gap]').forEach(function (r) {
      const x0 = Number(r.getAttribute('x'));
      const x1 = x0 + Number(r.getAttribute('width'));
      circles.forEach(function (cx) {
        if (cx >= x0 && cx <= x1) out.push({ gap: [x0, x1], visitAt: cx });
      });
    });
  });
  return out;
}
