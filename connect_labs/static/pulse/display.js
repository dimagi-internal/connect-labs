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
  const { nf } = window.PulseCards.helpers;

  const store = new window.PulseStore({
    base: CFG.base,
    mode: 'replay',
    speed: 240,
  });

  /* ═══ map ═══════════════════════════════════════════════════════ */
  const cv = $('#sky'),
    cx = cv.getContext('2d');
  const FOCI = {
    world: { lon: [-16, 90], lat: [-14, 32] },
    ng: { lon: [2.5, 14.8], lat: [4.0, 14.2] },
    ea: { lon: [28.5, 41.5], lat: [-6.5, 4.8] },
    in: { lon: [68, 90], lat: [8, 30] },
  };
  let view = { ...FOCI.world },
    target = { ...FOCI.world },
    focus = 'world';
  let W = 0,
    H = 0,
    dpr = 1,
    baseLayer = null;
  let cells = [];

  function size() {
    dpr = Math.min(devicePixelRatio || 1, 2);
    const r = cv.getBoundingClientRect();
    W = r.width;
    H = r.height;
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawBase();
  }

  /* Fit the focus box without distorting geography. */
  function proj(la, lo, v) {
    const lonSpan = v.lon[1] - v.lon[0],
      latSpan = v.lat[1] - v.lat[0];
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
    return [ox + (lo - v.lon[0]) * sx, oy + (v.lat[1] - la) * sy];
  }

  /**
   * The ambient layer IS the basemap. Two passes — a wide soft bloom so
   * clusters read as inhabited area, then a tight core so settlements stay
   * countable when zoomed. Both radius and alpha are hard-capped: under
   * `lighter` blending a dense cluster otherwise saturates to a white blob
   * and destroys the structure that is the whole reason to zoom in.
   */
  function drawBase() {
    const off = document.createElement('canvas');
    off.width = cv.width;
    off.height = cv.height;
    const c = off.getContext('2d');
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.globalCompositeOperation = 'lighter';

    const ppd = W / (view.lon[1] - view.lon[0]);
    const zoom = Math.min(Math.max(ppd / 12, 0.6), 1.7);

    for (const cell of cells) {
      const [x, y] = proj(cell.lat, cell.lon, view);
      if (x < -40 || x > W + 40 || y < -40 || y > H + 40) continue;
      const w = cell.n;
      const r = Math.min((2.2 + Math.log1p(w) * 1.0) * zoom, 12);
      const a = Math.min(0.028 + Math.log1p(w) * 0.016, 0.095);
      const g = c.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(212,150,44,${a})`);
      g.addColorStop(1, 'rgba(212,150,44,0)');
      c.fillStyle = g;
      c.beginPath();
      c.arc(x, y, r, 0, 6.2832);
      c.fill();
    }
    for (const cell of cells) {
      const [x, y] = proj(cell.lat, cell.lon, view);
      if (x < -20 || x > W + 20 || y < -20 || y > H + 20) continue;
      const w = cell.n;
      const r = Math.max(
        0.6,
        Math.min((0.6 + Math.log1p(w) * 0.24) * zoom, 2.3),
      );
      const a = Math.min(0.26 + Math.log1p(w) * 0.075, 0.66);
      c.fillStyle = `rgba(255,208,128,${a})`;
      c.beginPath();
      c.arc(x, y, r, 0, 6.2832);
      c.fill();
    }
    baseLayer = off;
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

  const ANCHORS = {
    NG: [6.6, 8.4, 'NIGERIA'],
    KE: [-3.6, 37.4, 'KENYA'],
    UG: [4.2, 30.4, 'UGANDA'],
    IN: [19.4, 79.0, 'INDIA'],
    CD: [-6.5, 23.0, 'DR CONGO'],
    TZ: [-8.0, 34.5, 'TANZANIA'],
  };

  let lastPaint = 0;
  function paint(ts) {
    requestAnimationFrame(paint);
    const dt = Math.min((ts - lastPaint) / 1000, 0.1);
    lastPaint = ts;

    let moving = false;
    for (const k of ['lon', 'lat']) {
      for (let i = 0; i < 2; i++) {
        const d = target[k][i] - view[k][i];
        if (Math.abs(d) > 1e-4) {
          view[k][i] += d * Math.min(dt * 3.2, 1);
          moving = true;
        }
      }
    }
    if (moving) drawBase();

    cx.clearRect(0, 0, W, H);

    cx.strokeStyle = 'rgba(120,140,180,.05)';
    cx.lineWidth = 1;
    for (let lo = -180; lo <= 180; lo += 10) {
      const [x] = proj(0, lo, view);
      if (x > 0 && x < W) {
        cx.beginPath();
        cx.moveTo(x, 0);
        cx.lineTo(x, H);
        cx.stroke();
      }
    }
    for (let la = -60; la <= 60; la += 10) {
      const [, y] = proj(la, 0, view);
      if (y > 0 && y < H) {
        cx.beginPath();
        cx.moveTo(0, y);
        cx.lineTo(W, y);
        cx.stroke();
      }
    }

    if (baseLayer) {
      cx.save();
      cx.setTransform(1, 0, 0, 1, 0, 0);
      cx.drawImage(baseLayer, 0, 0);
      cx.restore();
    }

    /* Country names, offset clear of their own cluster and skipped where the
       header copy sits, so type never lands on top of the claim. */
    cx.font = '600 10px ui-monospace, Menlo, monospace';
    cx.textAlign = 'center';
    cx.letterSpacing = '2px';
    for (const k in ANCHORS) {
      const [la, lo, name] = ANCHORS[k];
      const [x, y] = proj(la, lo, view);
      if (x < 34 || x > W - 34 || y < 22 || y > H - 22) continue;
      if (y < 150 && x < 520) continue; // reserved for the claim
      cx.fillStyle = 'rgba(5,8,16,.75)';
      cx.fillText(name, x + 0.6, y + 0.6);
      cx.fillStyle = 'rgba(158,173,202,.5)';
      cx.fillText(name, x, y);
    }
    cx.letterSpacing = '0px';

    cx.globalCompositeOperation = 'lighter';
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i];
      s.t += dt;
      const life = 3.4;
      if (s.t > life) {
        sparks.splice(i, 1);
        continue;
      }
      const p = s.t / life,
        [r, g, b] = s.col;
      const [x, y] = proj(s.la, s.lo, view);
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
        title: 'The money reaches the person who did the work.',
        note: "Measured from approved work, not budgeted — and paid to a worker's phone, not a sub-grantee.",
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
        title:
          '$663,682 has reached frontline workers, one verified service at a time.',
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

  function setAct(i, manual) {
    act = (i + ACTS.length) % ACTS.length;
    const a = ACTS[act];
    const card = window.PulseCards.CARDS[a.card];
    $('#act-label').textContent = card ? card.label : a.eyebrow;
    $('#act-eyebrow').textContent = a.eyebrow;
    $('#act-title').textContent = a.title;
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

  function setFocus(k) {
    focus = k;
    target = { lon: [...FOCI[k].lon], lat: [...FOCI[k].lat] };
    $$('.pulse-focus button').forEach((b) =>
      b.setAttribute('aria-pressed', b.dataset.focus === k),
    );
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

  store.on('summary', (s) => {
    const scope = s.scope || {};
    $('#s-opp').textContent = nf.format(scope.opportunities || 0);
    $('#s-prog').textContent = nf.format(scope.programs || 0);
    $('#s-cty').textContent = (s.money?.by_country || []).length || '—';
  });

  store.on('event', ignite);
  store.on('backfill', () => {
    sparks.length = 0;
  });

  /* ═══ grid (the map's geography) ════════════════════════════════ */
  async function loadGrid() {
    try {
      const res = await fetch(`${CFG.base}/api/grid/?limit=40000`);
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
      $('#btn-play').textContent = store.toggle() ? 'Pause' : 'Play';
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
