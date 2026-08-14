/* The opportunity dossier — renderer over /api/opp/.
 *
 * Same discipline as the display cards: plain DOM, no framework, everything
 * derived from one JSON payload. The page is a *document* about one
 * engagement, not a live telemetry surface — it fetches once and renders;
 * there is no polling loop to leak.
 */
(function () {
  'use strict';

  const CFG = window.PULSE_CONFIG || {};
  const nf = new Intl.NumberFormat('en-US');
  const money0 = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });

  function $(sel) {
    return document.querySelector(sel);
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function fmtDate(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  }

  function pct(x) {
    if (x == null) return '—';
    return (x * 100).toFixed(1) + '%';
  }

  /* ── header ─────────────────────────────────────────────────────── */

  function renderHead(d) {
    const chips = $('#opp-chips');
    chips.replaceChildren();
    const bits = [
      d.opp.service_name,
      d.opp.partner,
      d.opp.country_name,
      d.opp.program,
    ].filter(Boolean);
    for (const b of bits) chips.appendChild(el('span', 'opp-chip', b));

    const standing = $('#opp-standing');
    standing.replaceChildren();
    const state = el(
      'span',
      'opp-state ' + (d.opp.active ? 'is-active' : 'is-ended'),
      d.opp.active ? 'Active' : 'Ended',
    );
    standing.appendChild(state);
    if (d.opp.end_date) {
      standing.appendChild(
        el(
          'span',
          'opp-panel-note',
          (d.opp.active ? 'ends ' : 'ended ') + d.opp.end_date,
        ),
      );
    }
  }

  /* ── KPI band ───────────────────────────────────────────────────── */

  function kpi(label, value, note) {
    const cell = el('div', 'opp-kpi');
    cell.appendChild(el('span', 'pulse-lbl', label));
    cell.appendChild(el('span', 'opp-kpi-v num', value));
    if (note) cell.appendChild(el('span', 'opp-kpi-note', note));
    return cell;
  }

  function renderKpis(d) {
    const t = d.totals;
    const m = d.money;
    const root = $('#opp-kpis');
    root.replaceChildren(
      kpi('Services delivered', nf.format(t.events), 'full history'),
      kpi(
        'Verified',
        nf.format(m.approved),
        m.works ? pct(m.approved / m.works) + ' of work claimed' : '',
      ),
      kpi('Paid to workers', money0.format(m.usd_workers), ''),
      kpi('To the organisation', money0.format(m.usd_org), ''),
      kpi('Workers', nf.format(t.workers), 'distinct, all-time'),
      kpi(
        'Cost per verified',
        m.rate != null ? '$' + m.rate.toFixed(2) : '—',
        'workers + delivery org',
      ),
    );
  }

  /* ── delivery rhythm — the full-history weekly strip ────────────── */

  function renderRhythm(d) {
    const root = $('#rhythm');
    root.replaceChildren();
    const weeks = d.weekly || [];
    if (!weeks.length) {
      root.appendChild(
        el(
          'p',
          'opp-empty',
          'No delivery has been recorded for this opportunity yet.',
        ),
      );
      return;
    }

    const W = 1160;
    const H = 190;
    const PAD_B = 22;
    const max = Math.max(...weeks.map((w) => w.n), 1);
    const bw = W / weeks.length;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');
    svg.setAttribute(
      'aria-label',
      `Services delivered per week from ${fmtDate(weeks[0].t)} to ${fmtDate(
        weeks[weeks.length - 1].t,
      )}`,
    );

    const ns = 'http://www.w3.org/2000/svg';
    // Year ticks: the strip can span years, and unlabelled time is a guess.
    let lastYear = null;
    weeks.forEach((w, i) => {
      const yr = new Date(w.t * 1000).getFullYear();
      if (yr !== lastYear) {
        lastYear = yr;
        const x = i * bw;
        const line = document.createElementNS(ns, 'line');
        line.setAttribute('x1', x);
        line.setAttribute('x2', x);
        line.setAttribute('y1', 0);
        line.setAttribute('y2', H - PAD_B);
        line.setAttribute('class', 'rh-year');
        svg.appendChild(line);
        const label = document.createElementNS(ns, 'text');
        label.setAttribute('x', x + 4);
        label.setAttribute('y', H - 8);
        label.setAttribute('class', 'rh-year-lbl');
        label.textContent = String(yr);
        svg.appendChild(label);
      }
    });

    const gap = bw > 3 ? 1 : 0; // 1px spacer between bars while there is room
    weeks.forEach((w, i) => {
      const x = i * bw;
      const hTotal = ((H - PAD_B) * w.n) / max;
      const hApproved = ((H - PAD_B) * w.approved) / max;

      const total = document.createElementNS(ns, 'rect');
      total.setAttribute('x', x);
      total.setAttribute('y', H - PAD_B - hTotal);
      total.setAttribute('width', Math.max(bw - gap, 0.5));
      total.setAttribute('height', Math.max(hTotal, w.n ? 1 : 0));
      total.setAttribute('class', 'rh-bar');
      svg.appendChild(total);

      const ok = document.createElementNS(ns, 'rect');
      ok.setAttribute('x', x);
      ok.setAttribute('y', H - PAD_B - hApproved);
      ok.setAttribute('width', Math.max(bw - gap, 0.5));
      ok.setAttribute('height', Math.max(hApproved, w.approved ? 1 : 0));
      ok.setAttribute('class', 'rh-bar-ok');
      svg.appendChild(ok);
    });

    root.appendChild(svg);

    // Hover: one tooltip, driven by pointer position over the whole strip so
    // the hit target is the chart, not a 2px bar.
    const tip = el('div', 'opp-tip');
    tip.hidden = true;
    root.appendChild(tip);
    root.addEventListener('pointermove', (evt) => {
      const box = svg.getBoundingClientRect();
      const i = Math.min(
        weeks.length - 1,
        Math.max(
          0,
          Math.floor(((evt.clientX - box.left) / box.width) * weeks.length),
        ),
      );
      const w = weeks[i];
      tip.textContent = `${fmtDate(w.t)} — ${nf.format(
        w.n,
      )} services, ${nf.format(w.approved)} approved`;
      tip.hidden = false;
      const x = Math.min(evt.clientX - box.left, box.width - 230);
      tip.style.left = Math.max(x, 0) + 'px';
    });
    root.addEventListener('pointerleave', () => {
      tip.hidden = true;
    });

    const peak = weeks.reduce((a, b) => (b.n > a.n ? b : a));
    $('#rhythm-note').textContent = `${weeks.length} weeks · peak ${nf.format(
      peak.n,
    )} in the week of ${fmtDate(peak.t)}`;
  }

  /* ── verification ───────────────────────────────────────────────── */

  const STATUS_LABELS = {
    approved: 'Approved',
    pending: 'Awaiting review',
    rejected: 'Rejected',
    over_limit: 'Over limit',
    duplicate: 'Duplicate',
    trial: 'Trial',
  };

  // `display` is the printed figure -- a count with a share, a currency
  // amount, whatever reads best; the track only needs n/total for width.
  function bar(label, n, total, cls, display) {
    const row = el('div', 'opp-bar-row');
    const head = el('div', 'opp-bar-head');
    head.appendChild(el('span', 'opp-bar-lbl', label));
    head.appendChild(el('span', 'opp-bar-n num', display));
    row.appendChild(head);
    const track = el('div', 'opp-bar-track');
    const fill = el('div', 'opp-bar-fill ' + cls);
    fill.style.width = total
      ? Math.max((n / total) * 100, n ? 0.75 : 0) + '%'
      : '0%';
    track.appendChild(fill);
    row.appendChild(track);
    return row;
  }

  function renderVerification(d) {
    const root = $('#verification');
    root.replaceChildren();
    const total = d.totals.events || 0;
    const entries = Object.entries(d.statuses || {});
    if (!entries.length) {
      root.appendChild(
        el(
          'p',
          'opp-empty',
          'No visit-level records held for this opportunity.',
        ),
      );
      return;
    }
    const CLS = { approved: 'f-ok', rejected: 'f-crit', pending: 'f-idle' };
    for (const [status, n] of entries) {
      root.appendChild(
        bar(
          STATUS_LABELS[status] || status,
          n,
          total,
          CLS[status] || 'f-idle',
          nf.format(n) + ' · ' + pct(total ? n / total : null),
        ),
      );
    }
    if (d.totals.flagged) {
      const flags = Object.values(d.flags || {});
      const detail = flags.length
        ? flags.map((f) => `${f.label} ${nf.format(f.n)}`).join(' · ')
        : null;
      root.appendChild(
        bar(
          'Flagged for review',
          d.totals.flagged,
          total,
          'f-warn',
          nf.format(d.totals.flagged) + ' · ' + pct(d.totals.flag_rate),
        ),
      );
      if (detail) root.appendChild(el('p', 'opp-flag-detail', detail));
    }
  }

  /* ── money ──────────────────────────────────────────────────────── */

  function renderMoney(d) {
    const m = d.money;
    const root = $('#money');
    root.replaceChildren();
    if (!m.works) {
      root.appendChild(
        el(
          'p',
          'opp-empty',
          'No payment units have been recorded for this opportunity.',
        ),
      );
      return;
    }
    root.appendChild(
      bar(
        'Earned by frontline workers',
        m.usd_workers,
        m.usd_total,
        'f-light',
        money0.format(m.usd_workers),
      ),
    );
    root.appendChild(
      bar(
        'Accrued to the delivery organisation',
        m.usd_org,
        m.usd_total,
        'f-c2',
        money0.format(m.usd_org),
      ),
    );
    const note = el('p', 'opp-money-note');
    note.textContent =
      `${nf.format(m.approved)} of ${nf.format(
        m.works,
      )} claimed units approved` +
      (m.rate != null
        ? ` · ${
            '$' + m.rate.toFixed(2)
          } per verified service, both sides included`
        : '');
    root.appendChild(note);
  }

  /* ── workers ────────────────────────────────────────────────────── */

  function renderWorkers(d) {
    const root = $('#workers');
    root.replaceChildren();
    const rows = d.workers || [];
    $('#workers-note').textContent = d.totals.workers
      ? `${nf.format(d.totals.workers)} all-time · top ${rows.length} by volume`
      : '';
    if (!rows.length) {
      root.appendChild(
        el('p', 'opp-empty', 'No worker activity held for this opportunity.'),
      );
      return;
    }
    const max = Math.max(...rows.map((r) => r.events), 1);
    const table = el('div', 'opp-workers');
    for (const r of rows) {
      const row = el('div', 'opp-worker-row');
      row.appendChild(el('span', 'opp-worker-id', r.w));
      const track = el('div', 'opp-bar-track');
      const fill = el('div', 'opp-bar-fill f-light');
      fill.style.width = (r.events / max) * 100 + '%';
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el('span', 'opp-worker-n num', nf.format(r.events)));
      row.appendChild(
        el('span', 'opp-worker-usd num', r.usd ? money0.format(r.usd) : '—'),
      );
      row.appendChild(el('span', 'opp-worker-ts', fmtDate(r.last_ts)));
      table.appendChild(row);
    }
    const head = el('div', 'opp-worker-row opp-worker-head');
    head.appendChild(el('span', 'pulse-lbl', 'Worker'));
    head.appendChild(el('span', 'pulse-lbl', ''));
    head.appendChild(el('span', 'pulse-lbl', 'Services'));
    head.appendChild(el('span', 'pulse-lbl', 'Earned'));
    head.appendChild(el('span', 'pulse-lbl', 'Last seen'));
    table.prepend(head);
    root.appendChild(table);
  }

  /* ── map ────────────────────────────────────────────────────────── */

  function renderMap(d) {
    const host = $('#opp-map');
    const points = d.points || [];
    if (
      !window.ConnectMap ||
      !window.mapboxgl ||
      !window.MAPBOX_TOKEN ||
      !points.length
    ) {
      host.replaceChildren(
        el('p', 'opp-empty', 'No mappable deliveries for this opportunity.'),
      );
      return;
    }
    const lats = points.map((p) => p[0]);
    const lons = points.map((p) => p[1]);
    const map = window.ConnectMap.createMap(host, {
      center: [
        (Math.min(...lons) + Math.max(...lons)) / 2,
        (Math.min(...lats) + Math.max(...lats)) / 2,
      ],
      zoom: 5,
      interactive: true,
    });
    map.on('load', () => {
      try {
        for (const layer of map.getStyle().layers) {
          if (layer.type !== 'symbol' || layer.source !== 'composite') continue;
          if (/poi|road|transit|airport/.test(layer.id)) {
            map.setLayoutProperty(layer.id, 'visibility', 'none');
            continue;
          }
          map.setPaintProperty(layer.id, 'text-opacity', 0.4);
        }
      } catch (err) {
        /* basemap dimming is cosmetic */
      }
      const maxN = Math.max(...points.map((p) => p[2]), 1);
      map.addSource('opp-points', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: points.map((p) => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [p[1], p[0]] },
            properties: { n: p[2] },
          })),
        },
      });
      map.addLayer({
        id: 'opp-points',
        type: 'circle',
        source: 'opp-points',
        paint: {
          'circle-color': '#feaf31',
          'circle-opacity': 0.55,
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['sqrt', ['get', 'n']],
            0,
            1.5,
            Math.sqrt(maxN),
            11,
          ],
          'circle-blur': 0.35,
        },
      });
      const b = [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ];
      map.fitBounds(b, { padding: 42, maxZoom: 9, duration: 0 });
    });
  }

  /* ── footer ─────────────────────────────────────────────────────── */

  function renderFoot(d) {
    const t = d.totals;
    const bits = [];
    if (t.first_ts) bits.push(`first delivery ${fmtDate(t.first_ts)}`);
    if (t.last_ts) bits.push(`most recent ${fmtDate(t.last_ts)}`);
    if (d.opp.lifetime_visits && t.events < d.opp.lifetime_visits) {
      bits.push(
        `${nf.format(
          d.opp.lifetime_visits - t.events,
        )} of Connect's lifetime count not yet held locally`,
      );
    }
    $('#opp-foot').textContent = bits.join(' · ');
  }

  /* ── boot ───────────────────────────────────────────────────────── */

  async function boot() {
    let data;
    try {
      const res = await fetch(`${CFG.base}/api/opp/?id=${CFG.oppId}`, {
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (err) {
      console.error('[pulse] opp dossier failed', err);
      $('#opp-kpis').replaceChildren(
        el(
          'p',
          'opp-empty',
          'Could not load this opportunity. Reload to try again.',
        ),
      );
      return;
    }
    renderHead(data);
    renderKpis(data);
    renderRhythm(data);
    renderVerification(data);
    renderMoney(data);
    renderWorkers(data);
    renderMap(data);
    renderFoot(data);
  }

  boot();
})();
