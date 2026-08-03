/**
 * Pulse display — wires the store to the map, the cards and the acts.
 *
 * The map has no basemap. Its geography is the accumulated grid of where work
 * has actually happened, with live events igniting on top. Nothing here draws
 * a coastline: the shape of Nigeria appears because 1.5 million services were
 * delivered there.
 */
(function () {
  'use strict';

  const CFG = window.PULSE_CONFIG || {};
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const { nf, usd, usdCompact } = window.PulseCards.helpers;

  const store = new window.PulseStore({
    base: CFG.base,
    mode: 'replay',
    speed: 240,
    token: CFG.token || null,
  });

  /* ═══ map ═══════════════════════════════════════════════════════
     A real Mapbox dark basemap underneath (coastlines, country borders,
     place names) with our density + ignition layer painted on a transparent
     canvas above it. Projection comes from the map, so the overlay stays
     locked to the basemap through every pan and zoom.                     */
  const cv = $('#sky'),
    cx = cv.getContext('2d');

  /* [west, south, east, north] */
  const FOCI = {
    world: [-17, -15, 92, 33],
    ng: [2.5, 4.0, 14.8, 14.2],
    ea: [28.5, -6.5, 41.5, 4.8],
    in: [68, 8, 90, 30],
  };
  let focus = 'world';
  let W = 0,
    H = 0,
    dpr = 1,
    baseLayer = null,
    baseDirty = true;
  let cells = [];
  let map = null;

  function size() {
    dpr = Math.min(devicePixelRatio || 1, 2);
    const r = cv.getBoundingClientRect();
    W = r.width;
    H = r.height;
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    baseDirty = true;
  }

  /* Project through the basemap so overlay and basemap can never disagree.
   *
   * Falls back to a plain equirectangular fit over the current focus bounds
   * when Mapbox is unavailable (no token, blocked CDN, offline). Without this
   * the whole map goes blank on a missing token rather than degrading to the
   * density layer -- a silent blackout in exactly the situation where someone
   * is least able to diagnose it. */
  function proj(lat, lon) {
    if (map) {
      const pt = map.project([lon, lat]);
      return [pt.x, pt.y];
    }
    const [w, s2, e, n] = FOCI[focus];
    const lonSpan = e - w,
      latSpan = n - s2;
    const boxAR = W / H,
      dataAR = lonSpan / latSpan;
    let sx,
      sy,
      ox = 0,
      oy = 0;
    if (dataAR > boxAR) {
      sx = W / lonSpan;
      sy = sx;
      oy = (H - latSpan * sy) / 2;
    } else {
      sy = H / latSpan;
      sx = sy;
      ox = (W - lonSpan * sx) / 2;
    }
    return [ox + (lon - w) * sx, oy + (n - lat) * sy];
  }

  /**
   * The density layer. Two passes — a wide soft bloom so clusters read as
   * inhabited area, then a tight core so settlements stay countable when
   * zoomed. Both radius and alpha are hard-capped: under `lighter` blending a
   * dense cluster otherwise saturates to a white blob and destroys the
   * structure that is the whole reason to zoom in.
   *
   * Rebuilt only when the view actually changed — on every frame it would
   * cost more than the ignitions it sits under.
   */
  function drawBase() {
    if (!W) return;
    const off = document.createElement('canvas');
    off.width = cv.width;
    off.height = cv.height;
    const c = off.getContext('2d');
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.globalCompositeOperation = 'lighter';

    const zoom = map
      ? Math.min(Math.max(map.getZoom() / 4, 0.6), 1.9)
      : Math.min(
          Math.max(W / (FOCI[focus][2] - FOCI[focus][0]) / 12, 0.6),
          1.7,
        );

    for (const cell of cells) {
      const [x, y] = proj(cell.lat, cell.lon);
      if (x < -40 || x > W + 40 || y < -40 || y > H + 40) continue;
      const r = Math.min((2.2 + Math.log1p(cell.n) * 1.0) * zoom, 14);
      const a = Math.min(0.03 + Math.log1p(cell.n) * 0.018, 0.11);
      const g = c.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(254,175,49,${a})`);
      g.addColorStop(1, 'rgba(254,175,49,0)');
      c.fillStyle = g;
      c.beginPath();
      c.arc(x, y, r, 0, 6.2832);
      c.fill();
    }
    for (const cell of cells) {
      const [x, y] = proj(cell.lat, cell.lon);
      if (x < -20 || x > W + 20 || y < -20 || y > H + 20) continue;
      const r = Math.max(
        0.6,
        Math.min((0.6 + Math.log1p(cell.n) * 0.26) * zoom, 2.6),
      );
      const a = Math.min(0.3 + Math.log1p(cell.n) * 0.08, 0.72);
      c.fillStyle = `rgba(255,214,140,${a})`;
      c.beginPath();
      c.arc(x, y, r, 0, 6.2832);
      c.fill();
    }
    baseLayer = off;
    baseDirty = false;
  }

  /* live ignitions */
  const sparks = [];
  const COL = {
    approved: [255, 206, 110],
    over_limit: [150, 160, 185],
    pending: [150, 160, 185],
    incomplete: [150, 160, 185],
    rejected: [214, 110, 100],
    duplicate: [214, 110, 100],
  };
  function ignite(ev) {
    if (ev.lat == null) return;
    sparks.push({
      la: ev.lat,
      lo: ev.lon,
      t: 0,
      col: ev.flag_type ? [222, 168, 62] : COL[ev.status] || COL.approved,
    });
    if (sparks.length > 260) sparks.splice(0, sparks.length - 260);
  }

  let lastPaint = 0;
  function paint(ts) {
    requestAnimationFrame(paint);
    const dt = Math.min((ts - lastPaint) / 1000, 0.1);
    lastPaint = ts;

    if (baseDirty) drawBase();

    cx.clearRect(0, 0, W, H);

    /* The basemap already supplies coastlines, borders and place names, so the
       overlay draws only what the basemap cannot: accumulated density and the
       services arriving right now. */
    if (baseLayer) {
      cx.save();
      cx.setTransform(1, 0, 0, 1, 0, 0);
      cx.drawImage(baseLayer, 0, 0);
      cx.restore();
    }

    cx.globalCompositeOperation = 'lighter';
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s2 = sparks[i];
      s2.t += dt;
      const life = 3.4;
      if (s2.t > life) {
        sparks.splice(i, 1);
        continue;
      }
      const p = s2.t / life,
        [r, g, b] = s2.col;
      const [x, y] = proj(s2.la, s2.lo);
      if (x < -40 || x > W + 40 || y < -40 || y > H + 40) continue;
      if (p < 0.55 && !reduced) {
        const rr = 3 + p * 34;
        cx.strokeStyle = `rgba(${r},${g},${b},${(1 - p / 0.55) * 0.5})`;
        cx.lineWidth = 1.4;
        cx.beginPath();
        cx.arc(x, y, rr, 0, 6.2832);
        cx.stroke();
      }
      const a = 1 - p * 0.82;
      const gr = cx.createRadialGradient(x, y, 0, x, y, 11);
      gr.addColorStop(0, `rgba(${r},${g},${b},${a})`);
      gr.addColorStop(0.35, `rgba(${r},${g},${b},${a * 0.34})`);
      gr.addColorStop(1, `rgba(${r},${g},${b},0)`);
      cx.fillStyle = gr;
      cx.beginPath();
      cx.arc(x, y, 11, 0, 6.2832);
      cx.fill();
      cx.fillStyle = `rgba(255,246,222,${a})`;
      cx.beginPath();
      cx.arc(x, y, 1.7, 0, 6.2832);
      cx.fill();
    }
    cx.globalCompositeOperation = 'source-over';

    // A window is modal — the tour must not keep swapping cards behind it.
    if (!(window.PulseWindows && window.PulseWindows.isOpen()))
      Partners.tick(ts, ts - lastInteractionAt);
    Brush.tick();
  }

  function initBasemap() {
    if (!window.ConnectMap || !window.mapboxgl || !window.MAPBOX_TOKEN) {
      console.warn(
        '[pulse] no Mapbox token or ConnectMap — density layer only',
      );
      return null;
    }
    const m = window.ConnectMap.createMap($('#basemap'), {
      center: [20, 8],
      zoom: 2.2,
      interactive: true,
    });
    // Any view change invalidates the projected density layer.
    m.on('move', () => {
      baseDirty = true;
      Partners.reposition();
    });
    m.on('zoom', () => {
      baseDirty = true;
      Partners.reposition();
    });
    m.on('resize', () => {
      size();
    });
    m.on('load', () => {
      baseDirty = true;
      setFocus(focus, true);
    });
    return m;
  }

  /* ═══ layouts ═══════════════════════════════════════════════════
     Same card library, same store — the layout only chooses which acts
     appear, in what order, and what the map is doing while they do. That
     is the whole point of splitting cards from composition: a new option
     is a config entry, not a rewrite.                                    */
  const LAYOUTS = {
    /* The pulse first: something is happening right now, in a real place. */
    nightmap: [
      {
        card: 'verification',
        eyebrow: 'The pulse',
        title: 'Every point of light is a service someone actually received.',
        note: "Positions are the worker's GPS at the moment of delivery. Nothing here is modelled.",
        focus: 'world',
      },
      {
        card: 'money',
        eyebrow: 'The cost',
        title:
          'The money reaches both sides of delivery — the worker and the local organisation.',
        note: "Measured from approved work, not budgeted — paid to a worker's phone and to the organisation running the programme, with no sub-grantee chain in between.",
        focus: 'ng',
      },
      {
        card: 'reach',
        eyebrow: 'The scale',
        title:
          'Local organisations, running their own programmes on shared rails.',
        note: 'Dimagi operates none of these. Every one is delivered by a local partner.',
        focus: 'world',
      },
      {
        card: 'offline',
        eyebrow: 'The tail',
        title: "The work happens where the signal doesn't.",
        note: 'Submissions arrive minutes — sometimes days — after the service was delivered.',
        focus: 'ea',
      },
    ],

    /* Money first, and the argument is unit economics rather than motion. */
    financial: [
      {
        card: 'money',
        eyebrow: 'The ledger',
        /* A figure in an act title must come from the payload the panels below
           it are drawn from. This one was hardcoded, and by the time anyone
           looked it read $663,682 against a live $478,490 -- 39% high, on the
           same screen as two correct copies of the real number. A stale
           constant cannot be spotted by eye, so titles quoting a number are
           functions of the summary and unquote themselves when it is absent. */
        title: (s) =>
          s?.money?.total_paid || s?.money?.to_workers
            ? `${usd(
                s.money.total_paid || s.money.to_workers,
                0,
              )} has gone out through verified delivery — to workers and the organisations they deliver for.`
            : 'Money goes out through verified delivery — to workers and the organisations they deliver for.',
        note: 'Every dollar here was earned by a specific unit of approved work — not allocated, not budgeted.',
        focus: 'world',
      },
      {
        card: 'unitecon',
        eyebrow: 'Unit economics',
        title:
          'A verified service costs a couple of dollars. The spread is the point.',
        note: 'A reading check and a Kangaroo Mother Care follow-up are not the same job, and the price says so.',
        focus: 'ng',
      },
      {
        card: 'verification',
        eyebrow: 'What we refuse to pay for',
        title: 'Money moves only after the work survives the checks.',
        note: 'The rejected and over-cap slices are spend that did not happen because the platform caught it.',
        focus: 'world',
      },
      {
        card: 'reach',
        eyebrow: 'The portfolio',
        title: 'Eight countries, run by local organisations on shared rails.',
        note: 'Concentration and spread both matter to a funder: this is where the money actually lands.',
        focus: 'ea',
      },
    ],

    /* Everything at once, cycling fast — the wall-display read. */
    mission: [
      {
        card: 'verification',
        eyebrow: 'Verification',
        title: 'Every submission, checked before it is paid.',
        note: 'Automated checks, then a human on anything flagged.',
        focus: 'ng',
      },
      {
        card: 'money',
        eyebrow: 'Money',
        title: 'Earned by workers, accrued to organisations.',
        note: 'Measured from approved work across the whole portfolio.',
        focus: 'world',
      },
      {
        card: 'unitecon',
        eyebrow: 'Unit economics',
        title: 'What a verified service costs.',
        note: 'Per programme, measured — not a blended headline.',
        focus: 'ng',
      },
      {
        card: 'reach',
        eyebrow: 'Reach',
        title: 'Where the work is.',
        note: 'Opportunities, programmes and countries currently on the platform.',
        focus: 'world',
      },
      {
        card: 'offline',
        eyebrow: 'Sync',
        title: 'Field time versus server time.',
        note: 'The tail is offline-first working as designed.',
        focus: 'ea',
      },
    ],
  };

  const ACTS = LAYOUTS[CFG.layout] || LAYOUTS.nightmap;
  const CYCLE_MS = CFG.layout === 'mission' ? 12000 : 24000;

  let act = 0,
    autoCycle = true;
  const actBody = document.createElement('div');
  actBody.className = 'act-body';

  function buildActPanel() {
    const panel = $('#act');
    panel.innerHTML = '';
    const head = document.createElement('div');
    head.className = 'act-head';
    head.innerHTML = `<span class="pulse-lbl" id="act-label">—</span>
      <div class="act-nav" role="group" aria-label="Act">
        ${ACTS.map(
          (_, i) =>
            `<button data-act="${i}" aria-pressed="${i === 0}">${
              i + 1
            }</button>`,
        ).join('')}
      </div>`;
    panel.appendChild(head);
    panel.appendChild(actBody);
    head
      .querySelectorAll('[data-act]')
      .forEach((b) =>
        b.addEventListener('click', () => setAct(+b.dataset.act, true)),
      );
  }

  /* Titles may be a function of the summary so a quoted figure tracks the
     data. Repainted on every summary, not just on act change -- otherwise the
     number freezes at whatever it was when the act opened. */
  function paintActTitle() {
    const a = ACTS[act];
    if (!a) return;
    $('#act-title').textContent =
      typeof a.title === 'function' ? a.title(store.summary) : a.title;
  }

  function setAct(i, manual) {
    act = (i + ACTS.length) % ACTS.length;
    const a = ACTS[act];
    const card = window.PulseCards.CARDS[a.card];
    $('#act-label').textContent = card ? card.label : a.eyebrow;
    $('#act-eyebrow').textContent = a.eyebrow;
    paintActTitle();
    $('#act-note').textContent = a.note;
    $$('#act .act-nav button').forEach((b) =>
      b.setAttribute('aria-pressed', +b.dataset.act === act),
    );

    actBody.innerHTML = '';
    if (card) {
      try {
        card.mount(actBody, store);
      } catch (err) {
        console.error('[pulse] card failed', a.card, err);
      }
    }
    setFocus(a.focus);
    if (manual) autoCycle = false;
  }

  function setFocus(k, immediate) {
    focus = k;
    $$('.pulse-focus button').forEach((b) =>
      b.setAttribute('aria-pressed', b.dataset.focus === k),
    );
    if (!map) {
      baseDirty = true;
      return;
    }
    const [w, s2, e, n] = FOCI[k];
    map.fitBounds(
      [
        [w, s2],
        [e, n],
      ],
      { padding: 40, duration: immediate ? 0 : 1600 },
    );
  }

  /* Speeds pace replay only — in live mode the clock is wall time and there is
     nothing to speed up, so they are hidden rather than left there doing
     nothing. */
  function paintTransport() {
    const live = store.mode === 'live';
    $('#btn-mode').textContent = live ? 'Replay' : 'Go live';
    $('#btn-mode').title = live
      ? 'Switch back to the replay window'
      : 'Follow services as they arrive';
    const speeds = $('#speed-controls');
    if (speeds) speeds.hidden = live;
  }

  /* ═══ status bar ════════════════════════════════════════════════ */
  function paintStatus() {
    const mode = $('#mode'),
      text = $('#mode-text'),
      alert = $('#alert');
    const ing = store.ingest || {};

    if (store.mode === 'live') {
      const ok = !!ing.live_ok;
      mode.dataset.state = ok ? 'live' : 'stale';
      text.textContent = ok ? 'Live' : 'Not live';
    } else {
      mode.dataset.state = 'replay';
      const when = store.clock
        ? new Date(store.clock * 1000)
            .toISOString()
            .slice(5, 16)
            .replace('T', ' ')
        : '';
      text.textContent = 'Replay' + (when ? ' · ' + when + ' UTC' : '');
    }

    // The server decides honesty; the page only reports it.
    if (ing.message) {
      alert.hidden = false;
      $('#alert-text').textContent = ing.message;
    } else {
      alert.hidden = true;
    }
  }

  store.on('ingest', paintStatus);
  store.on('clock', paintStatus);
  store.on('control', paintStatus);

  let menuBuilt = false;
  let orgMenuBuilt = false;
  let svcMenuBuilt = false;
  store.on('summary', (s) => {
    const sel = $('#prog-filter');
    // Built once: the menu is the same list under every filter, and rebuilding
    // it inside its own change handler would reset the control mid-interaction.
    if (sel && !menuBuilt && Array.isArray(s.programs)) {
      for (const p of s.programs) {
        const o = document.createElement('option');
        o.value = String(p.id);
        // Connect's own programme name, verbatim. Nothing invented in labs.
        // Say which programmes are dormant in the menu, rather than letting
        // someone select one and be shown an empty map with no stated reason.
        o.textContent = p.recent_events
          ? p.name
          : `${p.name} — no recent delivery`;
        o.title = `${p.service_label} · ${nf.format(
          p.visits,
        )} services all-time · ${
          p.recent_events
            ? nf.format(p.recent_events) + ' in the last 30 days'
            : 'none in the last 30 days'
        }`;
        sel.appendChild(o);
      }
      menuBuilt = true;
      if (store.program) sel.value = String(store.program);
    }
    const svcSel = $('#svc-filter');
    if (svcSel && !svcMenuBuilt && Array.isArray(s.services)) {
      for (const v of s.services) {
        const opt = document.createElement('option');
        opt.value = v.slug;
        // Same "no recent delivery" wording as the other menus, so the phrase
        // means one thing across all three controls.
        opt.textContent = v.recent_events
          ? v.name
          : v.name + ' \u2014 no recent delivery';
        opt.title =
          nf.format(v.visits) +
          ' services all-time \u00b7 ' +
          nf.format(v.opportunities) +
          ' opportunities';
        svcSel.appendChild(opt);
      }
      svcMenuBuilt = true;
      if (store.service) svcSel.value = store.service;
    }

    const orgSel = $('#org-filter');
    const orgWrap = $('#org-filter-wrap');
    if (orgSel && orgWrap && !orgMenuBuilt && Array.isArray(s.orgs)) {
      // An empty list means this caller may not name partners (an anonymised
      // public link). Hide the control rather than offering a menu with nothing
      // in it, which reads as a broken filter instead of a withheld one.
      if (!s.orgs.length) {
        orgWrap.hidden = true;
      } else {
        orgWrap.hidden = false;
        for (const o of s.orgs) {
          const opt = document.createElement('option');
          opt.value = o.slug;
          // Connect's own partner name, verbatim. Say which partners are
          // dormant rather than letting someone pick one and get a blank map.
          const label = o.partner
            ? o.partner === o.name
              ? o.partner
              : `${o.partner} · ${o.name}`
            : o.named === false
            ? `${o.name} (identifier)`
            : o.name;
          opt.textContent = o.recent_events
            ? label
            : `${label} — no recent delivery`;
          opt.title = `${nf.format(o.visits)} services all-time · ${nf.format(
            o.opportunities,
          )} opportunities${o.funder ? ' · funded by ' + o.funder : ''}`;
          orgSel.appendChild(opt);
        }
        orgMenuBuilt = true;
        if (store.org) orgSel.value = store.org;
      }
    }

    const scope = s.scope || {};
    $('#s-opp').textContent = nf.format(scope.opportunities || 0);
    $('#s-prog').textContent = nf.format(scope.programs || 0);
    $('#s-cty').textContent = (s.money?.by_country || []).length || '—';
    paintActTitle();
    Brush.paint(s);
  });

  store.on('event', ignite);
  store.on('backfill', () => {
    sparks.length = 0;
  });

  /* ═══ grid (the map's geography) ════════════════════════════════ */
  async function loadGrid() {
    try {
      // Built by the store, not by hand: this fetch has to carry whatever the
      // store is filtered to. A local copy of the query string silently missed
      // the partner filter and left the whole estate's geography under one
      // partner's points -- the same defect the programme filter already fixed.
      const res = await fetch(store._url('/api/grid/', { limit: '40000' }));
      if (!res.ok) throw new Error(res.status);
      const payload = await res.json();
      const q = payload.quantum || 100;
      cells = payload.cells.map((r) => ({
        lat: r[0] / q,
        lon: r[1] / q,
        n: r[2],
        country: r[5],
      }));
      drawBase();
      // Held on the store so a card mounted later can still paint from it.
      store.lastGrid = payload;
      store.emit('grid', payload);
    } catch (err) {
      console.error('[pulse] grid load failed', err);
    }
  }

  /* ═══ partner cards ═════════════════════════════════════════════
     A partner dossier that belongs to a PLACE: positioned over the map at the
     partner's own geography and tethered there, so "who delivers here" is
     answered spatially instead of in a table somewhere else on screen.

     Every card is drawn from the partner's row in the summary payload, which
     already carries its money, approval rate and 26-week series. That is
     deliberate — the card appears on hover, and fetching per partner would put
     a network request behind a mouse movement.                              */
  const Partners = (() => {
    const layer = $('.pulse-map');
    let card = null;
    let tether = null;
    let pinned = null; // slug the user asked to keep up
    let showing = null; // slug currently drawn
    let cycleAt = 0;

    const rows = () => (store.summary && store.summary.orgs) || [];
    const row = (slug) => rows().find((o) => o.slug === slug) || null;

    /* Where a partner is, in lat/lon.
     *
     * Their density cells are the honest answer, but the loaded cells are only
     * this partner's when the display is filtered to them. Unfiltered, fall
     * back to the modal country the payload reports — a country centroid is
     * coarse but it is never *wrong* in the way averaging every partner's
     * cells together would be. */
    const COUNTRY_AT = {
      NG: [10.2, 8.3],
      KE: [-0.4, 37.0],
      UG: [1.3, 32.4],
      IN: [22.0, 79.0],
      CD: [-3.4, 23.0],
      LR: [6.5, -9.4],
      SL: [8.5, -11.8],
      TZ: [-6.2, 35.0],
      ML: [17.4, -3.9],
    };

    function where(r) {
      if (store.org === r.slug && cells.length) {
        let la = 0,
          lo = 0,
          w = 0;
        for (const c of cells) {
          la += c.lat * c.n;
          lo += c.lon * c.n;
          w += c.n;
        }
        if (w) return [la / w, lo / w];
      }
      return COUNTRY_AT[r.country] || null;
    }

    function spark(r) {
      const s = r.spark || [];
      if (!s.length) return '';
      const max = Math.max(...s, 1);
      // The final bucket is the current week and is incomplete by definition.
      const last = s.length - 1;
      return s
        .map(
          (v, i) =>
            `<i style="height:${Math.max((v / max) * 100, v ? 3 : 0).toFixed(
              1,
            )}%" ${i === last ? 'data-partial="1"' : ''}></i>`,
        )
        .join('');
    }

    function mix(r) {
      // Service mix is only known for the partner the display is scoped to —
      // by_service is a property of the current filter, not of a menu row. So
      // the strip appears when it can be true and is absent otherwise, rather
      // than showing the whole portfolio's mix under one partner's name.
      if (store.org !== r.slug) return '';
      const svc = (store.summary?.money?.by_service || []).filter(
        (x) => x.usd_total > 0,
      );
      if (!svc.length) return '';
      const total = svc.reduce((a, b) => a + b.usd_total, 0) || 1;
      const PAL = ['var(--c-1)', 'var(--c-2)', 'var(--c-3)', 'var(--c-4)'];
      const top = svc.slice(0, 4);
      return (
        `<div class="pulse-partner-mix">` +
        top
          .map(
            (x, i) =>
              `<i style="flex:${x.usd_total} 0 0;background:${
                PAL[i % 4]
              }"></i>`,
          )
          .join('') +
        `</div><div class="pulse-partner-mixkey">` +
        top
          .map(
            (x, i) =>
              `<span><i style="background:${PAL[i % 4]}"></i>${
                x.name
              } ${Math.round((x.usd_total / total) * 100)}%</span>`,
          )
          .join('') +
        `</div>`
      );
    }

    function html(r) {
      const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
      const where = r.country
        ? (store.summary?.labels?.countries || {})[r.country] || r.country
        : '';
      const inexact =
        store.org === r.slug &&
        store.lastGrid &&
        store.lastGrid.exact === false;
      // The real partner leads when we know it; the Connect workspace it came
      // from stays visible underneath, because several workspaces can be the
      // same partner and hiding that would make one of them look like the whole.
      const title = r.partner || r.name;
      const isSlug = !r.partner && r.named === false;
      return (
        `<div class="pulse-partner-head">
           <span class="pulse-partner-name"${
             isSlug ? ' data-slug="1"' : ''
           }>${title}</span>
           ${where ? `<span class="pulse-partner-where">${where}</span>` : ''}
         </div>` +
        `<div class="pulse-partner-funder">${
          r.partner && r.partner !== r.name
            ? `workspace <b>${r.name}</b> · `
            : ''
        }${
          r.funder
            ? `funded by <b>${r.funder}</b>`
            : `${nf.format(r.opportunities)} opportunit${
                r.opportunities === 1 ? 'y' : 'ies'
              }`
        }</div>` +
        `<div class="pulse-partner-figs">
           <div><span class="pf-l">Paid out</span>
                <div class="pf-v gold">${usdCompact(r.usd_total)}</div></div>
           <div><span class="pf-l">Per service</span>
                <div class="pf-v">${
                  r.rate == null ? '—' : usd(r.rate)
                }</div></div>
           <div><span class="pf-l">Services</span>
                <div class="pf-v">${nf.format(r.visits)}</div></div>
           <div><span class="pf-l">Approved</span>
                <div class="pf-v">${pct(r.approval_rate)}</div></div>
         </div>` +
        (r.spark && r.spark.length
          ? `<div class="pulse-partner-spark">${spark(r)}</div>
             <div class="pulse-partner-axis"><span>26 weeks ago</span><span>this week</span></div>`
          : '') +
        mix(r) +
        (r.named === false
          ? `<div class="pulse-partner-note">Connect does not publish this
               partner's name to the polling account, so this is their
               identifier rather than their name.</div>`
          : '') +
        (inexact
          ? `<div class="pulse-partner-note">Some of this partner's work sits
               outside a programme, so the lit geography under this card is
               partial.</div>`
          : '')
      );
    }

    function clear() {
      if (card) {
        const dying = card;
        dying.dataset.leaving = '1';
        setTimeout(() => dying.remove(), 200);
      }
      if (tether) tether.remove();
      card = tether = null;
      showing = null;
    }

    /* Place the card near the anchor but always fully on screen, and put it on
       whichever side has room. A card that runs off the edge of a wall display
       is worse than one on the unexpected side. */
    function place(x, y) {
      const w = 268,
        pad = 14;
      const right = x + 26 + w < W - pad;
      const cx0 = right ? x + 26 : x - 26 - w;
      const cy0 = Math.min(Math.max(y - 96, pad), Math.max(H - 250, pad));
      card.style.left =
        Math.round(Math.min(Math.max(cx0, pad), W - w - pad)) + 'px';
      card.style.top = Math.round(cy0) + 'px';
      card.style.setProperty('--ox', right ? '0%' : '100%');
      card.style.setProperty('--oy', '50%');

      const ax = right ? cx0 : cx0 + w;
      const ay = cy0 + 96;
      const dx = x - ax,
        dy = y - ay;
      const len = Math.hypot(dx, dy);
      const rot = (Math.atan2(dy, dx) * 180) / Math.PI;
      tether.style.left = ax + 'px';
      tether.style.top = ay + 'px';
      tether.style.width = Math.round(len) + 'px';
      tether.style.transform = `rotate(${rot.toFixed(2)}deg)`;
      tether.style.setProperty('--rot', `${rot.toFixed(2)}deg`);
    }

    function show(slug) {
      const r = row(slug);
      if (!r) return clear();
      const at = where(r);
      if (!at) return clear();
      const [x, y] = proj(at[0], at[1]);
      if (!isFinite(x) || x < -200 || x > W + 200) return clear();

      if (showing !== slug) {
        clear();
        tether = document.createElement('div');
        tether.className = 'pulse-tether';
        card = document.createElement('div');
        card.className = 'pulse-partner';
        card.innerHTML = html(r);
        /* The card is a summary; clicking it opens the full record. It has to
           opt back into pointer events to be clickable at all — the class sets
           pointer-events:none so a card can never swallow a map drag. */
        card.style.pointerEvents = 'auto';
        card.style.cursor = 'pointer';
        card.title = "Click for this partner's full record";
        card.addEventListener('click', (e) => {
          e.stopPropagation();
          if (window.PulseWindows) window.PulseWindows.openPartner(store, slug);
        });
        layer.appendChild(tether);
        layer.appendChild(card);
        showing = slug;
      } else {
        card.innerHTML = html(r);
      }
      place(x, y);
    }

    /* Keep the card locked to its point through pans, zooms and act changes —
       it is anchored to geography, so it has to move when the geography does. */
    function reposition() {
      if (!showing) return;
      const r = row(showing);
      const at = r && where(r);
      if (!at) return clear();
      const [x, y] = proj(at[0], at[1]);
      if (!isFinite(x)) return;
      place(x, y);
    }

    /* Nearest partner to a screen point, via the country of the nearest lit
       cell. Cheap, and it means pointing at Kano surfaces whoever works in
       Kano rather than whoever happens to be first in the menu. */
    function nearest(px, py) {
      let best = null,
        bd = Infinity;
      for (const c of cells) {
        const [x, y] = proj(c.lat, c.lon);
        const d = (x - px) ** 2 + (y - py) ** 2;
        if (d < bd) {
          bd = d;
          best = c;
        }
      }
      if (!best || bd > 120 * 120) return null;
      const here = rows().filter((o) => o.country === best.country);
      if (!here.length) return null;
      return here[0].slug; // rows are already ordered by delivery
    }

    return {
      pin(slug) {
        pinned = slug;
        show(slug);
      },
      unpin() {
        pinned = null;
        clear();
      },
      /* Is there a partner under this point? Used only to set the cursor, so
         the map can advertise that it is clickable without anything appearing.
         Showing a card on mousemove reads as the display twitching: a card
         arrives, you did not ask for it, and nothing says what it refers to. */
      at(px, py) {
        return nearest(px, py);
      },
      /* Explicit inspect. Clicking the same partner again releases it, so the
         gesture that opened the card also closes it. */
      toggleAt(px, py) {
        const slug = nearest(px, py);
        if (!slug) {
          if (pinned) this.unpin();
          return null;
        }
        if (pinned === slug) {
          this.unpin();
          return null;
        }
        this.pin(slug);
        return slug;
      },
      reposition,
      /* The unattended tour. A wall display with nobody in front of it should
         still show the portfolio off, but it must never compete with someone
         who is actually driving -- `idleFor` is how long since the last real
         interaction, and the tour only runs once the room has gone quiet. */
      tick(now, idleFor) {
        if (pinned || !rows().length) return;
        if (idleFor < IDLE_BEFORE_TOUR) {
          if (showing) clear();
          return;
        }
        if (now - cycleAt < 7000) return;
        cycleAt = now;
        const live = rows().filter((o) => o.recent_events > 0);
        const pool = live.length ? live : rows().slice(0, 6);
        if (!pool.length) return clear();
        const i = Math.floor(now / 7000) % pool.length;
        show(pool[i].slug);
      },
      showing: () => showing,
    };
  })();

  /* How long the pointer has to be still before the display starts touring
     partners by itself. Long enough that it never interrupts someone reading
     the screen, short enough that an unattended wall display gets there. */
  const IDLE_BEFORE_TOUR = 45000;
  let lastInteractionAt = 0;

  /* ═══ replay range brush ══════════════════════════
     Drag a window over a histogram of when delivery actually happened.

     This exists because the replay loop is DERIVED, not configured: the store
     paces between the first and last event it received, so a partner with two
     visits ten minutes apart loops over ten minutes however many hours were
     requested. The histogram makes that legible before you drag, and the drag
     lets you hold the window open over a chosen stretch instead.           */
  const Brush = (() => {
    const track = $('#brush');
    const bars = $('#brush-bars');
    const sel = $('#brush-sel');
    const head = $('#brush-head');
    const read = $('#brush-read');
    const clear = $('#brush-clear');
    if (!track) return { paint() {}, tick() {} };

    let span = null; // [fromEpoch, toEpoch] the histogram covers
    let drag = null;

    const fmt = (ts) => {
      const d = new Date(ts * 1000);
      return (
        String(d.getUTCMonth() + 1).padStart(2, '0') +
        '-' +
        String(d.getUTCDate()).padStart(2, '0') +
        ' ' +
        String(d.getUTCHours()).padStart(2, '0') +
        ':' +
        String(d.getUTCMinutes()).padStart(2, '0')
      );
    };
    const dur = (a, b) => {
      const h = (b - a) / 3600;
      if (h < 1) return Math.max(1, Math.round(h * 60)) + 'm';
      if (h < 48) return h.toFixed(h < 10 ? 1 : 0) + 'h';
      return (h / 24).toFixed(1) + 'd';
    };
    const atX = (clientX) => {
      const r = track.getBoundingClientRect();
      const f = Math.min(Math.max((clientX - r.left) / r.width, 0), 1);
      return span[0] + (span[1] - span[0]) * f;
    };
    const toPct = (ts) =>
      (((ts - span[0]) / (span[1] - span[0])) * 100).toFixed(3) + '%';

    function paintSelection() {
      const r = store.range;
      if (!span) return;
      if (!r) {
        sel.hidden = true;
        clear.hidden = true;
        read.textContent = 'Full window';
        track.setAttribute('aria-valuetext', 'Full window');
        return;
      }
      sel.hidden = false;
      clear.hidden = false;
      sel.style.left = toPct(r[0]);
      sel.style.width =
        (((r[1] - r[0]) / (span[1] - span[0])) * 100).toFixed(3) + '%';
      const label =
        fmt(r[0]) + ' \u2192 ' + fmt(r[1]) + ' \u00b7 ' + dur(r[0], r[1]);
      read.innerHTML = '<b>' + label + '</b> UTC';
      track.setAttribute('aria-valuetext', label);
    }

    async function commit(a, b) {
      if (Math.abs(b - a) < 60) return; // a click, not a drag
      try {
        await store.setRange(Math.min(a, b), Math.max(a, b));
      } catch (err) {
        console.error('[pulse] range select failed', err);
      }
      paintSelection();
      paintTransport();
      paintStatus();
    }

    track.addEventListener('pointerdown', (e) => {
      if (!span) return;
      track.setPointerCapture(e.pointerId);
      drag = { from: atX(e.clientX), to: atX(e.clientX) };
    });
    track.addEventListener('pointermove', (e) => {
      if (!drag || !span) return;
      drag.to = atX(e.clientX);
      sel.hidden = false;
      const a = Math.min(drag.from, drag.to);
      const b = Math.max(drag.from, drag.to);
      sel.style.left = toPct(a);
      sel.style.width =
        (((b - a) / (span[1] - span[0])) * 100).toFixed(3) + '%';
      read.innerHTML =
        '<b>' + fmt(a) + ' \u2192 ' + fmt(b) + '</b> \u00b7 ' + dur(a, b);
    });
    const end = () => {
      if (!drag) return;
      const d = drag;
      drag = null;
      commit(d.from, d.to);
    };
    track.addEventListener('pointerup', end);
    track.addEventListener('pointercancel', end);
    clear.addEventListener('click', async () => {
      await store.setRange(null, null);
      paintSelection();
      paintTransport();
      paintStatus();
    });

    return {
      /* Redrawn from the summary, which carries hourly activity across the
         whole retention window -- not just the replay window, or you could
         never drag to a period the current loop excludes. */
      paint(s2) {
        const a = (s2 && s2.activity) || [];
        if (!a.length) {
          bars.innerHTML = '';
          span = null;
          return;
        }
        const now = Math.floor(Date.now() / 1000);
        span = [a[0].t, Math.max(a[a.length - 1].t + 3600, now)];
        const byHour = new Map(a.map((x) => [Math.floor(x.t / 3600), x.n]));
        const h0 = Math.floor(span[0] / 3600);
        const h1 = Math.floor(span[1] / 3600);
        const max = Math.max(...a.map((x) => x.n), 1);
        let html = '';
        for (let h = h0; h <= h1; h++) {
          const n = byHour.get(h) || 0;
          html +=
            '<i style="height:' +
            (n ? Math.max((n / max) * 100, 6).toFixed(1) : 0) +
            '%"' +
            (n ? '' : ' data-empty="1"') +
            '></i>';
        }
        bars.innerHTML = html;
        paintSelection();
      },
      /* The playhead, so the brush doubles as the transport scrubber. */
      tick() {
        if (!span || !store.clock) {
          head.hidden = true;
          return;
        }
        head.hidden = false;
        head.style.left = toPct(store.clock);
      },
    };
  })();

  /* ═══ controls ══════════════════════════════════════════════════ */
  function wireControls() {
    /* Inspecting a partner is a CLICK, not a hover.
     *
       Surfacing a card on mousemove meant a dossier appeared whenever the
       pointer crossed the map -- you had not asked for it, nothing said what it
       referred to, and because the unattended tour drew the same card, there was
       no way to tell whether the display was answering you or talking to itself.
       A click is unambiguous, and clicking the same partner again closes it.

       The listener lives on the map section rather than on #sky, which is
       pointer-events:none so Mapbox keeps its own pan and zoom; .pulse-map
       shares that exact box, so the offsets already match what proj() returns.

       Registered here, once. It previously sat inside setFocus(), which runs on
       every focus change and every filter change -- so the handlers accumulated,
       and by the fourth programme switch a single mouse move was doing the
       cell-walk four times. */
    const mapBox = $('.pulse-map');
    if (mapBox) {
      const atPointer = (e) => {
        const r = mapBox.getBoundingClientRect();
        return [e.clientX - r.left, e.clientY - r.top];
      };

      let cursorRaf = 0;
      mapBox.addEventListener('mousemove', (e) => {
        lastInteractionAt = performance.now();
        // Only ever sets the cursor. Coalesced to one lookup per frame, because
        // the hit test walks every lit cell and mousemove outruns the repaint.
        if (cursorRaf) return;
        const [px, py] = atPointer(e);
        cursorRaf = requestAnimationFrame(() => {
          cursorRaf = 0;
          mapBox.dataset.overPartner = Partners.at(px, py) ? '1' : '';
        });
      });

      mapBox.addEventListener('click', (e) => {
        lastInteractionAt = performance.now();
        const [px, py] = atPointer(e);
        const slug = Partners.toggleAt(px, py);
        // Keep the Partner menu honest about what is on screen.
        const sel = $('#org-filter');
        if (sel && !slug && !store.org) sel.value = '';
      });

      // Esc dismisses, matching every other transient panel.
      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        lastInteractionAt = performance.now();
        if (!store.org) Partners.unpin();
      });
    }

    $$('.pulse-focus button').forEach((b) =>
      b.addEventListener('click', () => {
        lastInteractionAt = performance.now();
        setFocus(b.dataset.focus);
        autoCycle = false;
      }),
    );
    $('#btn-play').addEventListener('click', () => {
      const playing = store.toggle();
      $('#btn-play').textContent = playing ? 'Pause' : 'Play';
      $('#btn-play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
    });

    /* Live and replay are different sources, not a display preference, so the
       switch reloads from the server rather than reinterpreting what is held.
       The button is disabled while switching: startLive() and
       loadReplayWindow() are both async, and a double-click would run two
       loads whose events interleave. */
    const sel = $('#prog-filter');
    if (sel) {
      sel.addEventListener('change', async () => {
        const val = sel.value ? Number(sel.value) : null;
        sel.disabled = true;
        try {
          await store.setProgram(val);
          Url.write();
          // The density layer is a separate fetch and has to follow the filter
          // too, or the map keeps the whole estate's geography under one
          // programme's points.
          await loadGrid();
          setFocus(focus, true);
        } catch (err) {
          console.error('[pulse] programme filter failed', err);
        } finally {
          sel.disabled = false;
          paintTransport();
          paintStatus();
        }
      });
    }

    const svcCtl = $('#svc-filter');
    if (svcCtl) {
      svcCtl.addEventListener('change', async () => {
        svcCtl.disabled = true;
        try {
          await store.setService(svcCtl.value || null);
          Url.write();
          // The density layer is a separate fetch and has to follow the filter
          // too. Here it narrows exactly: cells carry service_slug.
          await loadGrid();
          setFocus(focus, true);
        } catch (err) {
          console.error('[pulse] service filter failed', err);
        } finally {
          svcCtl.disabled = false;
          paintTransport();
          paintStatus();
        }
      });
    }

    const orgSel = $('#org-filter');
    if (orgSel) {
      orgSel.addEventListener('change', async () => {
        orgSel.disabled = true;
        try {
          await store.setOrg(orgSel.value || null);
          Url.write();
          await loadGrid();
          setFocus(focus, true);
          // Pin the selected partner's card. Selecting one is a request to
          // look at it, so it stays up rather than waiting to be pointed at.
          if (store.org) Partners.pin(store.org);
          else Partners.unpin();
        } catch (err) {
          console.error('[pulse] partner filter failed', err);
        } finally {
          orgSel.disabled = false;
          paintTransport();
          paintStatus();
        }
      });
    }

    $('#btn-mode').addEventListener('click', async () => {
      const btn = $('#btn-mode');
      const next = store.mode === 'live' ? 'replay' : 'live';
      btn.disabled = true;
      btn.textContent = next === 'live' ? 'Going live…' : 'Loading replay…';
      try {
        await store.setMode(next);
      } catch (err) {
        console.error('[pulse] mode switch failed', err);
      } finally {
        btn.disabled = false;
        paintTransport();
        paintStatus();
      }
    });
    $$('[data-speed]').forEach((b) =>
      b.addEventListener('click', () => {
        store.setSpeed(+b.dataset.speed);
        $('#speed-read').textContent = b.dataset.speed + '×';
        $$('[data-speed]').forEach((o) =>
          o.setAttribute('aria-pressed', o === b),
        );
      }),
    );
    addEventListener('keydown', (ev) => {
      if (ev.key >= '1' && ev.key <= String(ACTS.length))
        setAct(+ev.key - 1, true);
      else if (ev.code === 'Space') {
        ev.preventDefault();
        $('#btn-play').click();
      } else if (ev.key.toLowerCase() === 'f') {
        const ks = Object.keys(FOCI);
        setFocus(ks[(ks.indexOf(focus) + 1) % ks.length]);
        autoCycle = false;
      }
    });
    setInterval(() => {
      if (autoCycle) setAct(act + 1);
    }, CYCLE_MS);
  }

  /* ═══ go ════════════════════════════════════════════════════════ */
  /* ═══ shareable URL ═════════════════════════════════
     The address bar is the display's state, so a link can be pasted into a
     message and open on exactly what the sender was looking at.

     Filters use the same names the API does (`service`, `program`, `org`), so
     a URL someone builds by hand behaves the way the API docs would suggest.
     `org` both scopes the display and opens that partner's window -- sharing a
     partner means sharing its record, not a filtered map you then have to
     click into.

     replaceState, not pushState: this runs on wall displays that re-filter
     themselves for minutes at a time, and every act change would otherwise
     become a history entry nobody asked for. The URL stays current and
     shareable; the back button stays the way out of the page.               */
  const Url = (() => {
    const read = () => new URLSearchParams(location.search);

    function write() {
      const q = read();
      // The public-link token lives in the PATH, so anything already in the
      // query belongs to us -- except params we do not own, which are left be.
      const set = (k, v) => (v ? q.set(k, String(v)) : q.delete(k));
      set('service', store.service);
      set('program', store.program);
      set('org', store.org);
      const open = window.PulseWindows && window.PulseWindows.state();
      set('partner', open && open.partner);
      set('opp', open && open.opportunity);
      set('worker', open && open.worker);
      const qs = q.toString();
      history.replaceState(null, '', location.pathname + (qs ? '?' + qs : ''));
    }

    async function apply() {
      const q = read();
      const service = q.get('service');
      const program = q.get('program');
      const org = q.get('org');

      // Set them on the store directly and load ONCE, rather than calling three
      // setters that would each re-fetch everything in turn.
      store.service = service || null;
      store.program = program ? Number(program) : null;
      store.org = org || null;
      if (service || program || org) {
        await store.refreshSummary();
        await loadGrid();
        const svc = $('#svc-filter'),
          prog = $('#prog-filter'),
          o = $('#org-filter');
        if (svc) svc.value = store.service || '';
        if (prog) prog.value = store.program ? String(store.program) : '';
        if (o) o.value = store.org || '';
      }

      // A shared partner link opens the record, not just a filtered map.
      const partner = q.get('partner') || org;
      if (partner && window.PulseWindows) {
        const opp = q.get('opp');
        window.PulseWindows.openPartner(
          store,
          partner,
          opp ? Number(opp) : null,
        );
        const worker = q.get('worker');
        if (worker)
          window.PulseWindows.openWorker(store, worker, partner, partner);
      }
    }

    return { apply, write };
  })();

  async function boot() {
    buildActPanel();
    wireControls();
    addEventListener('resize', size);
    size();
    map = initBasemap();

    try {
      window.PulseCards.CARDS.kpis.mount($('#kpi'), store);
      window.PulseCards.CARDS.ticker.mount($('#ticker'), store);
    } catch (err) {
      console.error('[pulse] persistent card failed', err);
    }

    requestAnimationFrame(paint);
    await loadGrid();
    await store.start();
    // The address bar follows whatever window is opened from here on.
    if (window.PulseWindows) window.PulseWindows.onChange(Url.write);
    await Url.apply();
    Url.write();
    setAct(0);
    paintTransport();
    paintStatus();
  }

  boot().catch((err) => {
    console.error('[pulse] boot failed', err);
    $('#mode').dataset.state = 'stale';
    $('#mode-text').textContent = 'Failed to load';
    $('#alert').hidden = false;
    $('#alert-text').textContent =
      'Could not reach the Pulse API. Nothing on this screen is current.';
  });
})();
