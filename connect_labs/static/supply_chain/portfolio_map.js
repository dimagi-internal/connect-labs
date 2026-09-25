/*
 * portfolio_map.js — the portfolio laid out by place.
 *
 * Reads the payload portfolio/map_data.py put in #pm-data and does every
 * filter, count and redraw in the browser. It computes no figure of its own:
 * each number shown is one the server already returned, in the unit it came
 * in. What it COUNTS is places, which implies no sum across programs.
 *
 * Three rules, each mirrored from the server:
 *   - a place with no coordinates is listed under "Not on map", never dropped;
 *   - a blocker with no place is listed under "Unplaced blockers";
 *   - rows keep the portfolio's order: filtering narrows, nothing ranks.
 *
 * Filter state lives in the URL hash so a view can be shared as a link.
 */
(function () {
  'use strict';

  var el = document.getElementById('pm-data');
  if (!el) return;
  var DATA = JSON.parse(el.textContent);
  var V = DATA.vocabulary;

  // ---------------------------------------------------------------- vocab
  var STATUS = {
    stockout: { label: 'Stocked out', color: '#ef4444' },
    negative: { label: 'Impossible balance', color: '#a855f7' },
    below_min: { label: 'Below its minimum', color: '#f59e0b' },
    ok: { label: 'Within its band', color: '#22c55e' },
    overstocked: { label: 'Above its maximum', color: '#3b82f6' },
    durable: { label: 'Equipment (no cover)', color: '#14b8a6' },
    unknown: { label: 'Cannot be computed', color: '#94a3b8' },
    origin: { label: 'Supplier site', color: '#cbd5e1', text: '#475569' },
  };
  var STATUS_ORDER = [
    'stockout',
    'negative',
    'below_min',
    'ok',
    'overstocked',
    'durable',
    'unknown',
    'origin',
  ];
  var KIND_LABEL = {
    central_store: 'Central store',
    regional_store: 'Regional store',
    facility: 'Facility',
    user_held: 'Field worker',
    supplier_site: 'Supplier site',
    in_transit: 'In transit',
    customs: 'Customs',
  };
  var KIND_RADIUS = {
    central_store: 11,
    regional_store: 9,
    facility: 7,
    supplier_site: 8,
    customs: 8,
    in_transit: 6,
    user_held: 4.5,
  };
  // How a point got its coordinates (stock/services/placement.py). Anything
  // but `recorded` is a stand-in and is drawn faded: a head office is not a store.
  var LOCATION_LABEL = {
    recorded: 'Its own location',
    org_hq: 'Stand-in: organisation HQ',
    parent: 'Stand-in: where it is restocked from',
    country: 'Stand-in: country centre',
  };
  var CATEGORY_COLOR = {
    missing: '#f59e0b',
    conflict: '#a855f7',
    threshold: '#ef4444',
  };
  var PROGRAMME_PALETTE = [
    '#6366f1',
    '#14b8a6',
    '#f97316',
    '#ec4899',
    '#84cc16',
    '#06b6d4',
    '#eab308',
    '#8b5cf6',
  ];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c];
    });
  }
  function checkLabel(kind) {
    return (V.check_labels && V.check_labels[kind]) || kind.replace(/_/g, ' ');
  }
  function num(s) {
    var n = parseFloat(s);
    return isFinite(n) ? n : null;
  }
  function fmt(amount) {
    var n = num(amount);
    if (n == null) return String(amount);
    return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  // A derived figure on the wire: {amount, unit} | {unconfirmed: [...]} | a bare decimal string.
  function figure(v) {
    if (v == null) return { text: '—', why: null };
    if (typeof v === 'string') return { text: fmt(v), why: null };
    if (v.unconfirmed)
      return { text: 'cannot say', why: v.unconfirmed.join('; ') };
    if (v.not_forecast) return { text: 'not forecast', why: v.not_forecast };
    if (v.amount != null)
      return { text: fmt(v.amount) + (v.unit ? ' ' + v.unit : ''), why: null };
    return { text: '—', why: null };
  }
  function days(iso) {
    if (!iso) return null;
    var d = new Date(iso + 'T00:00:00');
    return Math.round((d - new Date(new Date().toDateString())) / 86400000);
  }

  // ---------------------------------------------------------------- model
  var programColor = {};
  var programName = {};
  var all = []; // every active place, placed or not
  var unlocated = []; // blockers with no place
  var moving = [];
  var byId = {};
  DATA.programs.forEach(function (p, i) {
    programColor[p.program_id] =
      PROGRAMME_PALETTE[i % PROGRAMME_PALETTE.length];
    programName[p.program_id] = p.name;
    p.points.forEach(function (pt) {
      pt._placed = true;
      all.push(pt);
    });
    p.unplaced.forEach(function (pt) {
      pt._placed = false;
      all.push(pt);
    });
    p.unlocated_checks.forEach(function (c) {
      unlocated.push(
        Object.assign(
          { program_id: p.program_id, checks_url: p.checks_url },
          c,
        ),
      );
    });
    p.moving.forEach(function (m) {
      moving.push(Object.assign({ program_id: p.program_id }, m));
    });
  });
  // Points sharing one coordinate -- every store a partner runs sits on its
  // head office until someone records where it really is -- would draw as one
  // dot at any zoom. Fan them into a small ring so each stays clickable; the
  // ring is display only, and the detail panel says the location is a stand-in.
  var stacks = {};
  all.forEach(function (pt) {
    if (!pt._placed) return;
    var k = pt.lat.toFixed(5) + ',' + pt.lng.toFixed(5);
    (stacks[k] = stacks[k] || []).push(pt);
  });
  Object.keys(stacks).forEach(function (k) {
    var group = stacks[k];
    group.forEach(function (pt, i) {
      if (group.length === 1) {
        pt._x = pt.lng;
        pt._y = pt.lat;
        return;
      }
      var angle = (2 * Math.PI * i) / group.length;
      var r = 0.012 + 0.002 * group.length;
      pt._x = pt.lng + r * Math.cos(angle);
      pt._y = pt.lat + r * Math.sin(angle);
    });
  });
  all.forEach(function (pt) {
    pt._key = pt.program_id + ':' + pt.id;
    pt._approx = pt._placed && pt.location.source !== 'recorded';
    byId[pt._key] = pt;
    pt._overdue = pt.expected_inbound.filter(function (e) {
      return e.overdue;
    }).length;
    pt._undated = pt.expected_inbound.filter(function (e) {
      return !e.expected_on;
    }).length;
    pt._movingIn = moving.filter(function (m) {
      return m.program_id === pt.program_id && m.to_supply_point_id === pt.id;
    });
    pt._text = [
      pt.name,
      pt.admin_area,
      pt.managed_by,
      KIND_LABEL[pt.kind],
      programName[pt.program_id],
    ]
      .join(' ')
      .toLowerCase();
  });

  // ---------------------------------------------------------------- state
  var FACETS = [
    {
      key: 'program',
      title: 'Program',
      options: DATA.programs.map(function (p) {
        return {
          value: String(p.program_id),
          label: p.name,
          swatch: programColor[p.program_id],
        };
      }),
      test: function (pt, vals) {
        return vals.indexOf(String(pt.program_id)) >= 0;
      },
    },
    {
      key: 'status',
      title: 'Stock status',
      options: STATUS_ORDER.map(function (s) {
        return { value: s, label: STATUS[s].label, swatch: STATUS[s].color };
      }),
      test: function (pt, vals) {
        return vals.indexOf(pt.status) >= 0;
      },
    },
    {
      key: 'blocker',
      title: 'Blockers',
      options: [
        { value: 'any', label: 'Has any blocker' },
        {
          value: 'threshold',
          label: 'Past a limit you set',
          swatch: CATEGORY_COLOR.threshold,
        },
        {
          value: 'conflict',
          label: 'Records disagree',
          swatch: CATEGORY_COLOR.conflict,
        },
        {
          value: 'missing',
          label: 'A fact is missing',
          swatch: CATEGORY_COLOR.missing,
        },
        { value: 'none', label: 'No blockers' },
      ],
      test: function (pt, vals) {
        return vals.some(function (v) {
          if (v === 'any') return pt.checks.length > 0;
          if (v === 'none') return pt.checks.length === 0;
          return pt.checks.some(function (c) {
            return c.category === v;
          });
        });
      },
    },
    {
      key: 'audience',
      title: 'Who can unblock it',
      options: ['supplier', 'partner', 'internal'].map(function (a) {
        return {
          value: a,
          label: (V.audience_labels && V.audience_labels[a]) || a,
        };
      }),
      test: function (pt, vals) {
        return pt.checks.some(function (c) {
          return vals.indexOf(c.audience) >= 0;
        });
      },
    },
    {
      key: 'inbound',
      title: 'Waiting on',
      options: [
        { value: 'overdue', label: 'An order past its promised date' },
        { value: 'undated', label: 'An order with no promised date' },
        { value: 'moving', label: 'A shipment in transit' },
        { value: 'any', label: 'Anything still owed' },
        { value: 'none', label: 'Nothing owed' },
      ],
      test: function (pt, vals) {
        return vals.some(function (v) {
          if (v === 'overdue') return pt._overdue > 0;
          if (v === 'undated') return pt._undated > 0;
          if (v === 'moving') return pt._movingIn.length > 0;
          if (v === 'any')
            return pt.expected_inbound.length > 0 || pt._movingIn.length > 0;
          return pt.expected_inbound.length === 0 && pt._movingIn.length === 0;
        });
      },
    },
    {
      key: 'location',
      title: 'Location',
      options: ['recorded', 'org_hq', 'parent', 'country', 'none'].map(
        function (v) {
          return {
            value: v,
            label: v === 'none' ? 'No location at all' : LOCATION_LABEL[v],
          };
        },
      ),
      test: function (pt, vals) {
        return (
          vals.indexOf(
            pt._placed ? pt.location.source || 'recorded' : 'none',
          ) >= 0
        );
      },
    },
    {
      key: 'kind',
      title: 'Kind of place',
      options: V.kinds.map(function (k) {
        return { value: k, label: KIND_LABEL[k] || k };
      }),
      test: function (pt, vals) {
        return vals.indexOf(pt.kind) >= 0;
      },
    },
    {
      key: 'org',
      title: 'Managed by',
      options: uniq(
        all.map(function (pt) {
          return pt.managed_by || '';
        }),
      ).map(function (o) {
        return { value: o, label: o || 'Not recorded' };
      }),
      test: function (pt, vals) {
        return vals.indexOf(pt.managed_by || '') >= 0;
      },
    },
  ];
  function uniq(xs) {
    var seen = {};
    return xs
      .filter(function (x) {
        if (seen[x]) return false;
        seen[x] = true;
        return true;
      })
      .sort();
  }

  var state = { f: {}, q: '', colour: 'status', sel: null, tab: 'list' };
  function readHash() {
    try {
      var h = decodeURIComponent(location.hash.slice(1));
      if (!h) return;
      var s = JSON.parse(h);
      state.f = s.f || {};
      state.q = s.q || '';
      state.colour = s.colour || 'status';
      state.sel = s.sel || null;
    } catch (e) {
      /* a hand-edited hash is not worth breaking the page over */
    }
  }
  function writeHash() {
    var s = { f: state.f, q: state.q, colour: state.colour };
    if (state.sel) s.sel = state.sel;
    history.replaceState(null, '', '#' + encodeURIComponent(JSON.stringify(s)));
  }

  function passes(pt, skipKey) {
    if (state.q && pt._text.indexOf(state.q.toLowerCase()) < 0) return false;
    for (var i = 0; i < FACETS.length; i++) {
      var f = FACETS[i];
      if (f.key === skipKey) continue;
      var vals = state.f[f.key];
      if (vals && vals.length && !f.test(pt, vals)) return false;
    }
    return true;
  }
  function visible() {
    return all.filter(function (pt) {
      return passes(pt);
    });
  }

  // ---------------------------------------------------------------- rail
  function renderFacets() {
    var html = FACETS.map(function (f) {
      if (!f.options.length) return '';
      // Counts are over the places every OTHER facet leaves in scope, so each
      // number says what clicking it would show.
      var pool = all.filter(function (pt) {
        return passes(pt, f.key);
      });
      var chosen = state.f[f.key] || [];
      var opts = f.options
        .map(function (o) {
          var n = pool.filter(function (pt) {
            return f.test(pt, [o.value]);
          }).length;
          var on = chosen.indexOf(o.value) >= 0;
          return (
            '<label class="pm-opt' +
            (n === 0 && !on ? ' pm-zero' : '') +
            '">' +
            '<input type="checkbox" data-facet="' +
            f.key +
            '" value="' +
            esc(o.value) +
            '"' +
            (on ? ' checked' : '') +
            '>' +
            (o.swatch
              ? '<span class="pm-sw" style="background:' +
                o.swatch +
                '"></span>'
              : '') +
            '<span>' +
            esc(o.label) +
            '</span><span class="pm-n">' +
            n +
            '</span></label>'
          );
        })
        .join('');
      return (
        '<div class="pm-facet"><h3><span>' +
        esc(f.title) +
        '</span>' +
        (chosen.length
          ? '<button type="button" data-clear="' + f.key + '">clear</button>'
          : '') +
        '</h3>' +
        opts +
        '</div>'
      );
    }).join('');
    document.getElementById('pm-facets').innerHTML = html;
  }

  document.getElementById('pm-facets').addEventListener('change', function (e) {
    var t = e.target;
    if (!t.dataset.facet) return;
    var cur = state.f[t.dataset.facet] || [];
    cur = t.checked
      ? cur.concat([t.value])
      : cur.filter(function (v) {
          return v !== t.value;
        });
    state.f[t.dataset.facet] = cur;
    update();
  });
  document.getElementById('pm-facets').addEventListener('click', function (e) {
    var k = e.target.dataset && e.target.dataset.clear;
    if (!k) return;
    delete state.f[k];
    update();
  });
  document.getElementById('pm-q').addEventListener('input', function (e) {
    state.q = e.target.value;
    update();
  });
  document.getElementById('pm-reset').addEventListener('click', function () {
    state.f = {};
    state.q = '';
    document.getElementById('pm-q').value = '';
    update();
  });

  // ---------------------------------------------------------------- KPIs
  // Each tile is a count of PLACES and a one-click filter. Nothing here is a
  // quantity, so nothing here adds a carton to a jerry can.
  var KPIS = [
    { label: 'Places', facet: null },
    {
      label: 'Stocked out',
      facet: 'status',
      value: 'stockout',
      color: STATUS.stockout.color,
    },
    {
      label: 'Below minimum',
      facet: 'status',
      value: 'below_min',
      color: STATUS.below_min.color,
    },
    {
      label: 'With a blocker',
      facet: 'blocker',
      value: 'any',
      color: '#dc2626',
    },
    {
      label: 'Order overdue',
      facet: 'inbound',
      value: 'overdue',
      color: '#ea580c',
    },
    {
      label: 'Shipment in transit',
      facet: 'inbound',
      value: 'moving',
      color: '#0ea5e9',
    },
    { label: 'Not on the map', facet: null, tab: 'unplaced', color: '#64748b' },
  ];
  function renderKpis(vis) {
    var html = KPIS.map(function (k, i) {
      var n,
        on = false;
      if (!k.facet && !k.tab) n = vis.length;
      else if (k.tab)
        n = vis.filter(function (pt) {
          return !pt._placed;
        }).length;
      else {
        var f = FACETS.filter(function (x) {
          return x.key === k.facet;
        })[0];
        n = vis.filter(function (pt) {
          return f.test(pt, [k.value]);
        }).length;
        on = (state.f[k.facet] || []).indexOf(k.value) >= 0;
      }
      return (
        '<button type="button" class="pm-kpi' +
        (on ? ' pm-on' : '') +
        '" data-kpi="' +
        i +
        '">' +
        '<div class="pm-v" style="color:' +
        (n && k.color ? k.color : '#111827') +
        '">' +
        n +
        '</div>' +
        '<div class="pm-l">' +
        esc(k.label) +
        '</div></button>'
      );
    }).join('');
    document.getElementById('pm-kpis').innerHTML = html;
  }
  document.getElementById('pm-kpis').addEventListener('click', function (e) {
    var b = e.target.closest('[data-kpi]');
    if (!b) return;
    var k = KPIS[+b.dataset.kpi];
    if (k.tab) return setTab(k.tab);
    if (!k.facet) {
      state.f = {};
      return update();
    }
    var cur = state.f[k.facet] || [];
    state.f[k.facet] = cur.indexOf(k.value) >= 0 ? [] : [k.value];
    update();
  });

  // ---------------------------------------------------------------- panel
  function setTab(tab) {
    state.tab = tab;
    ['list', 'unplaced', 'elsewhere'].forEach(function (t) {
      document
        .getElementById('pm-tab-' + t)
        .classList.toggle('pm-on', t === tab);
    });
    renderPanel(visible());
  }
  document.querySelector('.pm-tabbar').addEventListener('click', function (e) {
    var t = e.target.dataset && e.target.dataset.tab;
    if (t) {
      state.sel = null;
      setTab(t);
      writeHash();
    }
  });

  function statusChip(pt) {
    var s = STATUS[pt.status] || STATUS.unknown;
    return (
      '<span class="pm-chip" style="background:' +
      s.color +
      '26;color:' +
      (s.text || s.color) +
      '">' +
      esc(s.label) +
      '</span>'
    );
  }
  function listItem(pt) {
    var bits = [];
    if (pt.checks.length)
      bits.push(
        '<span style="color:#dc2626">' +
          pt.checks.length +
          ' blocker' +
          (pt.checks.length > 1 ? 's' : '') +
          '</span>',
      );
    if (pt._overdue)
      bits.push(
        '<span style="color:#ea580c">' + pt._overdue + ' overdue</span>',
      );
    if (pt._movingIn.length)
      bits.push(
        '<span style="color:#0284c7">' +
          pt._movingIn.length +
          ' in transit</span>',
      );
    return (
      '<button type="button" class="pm-list-item' +
      (state.sel === pt._key ? ' pm-sel' : '') +
      '" data-key="' +
      pt._key +
      '">' +
      '<div class="flex items-center gap-2"><span class="pm-sw" style="background:' +
      programColor[pt.program_id] +
      '"></span>' +
      '<span class="text-sm font-medium text-gray-900 truncate">' +
      esc(pt.name) +
      '</span>' +
      (pt._placed
        ? ''
        : '<i class="fa-solid fa-location-dot text-gray-300 ml-auto" title="No coordinates"></i>') +
      '</div>' +
      '<div class="text-xs text-gray-500 mt-0.5 ml-4">' +
      esc(KIND_LABEL[pt.kind] || pt.kind) +
      (pt.admin_area ? ' · ' + esc(pt.admin_area) : '') +
      ' · ' +
      esc(programName[pt.program_id]) +
      '</div>' +
      '<div class="text-xs mt-1 ml-4 flex flex-wrap gap-x-2 gap-y-1 items-center">' +
      statusChip(pt) +
      bits.join('') +
      '</div>' +
      '</button>'
    );
  }

  function renderPanel(vis) {
    var panel = document.getElementById('pm-panel');
    var unplacedVis = vis.filter(function (pt) {
      return !pt._placed;
    });
    document.getElementById('pm-tab-list').textContent =
      'Places (' + vis.length + ')';
    document.getElementById('pm-tab-unplaced').textContent =
      'Not on map (' + unplacedVis.length + ')';
    var elsewhere = unlocated.filter(function (c) {
      var pf = state.f.program;
      return !pf || !pf.length || pf.indexOf(String(c.program_id)) >= 0;
    });
    document.getElementById('pm-tab-elsewhere').textContent =
      'Unplaced blockers (' + elsewhere.length + ')';

    if (state.sel && byId[state.sel]) {
      panel.innerHTML = detail(byId[state.sel]);
      return;
    }
    if (state.tab === 'unplaced') {
      panel.innerHTML =
        '<p class="text-xs text-gray-600 px-4 py-3 bg-gray-50 border-b border-gray-100">' +
        'These places have no coordinates, so the map cannot draw them. They are still counted everywhere on this page. ' +
        'Set a latitude and longitude on each to put it on the map.</p>' +
        (unplacedVis.length
          ? unplacedVis.map(listItem).join('')
          : '<p class="text-sm text-gray-500 p-4">Every place in view is on the map.</p>');
      return;
    }
    if (state.tab === 'elsewhere') {
      panel.innerHTML =
        '<p class="text-xs text-gray-600 px-4 py-3 bg-gray-50 border-b border-gray-100">' +
        'Blockers about a quote, an award or the catalogue belong to no place, so they are not on the map. They are listed here so the map never looks cleaner than the chain is.</p>' +
        (elsewhere.length
          ? elsewhere
              .map(function (c) {
                return (
                  '<div class="px-4 py-2.5 border-b border-gray-100 text-sm">' +
                  '<div class="flex items-center gap-2"><span class="pm-sw" style="background:' +
                  CATEGORY_COLOR[c.category] +
                  '"></span>' +
                  '<span class="font-medium text-gray-900">' +
                  esc(checkLabel(c.kind)) +
                  '</span></div>' +
                  '<div class="text-xs text-gray-500 ml-4">' +
                  esc(c.subject.label || '') +
                  ' · ' +
                  esc(programName[c.program_id]) +
                  (c.days_open != null
                    ? ' · ' + c.days_open + ' days open'
                    : '') +
                  '</div>' +
                  '<a class="text-xs text-brand-indigo hover:underline ml-4" href="' +
                  esc(c.checks_url) +
                  '">Open in Checks</a></div>'
                );
              })
              .join('')
          : '<p class="text-sm text-gray-500 p-4">None.</p>');
      return;
    }
    panel.innerHTML = vis.length
      ? vis.map(listItem).join('')
      : '<p class="text-sm text-gray-500 p-4">No place matches these filters.</p>';
  }

  function band(pt) {
    var mos = num(pt.months_of_stock);
    var lo = num(pt.min_months_of_stock),
      hi = num(pt.max_months_of_stock);
    if (mos == null || (lo == null && hi == null)) return '';
    var top = Math.max(mos, hi || 0, lo || 0) * 1.25 || 1;
    var pct = function (x) {
      return Math.max(0, Math.min(100, (x / top) * 100));
    };
    return (
      '<div class="pm-band"><div class="pm-ok" style="left:' +
      pct(lo || 0) +
      '%;right:' +
      (100 - pct(hi || top)) +
      '%"></div>' +
      '<div class="pm-mark" style="left:calc(' +
      pct(mos) +
      '% - 1px)"></div></div>' +
      '<div class="flex justify-between text-gray-500" style="font-size:11px"><span>0</span><span>band ' +
      (lo != null ? fmt(lo) : '–') +
      '–' +
      (hi != null ? fmt(hi) : '–') +
      ' months</span></div>'
    );
  }

  function detail(pt) {
    var onHand = figure(pt.on_hand);
    var mos = figure(pt.months_of_stock);
    var reported = figure(pt.reported);
    var h = '<div class="pm-detail p-4">';
    h +=
      '<button type="button" class="text-xs text-brand-indigo hover:underline mb-2" data-back>&larr; Back to list</button>';
    h +=
      '<div class="flex items-start gap-2"><span class="pm-sw mt-1.5" style="background:' +
      programColor[pt.program_id] +
      '"></span><div>';
    h +=
      '<div class="text-base font-semibold text-gray-900">' +
      esc(pt.name) +
      '</div>';
    h +=
      '<div class="text-xs text-gray-500">' +
      esc(KIND_LABEL[pt.kind] || pt.kind) +
      (pt.admin_area ? ' · ' + esc(pt.admin_area) : '') +
      (pt.managed_by ? ' · managed by ' + esc(pt.managed_by) : '') +
      '</div>';
    h +=
      '<div class="text-xs text-gray-500">' +
      esc(programName[pt.program_id]) +
      '</div></div></div>';
    h += '<div class="mt-2">' + statusChip(pt) + '</div>';
    if (pt._approx) {
      h +=
        '<div class="mt-3 text-xs rounded-md bg-amber-50 border border-amber-200 p-2 text-amber-900">' +
        '<i class="fa-solid fa-location-crosshairs mr-1"></i>Shown at ' +
        esc(pt.location.label || LOCATION_LABEL[pt.location.source]) +
        (pt.location.precision
          ? ' — accurate to the ' + esc(pt.location.precision)
          : '') +
        ', not where this place really is. <a class="underline" href="' +
        esc(pt.links.edit) +
        '">Record its location</a></div>';
    }
    if (!pt._placed) {
      h +=
        '<div class="mt-3 text-xs rounded-md bg-gray-50 border border-gray-200 p-2 text-gray-700">No coordinates, so it is not on the map. ' +
        '<a class="text-brand-indigo hover:underline" href="' +
        esc(pt.links.edit) +
        '">Set its location</a></div>';
    }

    h += '<h4>Stock</h4><div class="grid grid-cols-2 gap-2 text-sm">';
    h +=
      '<div><div class="text-xs text-gray-500">On hand (ledger)</div><div class="font-semibold"' +
      (onHand.why ? ' title="' + esc(onHand.why) + '"' : '') +
      '>' +
      esc(onHand.text) +
      '</div></div>';
    h +=
      '<div><div class="text-xs text-gray-500">Months of stock</div><div class="font-semibold"' +
      (mos.why ? ' title="' + esc(mos.why) + '"' : '') +
      '>' +
      esc(mos.text) +
      '</div></div>';
    h +=
      '<div><div class="text-xs text-gray-500">Last counted</div><div>' +
      esc(reported.text) +
      (pt.reported_on
        ? ' <span class="text-xs text-gray-500">on ' +
          esc(pt.reported_on) +
          '</span>'
        : '') +
      '</div></div>';
    var dts = num(pt.days_to_stockout);
    h +=
      '<div><div class="text-xs text-gray-500">Runs out in</div><div>' +
      (dts != null ? fmt(Math.round(dts)) + ' days' : '—') +
      '</div></div>';
    h += '</div>' + band(pt);
    if (onHand.why)
      h +=
        '<p class="text-xs text-gray-500 mt-1">Why not: ' +
        esc(onHand.why) +
        '</p>';

    h += '<h4>Blockers (' + pt.checks.length + ')</h4>';
    if (!pt.checks.length)
      h += '<p class="text-sm text-gray-500">Nothing open at this place.</p>';
    pt.checks.forEach(function (c) {
      h +=
        '<div class="mb-2 text-sm border-l-2 pl-2" style="border-color:' +
        CATEGORY_COLOR[c.category] +
        '">' +
        '<div class="font-medium text-gray-900">' +
        esc(checkLabel(c.kind)) +
        '</div>' +
        '<div class="text-xs text-gray-500">' +
        esc((V.audience_labels || {})[c.audience] || c.audience) +
        (c.days_open != null ? ' · ' + c.days_open + ' days open' : '') +
        (c.subject && c.subject.type !== 'supply_point'
          ? ' · ' + esc(c.subject.label || '')
          : '') +
        '</div></div>';
    });

    h += '<h4>Still owed to it</h4>';
    if (!pt.expected_inbound.length && !pt._movingIn.length)
      h +=
        '<p class="text-sm text-gray-500">Nothing is owed to this place.</p>';
    pt.expected_inbound.forEach(function (e) {
      var d = days(e.expected_on);
      var when = !e.expected_on
        ? '<span style="color:#c2410c">no date promised</span>'
        : e.overdue
          ? '<span style="color:#dc2626">' + Math.abs(d) + ' days late</span>'
          : 'due ' + esc(e.expected_on);
      h +=
        '<div class="text-sm mb-1.5"><a class="text-brand-indigo hover:underline" href="' +
        esc(e.order_url) +
        '">' +
        esc(e.reference || 'Order #' + e.contract_id) +
        '</a> · ' +
        esc(figure(e.outstanding).text) +
        (e.item_name ? ' ' + esc(e.item_name) : '') +
        '<div class="text-xs text-gray-500">from ' +
        esc(e.supplier.name) +
        ' · ' +
        when +
        '</div></div>';
    });
    pt._movingIn.forEach(function (m) {
      h +=
        '<div class="text-sm mb-1.5"><i class="fa-solid fa-truck-fast mr-1" style="color:#0284c7"></i><a class="text-brand-indigo hover:underline" href="' +
        esc(m.url) +
        '">' +
        esc(m.reference || 'Shipment #' + m.shipment_id) +
        '</a> · ' +
        esc(m.status.replace(/_/g, ' ')) +
        '<div class="text-xs text-gray-500">' +
        esc(m.supplier) +
        (m.expected_on
          ? ' · expected ' + esc(m.expected_on)
          : ' · no expected date') +
        '</div></div>';
    });

    h +=
      '<h4>Open</h4><div class="flex flex-wrap gap-3 text-sm">' +
      '<a class="text-brand-indigo hover:underline" href="' +
      esc(pt.links.movements) +
      '">Movements</a>' +
      '<a class="text-brand-indigo hover:underline" href="' +
      esc(pt.links.network) +
      '">Network</a>' +
      '<a class="text-brand-indigo hover:underline" href="' +
      esc(pt.links.edit) +
      '">Edit place</a></div>';
    return h + '</div>';
  }

  document.getElementById('pm-panel').addEventListener('click', function (e) {
    if (e.target.closest('[data-back]')) {
      state.sel = null;
      renderPanel(visible());
      paintSelection();
      writeHash();
      return;
    }
    var b = e.target.closest('[data-key]');
    if (b) select(b.dataset.key, true);
  });

  function select(key, fly) {
    state.sel = key;
    var pt = byId[key];
    renderPanel(visible());
    paintSelection();
    writeHash();
    if (fly && map && pt && pt._placed)
      // Past clusterMaxZoom, so the place is its own dot rather than a member of one.
      map.flyTo({
        center: [pt._x, pt._y],
        zoom: Math.max(map.getZoom(), 11),
        speed: 1.4,
      });
  }

  // ---------------------------------------------------------------- colour
  document.getElementById('pm-colour').addEventListener('click', function (e) {
    var c = e.target.dataset && e.target.dataset.colour;
    if (!c) return;
    state.colour = c;
    update();
  });
  function colourFor(pt) {
    if (state.colour === 'program') return programColor[pt.program_id];
    if (state.colour === 'blockers') {
      if (!pt.checks.length) return '#475569';
      var cats = pt.checks.map(function (c) {
        return c.category;
      });
      if (cats.indexOf('threshold') >= 0) return CATEGORY_COLOR.threshold;
      if (cats.indexOf('conflict') >= 0) return CATEGORY_COLOR.conflict;
      return CATEGORY_COLOR.missing;
    }
    return (STATUS[pt.status] || STATUS.unknown).color;
  }
  // A rank per colour mode, lowest = most in need, so a cluster can take its
  // worst member's colour with one `min`. The order is the legend's order.
  function rankFor(pt) {
    if (state.colour === 'program') return 0;
    if (state.colour === 'blockers') {
      var cats = pt.checks.map(function (c) {
        return c.category;
      });
      return cats.indexOf('threshold') >= 0
        ? 0
        : cats.indexOf('conflict') >= 0
          ? 1
          : cats.length
            ? 2
            : 3;
    }
    var i = STATUS_ORDER.indexOf(pt.status);
    return i < 0 ? STATUS_ORDER.length : i;
  }
  function clusterColour() {
    var pairs;
    if (state.colour === 'program') return '#c7d2fe';
    if (state.colour === 'blockers')
      pairs = [
        CATEGORY_COLOR.threshold,
        CATEGORY_COLOR.conflict,
        CATEGORY_COLOR.missing,
        '#94a3b8',
      ];
    else
      pairs = STATUS_ORDER.map(function (s) {
        return STATUS[s].color;
      });
    var expr = ['match', ['get', 'worst']];
    pairs.forEach(function (c, i) {
      expr.push(i, c);
    });
    expr.push('#94a3b8');
    return expr;
  }
  function renderLegend() {
    var rows;
    if (state.colour === 'program') {
      rows = DATA.programs.map(function (p) {
        return [programColor[p.program_id], p.name];
      });
    } else if (state.colour === 'blockers') {
      rows = [
        [CATEGORY_COLOR.threshold, 'Past a limit you set'],
        [CATEGORY_COLOR.conflict, 'Records disagree'],
        [CATEGORY_COLOR.missing, 'A fact is missing'],
        ['#475569', 'No blockers'],
      ];
    } else {
      rows = STATUS_ORDER.map(function (s) {
        return [STATUS[s].color, STATUS[s].label];
      });
    }
    var html = rows
      .map(function (r) {
        return (
          '<div class="pm-row"><span class="pm-sw" style="background:' +
          r[0] +
          '"></span>' +
          esc(r[1]) +
          '</div>'
        );
      })
      .join('');
    html +=
      '<div class="pm-row" style="margin-top:6px;opacity:.8"><span class="pm-sw" style="background:transparent;border:2px solid #f43f5e"></span>Ring: open blocker</div>';
    html +=
      '<div class="pm-row" style="opacity:.8"><span style="width:14px;border-top:2px dashed #38bdf8"></span>Shipment in transit</div>';
    html +=
      '<div class="pm-row" style="opacity:.8"><span class="pm-sw" style="background:#94a3b8;opacity:.5"></span>Faded: location is a stand-in</div>';
    document.getElementById('pm-legend').innerHTML = html;
    Array.prototype.forEach.call(
      document.querySelectorAll('#pm-colour button'),
      function (b) {
        b.classList.toggle('pm-on', b.dataset.colour === state.colour);
      },
    );
  }

  // ---------------------------------------------------------------- map
  var map = null;
  var mapReady = false;
  var fitted = false;
  var mapEl = document.getElementById('pm-map');
  var anyPlaced = all.some(function (pt) {
    return pt._placed;
  });

  function noMap(msg) {
    mapEl.innerHTML = '<div class="pm-nomap"><div>' + msg + '</div></div>';
  }
  if (!window.mapboxgl || !window.ConnectMap) {
    noMap('The map library did not load. The lists beside it are complete.');
  } else if (!window.MAPBOX_TOKEN) {
    noMap(
      'No map token is configured on this server (MAPBOX_TOKEN), so the basemap cannot draw. The lists beside it are complete.',
    );
  } else {
    map = ConnectMap.createMap(mapEl, { center: [10, 5], zoom: 2.4 });
    map.addControl(
      new mapboxgl.NavigationControl({ showCompass: false }),
      'top-left',
    );
    map.on('load', function () {
      ConnectMap.calmBasemap(map);
      map.addSource('pm-links', { type: 'geojson', data: empty() });
      map.addSource('pm-moving', { type: 'geojson', data: empty() });
      // Clustered, so twenty field workers around one town read as twenty at
      // a glance rather than as one dot. A cluster carries its worst member's
      // colour and the sum of its blockers -- a count of findings, never of stock.
      map.addSource('pm-points', {
        type: 'geojson',
        data: empty(),
        cluster: true,
        clusterRadius: 38,
        clusterMaxZoom: 10,
        clusterProperties: {
          blockers: ['+', ['get', 'blockers']],
          worst: ['min', ['get', 'rank']],
        },
      });
      map.addLayer({
        id: 'pm-links',
        type: 'line',
        source: 'pm-links',
        paint: {
          'line-color': '#64748b',
          'line-width': 1.2,
          'line-opacity': 0.55,
        },
      });
      map.addLayer({
        id: 'pm-moving',
        type: 'line',
        source: 'pm-moving',
        paint: {
          'line-color': '#38bdf8',
          'line-width': 2.4,
          'line-dasharray': [0, 2, 2],
        },
      });
      map.addLayer({
        id: 'pm-clusters',
        type: 'circle',
        source: 'pm-points',
        filter: ['has', 'point_count'],
        paint: {
          'circle-radius': ['step', ['get', 'point_count'], 15, 10, 19, 50, 24],
          'circle-color': clusterColour(),
          'circle-opacity': 0.9,
          'circle-stroke-color': [
            'case',
            ['>', ['get', 'blockers'], 0],
            '#f43f5e',
            '#0b1020',
          ],
          'circle-stroke-width': [
            'case',
            ['>', ['get', 'blockers'], 0],
            3,
            1.5,
          ],
        },
      });
      map.addLayer({
        id: 'pm-cluster-count',
        type: 'symbol',
        source: 'pm-points',
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          'text-size': 12,
          'text-font': ['DIN Pro Bold', 'Arial Unicode MS Bold'],
          'text-allow-overlap': true,
        },
        paint: { 'text-color': '#0b1020' },
      });
      map.on('click', 'pm-clusters', function (e) {
        var f = e.features[0];
        map
          .getSource('pm-points')
          .getClusterExpansionZoom(
            f.properties.cluster_id,
            function (err, zoom) {
              if (!err)
                map.easeTo({
                  center: f.geometry.coordinates,
                  zoom: zoom + 0.3,
                });
            },
          );
      });
      map.on('mouseenter', 'pm-clusters', function () {
        map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', 'pm-clusters', function () {
        map.getCanvas().style.cursor = '';
      });
      map.addLayer({
        id: 'pm-halo',
        type: 'circle',
        source: 'pm-points',
        filter: [
          'all',
          ['!', ['has', 'point_count']],
          ['==', ['get', 'status'], 'stockout'],
        ],
        paint: {
          'circle-radius': ['*', ['get', 'r'], 2.2],
          'circle-color': STATUS.stockout.color,
          'circle-opacity': 0.18,
          'circle-blur': 0.6,
        },
      });
      map.addLayer({
        id: 'pm-points',
        type: 'circle',
        source: 'pm-points',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-radius': ['get', 'r'],
          'circle-color': ['get', 'color'],
          'circle-opacity': ['case', ['get', 'approx'], 0.5, 1],
          'circle-stroke-color': [
            'case',
            ['get', 'sel'],
            '#ffffff',
            ['>', ['get', 'blockers'], 0],
            '#f43f5e',
            '#0b1020',
          ],
          'circle-stroke-width': [
            'case',
            ['get', 'sel'],
            3.5,
            ['>', ['get', 'blockers'], 0],
            2.4,
            1,
          ],
        },
      });
      map.addLayer({
        id: 'pm-badges',
        type: 'symbol',
        source: 'pm-points',
        filter: [
          'all',
          ['!', ['has', 'point_count']],
          ['>', ['get', 'blockers'], 0],
        ],
        layout: {
          'text-field': ['to-string', ['get', 'blockers']],
          'text-size': 10,
          'text-offset': [0.9, -0.9],
          'text-font': ['DIN Pro Bold', 'Arial Unicode MS Bold'],
          'text-allow-overlap': true,
        },
        paint: {
          'text-color': '#fff',
          'text-halo-color': '#e11d48',
          'text-halo-width': 2.2,
        },
      });
      map.addLayer({
        id: 'pm-names',
        type: 'symbol',
        source: 'pm-points',
        filter: ['!', ['has', 'point_count']],
        layout: {
          visibility: 'none',
          'text-field': ['get', 'name'],
          'text-size': 11,
          'text-offset': [0, 1.3],
          'text-anchor': 'top',
          'text-font': ['DIN Pro Medium', 'Arial Unicode MS Regular'],
        },
        paint: {
          'text-color': '#e2e8f0',
          'text-halo-color': '#0b1020',
          'text-halo-width': 1.4,
        },
      });

      var popup = new mapboxgl.Popup({
        closeButton: false,
        closeOnClick: false,
        offset: 10,
      });
      map.on('mouseenter', 'pm-points', function (e) {
        map.getCanvas().style.cursor = 'pointer';
        var pt = byId[e.features[0].properties.key];
        if (!pt) return;
        popup
          .setLngLat([pt._x, pt._y])
          .setHTML(
            '<strong>' +
              esc(pt.name) +
              '</strong><br><span style="color:#6b7280">' +
              esc(KIND_LABEL[pt.kind] || pt.kind) +
              ' · ' +
              esc(programName[pt.program_id]) +
              '</span><br>' +
              esc(figure(pt.on_hand).text) +
              ' on hand' +
              (pt.checks.length
                ? '<br><span style="color:#dc2626">' +
                  pt.checks.length +
                  ' blocker' +
                  (pt.checks.length > 1 ? 's' : '') +
                  '</span>'
                : ''),
          )
          .addTo(map);
      });
      map.on('mouseleave', 'pm-points', function () {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });
      map.on('click', 'pm-points', function (e) {
        select(e.features[0].properties.key, false);
      });

      // Marching dashes: a consignment in motion should look like one.
      var step = 0;
      var DASHES = [
        [0, 4, 3],
        [0.5, 4, 2.5],
        [1, 4, 2],
        [1.5, 4, 1.5],
        [2, 4, 1],
        [2.5, 4, 0.5],
        [3, 4, 0],
        [0, 0.5, 3, 3.5],
        [0, 1, 3, 3],
        [0, 1.5, 3, 2.5],
        [0, 2, 3, 2],
        [0, 2.5, 3, 1.5],
        [0, 3, 3, 1],
        [0, 3.5, 3, 0.5],
      ];
      setInterval(function () {
        if (!map.getLayer('pm-moving')) return;
        step = (step + 1) % DASHES.length;
        map.setPaintProperty('pm-moving', 'line-dasharray', DASHES[step]);
      }, 90);

      mapReady = true;
      drawMap(visible());
      if (!anyPlaced) {
        var note = document.createElement('div');
        note.className = 'pm-nomap';
        note.style.pointerEvents = 'none';
        note.innerHTML =
          '<div style="background:rgba(15,23,42,.86);padding:14px 18px;border-radius:10px;max-width:360px">' +
          'None of these places has coordinates yet, so there is nothing to draw. ' +
          'Every one is listed under <strong>Not on map</strong>, with its status and blockers.</div>';
        mapEl.parentNode.appendChild(note);
      }
    });
  }

  function empty() {
    return { type: 'FeatureCollection', features: [] };
  }

  // A gentle curve between two places, so a route reads as a route rather
  // than as a border line on the basemap.
  function arc(a, b) {
    var mx = (a[0] + b[0]) / 2,
      my = (a[1] + b[1]) / 2;
    var dx = b[0] - a[0],
      dy = b[1] - a[1];
    var c = [mx - dy * 0.18, my + dx * 0.18];
    var pts = [];
    for (var t = 0; t <= 1.0001; t += 0.05) {
      var u = 1 - t;
      pts.push([
        u * u * a[0] + 2 * u * t * c[0] + t * t * b[0],
        u * u * a[1] + 2 * u * t * c[1] + t * t * b[1],
      ]);
    }
    return pts;
  }

  function drawMap(vis) {
    if (!map || !mapReady) return;
    var placed = vis.filter(function (pt) {
      return pt._placed;
    });
    var shown = {};
    placed.forEach(function (pt) {
      shown[pt._key] = pt;
    });
    map.getSource('pm-points').setData({
      type: 'FeatureCollection',
      features: placed.map(function (pt) {
        return {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [pt._x, pt._y] },
          properties: {
            approx: pt._approx,
            key: pt._key,
            name: pt.name,
            status: pt.status,
            color: colourFor(pt),
            r: KIND_RADIUS[pt.kind] || 6,
            blockers: pt.checks.length,
            sel: state.sel === pt._key,
            rank: rankFor(pt),
          },
        };
      }),
    });

    var links = [];
    if (document.getElementById('pm-links').checked) {
      placed.forEach(function (pt) {
        var parent = pt.parent_id && byId[pt.program_id + ':' + pt.parent_id];
        if (parent && parent._placed && shown[parent._key]) {
          links.push({
            type: 'Feature',
            geometry: {
              type: 'LineString',
              coordinates: [
                [parent._x, parent._y],
                [pt._x, pt._y],
              ],
            },
            properties: {},
          });
        }
      });
    }
    map
      .getSource('pm-links')
      .setData({ type: 'FeatureCollection', features: links });

    var routes = [];
    if (document.getElementById('pm-moving').checked) {
      moving.forEach(function (m) {
        var to = byId[m.program_id + ':' + m.to_supply_point_id];
        var from =
          m.from_supply_point_id &&
          byId[m.program_id + ':' + m.from_supply_point_id];
        // Drawn only when both ends are real places. A shipment with no
        // recorded origin is shown on its destination's card, not from a
        // point invented to hang a line on.
        if (to && from && to._placed && from._placed && shown[to._key]) {
          routes.push({
            type: 'Feature',
            geometry: {
              type: 'LineString',
              coordinates: arc([from._x, from._y], [to._x, to._y]),
            },
            properties: {},
          });
        }
      });
    }
    map
      .getSource('pm-moving')
      .setData({ type: 'FeatureCollection', features: routes });
    map.setPaintProperty('pm-clusters', 'circle-color', clusterColour());
    map.setLayoutProperty(
      'pm-names',
      'visibility',
      document.getElementById('pm-labels').checked ? 'visible' : 'none',
    );

    if (!fitted && placed.length) {
      fitted = true;
      if (placed.length === 1)
        map.jumpTo({ center: [placed[0]._x, placed[0]._y], zoom: 8 });
      else {
        var b = new mapboxgl.LngLatBounds();
        placed.forEach(function (pt) {
          b.extend([pt._x, pt._y]);
        });
        map.fitBounds(b, { padding: 60, duration: 0, maxZoom: 9 });
      }
    }
  }
  function paintSelection() {
    drawMap(visible());
  }
  ['pm-links', 'pm-moving', 'pm-labels'].forEach(function (id) {
    document.getElementById(id).addEventListener('change', function () {
      drawMap(visible());
    });
  });

  // ---------------------------------------------------------------- loop
  function update() {
    var vis = visible();
    renderFacets();
    renderKpis(vis);
    renderLegend();
    renderPanel(vis);
    drawMap(vis);
    writeHash();
  }

  readHash();
  document.getElementById('pm-q').value = state.q;
  update();
})();
