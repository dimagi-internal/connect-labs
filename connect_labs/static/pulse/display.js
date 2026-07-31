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
  const { nf, usd } = window.PulseCards.helpers;

  const store = new window.PulseStore({
    base: CFG.base,
    mode: 'replay',
    speed: 240,
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
    });
    m.on('zoom', () => {
      baseDirty = true;
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
    const scope = s.scope || {};
    $('#s-opp').textContent = nf.format(scope.opportunities || 0);
    $('#s-prog').textContent = nf.format(scope.programs || 0);
    $('#s-cty').textContent = (s.money?.by_country || []).length || '—';
    paintActTitle();
  });

  store.on('event', ignite);
  store.on('backfill', () => {
    sparks.length = 0;
  });

  /* ═══ grid (the map's geography) ════════════════════════════════ */
  async function loadGrid() {
    try {
      const q0 = new URLSearchParams({ limit: '40000' });
      if (store.program) q0.set('program', store.program);
      const res = await fetch(`${CFG.base}/api/grid/?${q0}`);
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

  /* ═══ controls ══════════════════════════════════════════════════ */
  function wireControls() {
    $$('.pulse-focus button').forEach((b) =>
      b.addEventListener('click', () => {
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
