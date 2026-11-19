/* The Investigations Tracker (public page): one sortable, filterable table of programs; rows expand in place.
   Everything shown comes from the JSON the server put on the page (investigations.page.build). Every node is built
   with textContent, so no field is ever read as HTML, and a link is followed only if it is https or a Labs path. */
(function () {
  'use strict';

  const D = JSON.parse(
    document.getElementById('investigations-data').textContent,
  );
  const P = D.programs;
  const GROUPS = D.groups;
  const BAR = D.bar;
  const SCALE_MAX = 30;
  const STAGE_ORDER = [
    'At scale',
    'Piloting',
    'Designing',
    'Idea',
    'Paused',
    'Completed',
  ];
  const MODEL_RANK = { public: 0, draft: 1, planned: 2, reference: 3, none: 4 };
  const MONTHS = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  const LONG_MONTHS = [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
  ];

  const COLS = [
    { key: 'name', label: 'Program', get: (p) => p.name.toLowerCase() },
    { key: 'stage', label: 'Stage', get: (p) => STAGE_ORDER.indexOf(p.stage) },
    { key: 'where', label: 'Where', get: (p) => whereText(p).toLowerCase() },
    {
      key: 'visits',
      label: 'Verified visits',
      num: true,
      get: (p) => p.visits,
      desc: true,
    },
    {
      key: 'ce',
      label: 'Cost-effectiveness',
      get: (p) => (p.ce ? p.ce.base : -1),
      desc: true,
    },
    { key: 'model', label: 'Model', get: (p) => MODEL_RANK[p.model] },
    { key: 'since', label: 'Since', get: (p) => p.since || '9999' },
  ];

  function ym(s) {
    if (!s) return '';
    const [y, m] = s.split('-');
    return MONTHS[+m - 1] + ' ' + y;
  }
  function longDate(s) {
    const [y, m, d] = (s || '').split('-');
    return y && m && d ? LONG_MONTHS[+m - 1] + ' ' + +d + ', ' + y : '';
  }
  function compact(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(2).replace(/0$/, '') + 'M';
    if (n >= 1e4) return Math.round(n / 1e3) + 'K';
    return n.toLocaleString('en-US');
  }
  function whereText(p) {
    return p.now.length ? p.now.join(', ') : p.planned || p.earlier || '';
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  /* A link the page may follow: https anywhere, or a path on this site. Anything else is shown as nothing. */
  function safeHref(url) {
    if (typeof url !== 'string') return '';
    if (url.startsWith('https://')) return url;
    /* A path on this site: one leading slash. A second slash or a backslash (code 92) would name another host. */
    const second = url.charCodeAt(1);
    return url.startsWith('/') && second !== 47 && second !== 92 ? url : '';
  }
  function anchor(label, url) {
    const href = safeHref(url);
    if (!href) return null;
    const a = el('a', null, label);
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener';
    return a;
  }
  const MAXV = Math.max(1, ...P.map((p) => p.visits));
  const pct = (v) => Math.max(0, Math.min(100, (v / SCALE_MAX) * 100));

  const state = {
    filter: 'all',
    q: '',
    grouped: true,
    sort: null,
    dir: 1,
    open: new Set(),
  };
  try {
    const s = JSON.parse(localStorage.getItem('cit-view') || 'null');
    if (s) {
      state.grouped = s.grouped !== false;
      state.sort = COLS.some((c) => c.key === s.sort) ? s.sort : null;
      state.dir = s.dir === -1 ? -1 : 1;
    }
  } catch (e) {
    /* storage is a convenience; the page works without it */
  }
  function remember() {
    try {
      localStorage.setItem(
        'cit-view',
        JSON.stringify({
          grouped: state.grouped,
          sort: state.sort,
          dir: state.dir,
        }),
      );
    } catch (e) {
      /* as above */
    }
  }

  function buildHeader() {
    const asof = document.getElementById('asof');
    const when = longDate(D.as_of);
    asof.textContent = [
      when ? 'Delivery figures from Connect, ' + when + '.' : '',
      D.models_note || '',
    ]
      .filter(Boolean)
      .join(' ');
    const stats = document.getElementById('stats');
    const tiles = [
      [compact(D.stats.visits), 'verified visits to date'],
      [D.stats.countries, 'countries'],
      [D.stats.organizations, 'local organizations delivering'],
      [
        String(D.stats.above_bar),
        (D.stats.above_bar === 1 ? 'program' : 'programs') +
          ' above the GiveWell bar',
      ],
    ];
    tiles.forEach(([value, label]) => {
      if (!value) return;
      const s = el('div', 'stat');
      s.append(el('b', null, value), el('span', null, label));
      stats.append(s);
    });
  }

  function setSort(key) {
    const c = COLS.find((x) => x.key === key);
    if (!c) {
      state.sort = null;
      state.dir = 1;
    } else if (state.sort === key) {
      state.dir = -state.dir;
    } else {
      state.sort = key;
      state.dir = c.desc ? -1 : 1;
    }
    remember();
    render();
  }

  function buildHead() {
    const tr = document.getElementById('head');
    tr.textContent = '';
    COLS.forEach((c) => {
      const th = el('th', c.num ? 'num' : '');
      th.scope = 'col';
      if (state.sort === c.key)
        th.setAttribute(
          'aria-sort',
          state.dir > 0 ? 'ascending' : 'descending',
        );
      const b = el('button');
      b.type = 'button';
      b.id = 'sort-' + c.key;
      b.append(
        el('span', null, c.label),
        el(
          'span',
          'arrow',
          state.sort === c.key ? (state.dir > 0 ? '▲' : '▼') : '▲▼',
        ),
      );
      b.addEventListener('click', () => setSort(c.key));
      th.append(b);
      tr.append(th);
    });
    document.getElementById('sortsel').value = state.sort || '';
  }

  /* On a phone the table is a list of cards with no header row, so sorting is a menu. */
  function buildSortMenu() {
    const sel = document.getElementById('sortsel');
    sel.append(new Option('Stage (default)', ''));
    COLS.forEach((c) => sel.append(new Option(c.label, c.key)));
    sel.addEventListener('change', () => {
      const c = COLS.find((x) => x.key === sel.value);
      state.sort = c ? c.key : null;
      state.dir = c && c.desc ? -1 : 1;
      remember();
      render();
    });
  }

  function buildChips() {
    const host = document.getElementById('chips');
    host.textContent = '';
    [{ key: 'all', title: 'All' }, ...GROUPS].forEach((g) => {
      const n =
        g.key === 'all' ? P.length : P.filter((p) => p.group === g.key).length;
      const b = el('button', 'chip');
      b.type = 'button';
      b.id = 'chip-' + g.key;
      b.setAttribute('aria-pressed', state.filter === g.key ? 'true' : 'false');
      b.append(g.title, el('span', 'n', n));
      b.addEventListener('click', () => {
        state.filter = g.key;
        buildChips();
        render();
      });
      host.append(b);
    });
  }

  function cell(key, label, cls) {
    const td = el('td', 'c-' + key + (cls ? ' ' + cls : ''));
    td.dataset.label = label;
    return td;
  }

  /* Only a Connect model is drawn on the scale. A GiveWell reference is text: it was measured against the benchmark
     GiveWell had in the year of that grant, so it does not belong on the same axis. */
  function ceCell(p) {
    const td = cell('ce', 'Cost-effectiveness');
    if (!p.ce) {
      if (p.ref && p.ref.short) {
        const w = el('div', 'refce');
        w.append(
          el('span', 'reflab', 'GiveWell reference'),
          el('span', 'refval', p.ref.short),
        );
        td.append(w);
      } else
        td.append(
          el(
            'span',
            'dim',
            p.model === 'draft' ? 'Model in development' : 'No estimate yet',
          ),
        );
      return td;
    }
    const w = el('div', 'ce');
    w.append(el('b', null, p.ce.base + 'x'));
    const s = el('div', 'scale');
    s.setAttribute('role', 'img');
    s.setAttribute(
      'aria-label',
      `Base case ${p.ce.base}x, scenarios ${p.ce.lo}x to ${p.ce.hi}x, GiveWell bar ${BAR}x`,
    );
    const r = el('div', 'range');
    r.style.left = pct(p.ce.lo) + '%';
    r.style.width = pct(p.ce.hi) - pct(p.ce.lo) + '%';
    const b = el('div', 'bar');
    b.style.left = pct(BAR) + '%';
    const d = el('div', 'dot');
    d.style.left = pct(p.ce.base) + '%';
    s.append(el('div', 'track'), r, b, d);
    w.append(s);
    td.append(w);
    return td;
  }

  function field(label, text, link) {
    const d = el('div');
    d.append(el('p', 'label', label));
    const p = el('p', null, text);
    if (link) {
      p.append(' ', link);
    }
    d.append(p);
    return d;
  }

  function progRows(p) {
    const open = state.open.has(p.name);
    const tr = el('tr', 'prog');
    tr.tabIndex = 0;
    tr.setAttribute('aria-expanded', open ? 'true' : 'false');
    const n = cell('name', 'Program');
    const nm = el('div', 'pname');
    nm.append(el('span', 'caret', '▶'), el('span', null, p.name));
    n.append(nm);
    const st = cell('stage', 'Stage');
    st.append(el('span', 'pill ' + p.group, p.stage));
    const wh = cell('where', 'Where', 'where');
    if (p.now.length) {
      wh.append(p.now.join(', '));
      if (p.place) wh.append(el('small', null, p.place));
      else if (p.earlier)
        wh.append(el('small', null, 'plus earlier countries'));
    } else if (p.planned) {
      wh.append(el('span', 'dim', p.planned));
      wh.append(el('small', null, 'planned'));
    } else {
      wh.append(el('span', 'dim', p.earlier || 'Not yet chosen'));
    }
    const v = cell('visits', 'Verified visits', 'num');
    if (p.visits) {
      const box = el('div', 'visits');
      box.append(el('span', null, p.visits.toLocaleString('en-US')));
      const bar = el('div', 'vbar');
      bar.style.width = Math.max(3, Math.sqrt(p.visits / MAXV) * 90) + 'px';
      box.append(bar);
      v.append(box);
    } else v.append(el('span', 'dim', 'Not yet'));
    const m = cell('model', 'Model');
    m.append(el('span', 'model ' + p.model, p.model_text));
    const si = cell('since', 'Since');
    si.append(p.since ? ym(p.since) : el('span', 'dim', 'Not started'));
    if (p.ended) si.append(el('small', 'dim', ' to ' + ym(p.ended)));
    tr.append(n, st, wh, v, ceCell(p), m, si);
    const toggle = () => {
      if (state.open.has(p.name)) state.open.delete(p.name);
      else state.open.add(p.name);
      render();
    };
    tr.addEventListener('click', (e) => {
      if (!e.target.closest('a')) toggle();
    });
    tr.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggle();
      }
    });
    if (!open) return [tr];

    const dr = el('tr', 'detail');
    const td = el('td');
    td.colSpan = COLS.length;
    const g = el('div', 'dgrid');
    g.append(field('What it is', p.what));
    const places = [
      p.now.length
        ? 'Now: ' +
          p.now.join(', ') +
          (p.place ? ' (' + p.place + ')' : '') +
          '.'
        : '',
      p.planned ? 'Planned: ' + p.planned + '.' : '',
      p.earlier ? (p.now.length ? 'Earlier: ' : '') + p.earlier + '.' : '',
    ]
      .filter(Boolean)
      .join(' ');
    if (places) g.append(field('Where', places));
    if (p.done) g.append(field('Delivery', p.done));
    const modelLink = p.ce
      ? anchor(
          p.link_kind === 'explorer'
            ? 'Open in the model explorer'
            : 'Open the model',
          p.link,
        )
      : null;
    g.append(
      field(
        'Cost-effectiveness',
        (p.ce
          ? `Base ${p.ce.base}x, scenarios ${p.ce.lo}x to ${p.ce.hi}x. `
          : '') + (p.note || p.model_text + '.'),
        modelLink,
      ),
    );
    if (p.ref) {
      const d = el('div');
      d.append(el('p', 'label', 'GiveWell and other references'));
      const t = el('p', null, p.ref.text + ' ');
      let shown = 0;
      p.ref.links.forEach((l) => {
        const a = anchor(l[0], l[1]);
        if (!a) return;
        if (shown++) t.append(' · ');
        t.append(a);
      });
      d.append(t);
      g.append(d);
    }
    if (p.next) g.append(field('Next', p.next));
    td.append(g);
    dr.append(td);
    return [tr, dr];
  }

  function render() {
    buildHead();
    const q = state.q.trim().toLowerCase();
    const rows = P.filter(
      (p) =>
        (state.filter === 'all' || p.group === state.filter) &&
        (!q ||
          [
            p.name,
            p.stage,
            p.now.join(' '),
            p.earlier,
            p.planned,
            p.place,
            p.what,
          ]
            .join(' ')
            .toLowerCase()
            .includes(q)),
    );
    const col = COLS.find((c) => c.key === state.sort);
    const cmp = (a, b) => {
      if (col) {
        const x = col.get(a),
          y = col.get(b);
        if (x < y) return -state.dir;
        if (x > y) return state.dir;
      }
      return (
        STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage) ||
        b.visits - a.visits
      );
    };
    const body = document.getElementById('body');
    body.textContent = '';
    if (!rows.length) {
      const tr = el('tr');
      const td = el(
        'td',
        'empty',
        'No programs match. Clear the search or pick another stage.',
      );
      td.colSpan = COLS.length;
      tr.append(td);
      body.append(tr);
      return;
    }
    if (state.grouped) {
      GROUPS.forEach((g) => {
        const items = rows.filter((p) => p.group === g.key).sort(cmp);
        if (!items.length) return;
        const tr = el('tr', 'group');
        const th = el('th');
        th.colSpan = COLS.length;
        th.scope = 'colgroup';
        th.append(g.title + ' (' + items.length + ')', el('span', null, g.def));
        tr.append(th);
        body.append(tr);
        items.forEach((p) => progRows(p).forEach((r) => body.append(r)));
      });
    } else {
      rows.sort(cmp).forEach((p) => progRows(p).forEach((r) => body.append(r)));
    }
  }

  document.getElementById('q').addEventListener('input', (e) => {
    state.q = e.target.value;
    render();
  });
  const grp = document.getElementById('grp');
  grp.checked = state.grouped;
  grp.addEventListener('change', () => {
    state.grouped = grp.checked;
    remember();
    render();
  });
  buildHeader();
  buildSortMenu();
  buildChips();
  render();
})();
