/*
 * portfolio_map.js — the portfolio laid out by place, drilled portfolio → program → place.
 *
 * Reads the payload portfolio/map_data.py put in #pm-data. Computes no figure
 * of its own: every number shown is one the server returned, in its own unit.
 * What it COUNTS is places and orders, which implies no sum across programs.
 *
 * What the map draws, and why so little:
 *   - stores, coloured by ONE thing: blocked / waiting on an answer / nothing open;
 *   - the routes supplies are on their way along (supplier → the store owed);
 *   - field workers only once you are zoomed in far enough to tell them apart.
 * Most of what stands in a chain's way is not at a place (a quote that cannot
 * be compared, a product failing its spec), so the PANEL carries the blockers,
 * grouped by stage, and the map shows where the place-bound ones bite.
 *
 * Nothing is dropped: a place with no location and a blocker with no place are
 * listed in the panel. Nothing is ranked: programs keep the portfolio's order.
 * State lives in the URL hash so a view can be shared as a link.
 */
(function () {
  'use strict';

  var el = document.getElementById('pm-data');
  if (!el) return;
  var DATA = JSON.parse(el.textContent);
  var V = DATA.vocabulary;

  // ---------------------------------------------------------------- vocab
  // One encoding on the map. Classified from facts the record holds -- a
  // check's category, a status against the place's own band, a promised date
  // passed -- never from a judgement about what matters most.
  var ATTN = {
    blocked: { label: 'Blocked', color: '#ef4444' },
    waiting: { label: 'Waiting on an answer', color: '#f59e0b' },
    clear: { label: 'Nothing open', color: '#94a3b8' },
  };
  var STAGES = [
    { key: 'source', label: 'Source' },
    { key: 'order', label: 'Order' },
    { key: 'deliver', label: 'Deliver' },
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
    central_store: 10,
    regional_store: 8,
    facility: 7,
    supplier_site: 7,
    customs: 7,
    in_transit: 6,
    user_held: 4,
  };
  var STATUS_LABEL = {
    stockout: 'Stocked out',
    negative: 'Impossible balance',
    below_min: 'Below its minimum',
    ok: 'Within its band',
    overstocked: 'Above its maximum',
    durable: 'Equipment',
    unknown: 'Stock cannot be computed',
    origin: 'Supplier site',
  };
  var LOCATION_LABEL = {
    recorded: 'Its own location',
    org_hq: 'Organisation head office',
    parent: 'Where it is restocked from',
    country: 'Country centre',
  };
  var PROGRAM_PALETTE = [
    '#6366f1',
    '#14b8a6',
    '#f97316',
    '#ec4899',
    '#84cc16',
    '#06b6d4',
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
  function audienceLabel(a) {
    return (V.audience_labels && V.audience_labels[a]) || a;
  }
  function num(s) {
    var n = parseFloat(s);
    return isFinite(n) ? n : null;
  }
  function fmt(n) {
    var v = num(n);
    return v == null
      ? String(n)
      : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
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
  function plural(n, one, many) {
    return n + ' ' + (n === 1 ? one : many || one + 's');
  }
  function daysLate(iso) {
    if (!iso) return null;
    return Math.round(
      (new Date(new Date().toDateString()) - new Date(iso + 'T00:00:00')) /
        86400000,
    );
  }

  // ---------------------------------------------------------------- model
  var programs = DATA.programs;
  var progById = {};
  var places = []; // every active place, placed or not
  var placeByKey = {};
  var suppliers = [];
  programs.forEach(function (p, i) {
    p._color = PROGRAM_PALETTE[i % PROGRAM_PALETTE.length];
    progById[p.program_id] = p;
    p.points.concat(p.unplaced).forEach(function (pt) {
      pt._placed = p.points.indexOf(pt) >= 0;
      pt._key = p.program_id + ':' + pt.id;
      pt._orders = p.orders.filter(function (o) {
        return o.to_supply_point_id === pt.id;
      });
      pt._attn = attentionOf(pt);
      pt._text = [
        pt.name,
        pt.admin_area,
        pt.managed_by,
        KIND_LABEL[pt.kind],
        p.name,
      ]
        .join(' ')
        .toLowerCase();
      places.push(pt);
      placeByKey[pt._key] = pt;
    });
    p.suppliers.forEach(function (s) {
      s._key = p.program_id + ':s' + s.id;
      suppliers.push(s);
    });
    // An order past its date, or with no date ever promised, is in the way as
    // surely as a check is -- the chlorine chain's whole story is the second.
    p._open =
      p.checks.length +
      p.orders.filter(function (o) {
        return o.overdue || !o.expected_on;
      }).length;
  });

  function attentionOf(pt) {
    var cats = pt.checks.map(function (c) {
      return c.category;
    });
    var late = pt._orders.some(function (o) {
      return o.overdue;
    });
    if (
      late ||
      cats.indexOf('threshold') >= 0 ||
      cats.indexOf('conflict') >= 0 ||
      ['stockout', 'negative', 'below_min'].indexOf(pt.status) >= 0
    ) {
      return 'blocked';
    }
    var undated = pt._orders.some(function (o) {
      return !o.expected_on;
    });
    if (undated || cats.length) return 'waiting';
    return 'clear';
  }

  // Places sharing one coordinate -- every store a partner runs sits on its
  // head office until someone records where it is -- fan into a small ring so
  // each stays clickable. Display only; the place's panel says it is a stand-in.
  var stacks = {};
  function stack(o, lat, lng) {
    o._lat = lat;
    o._lng = lng;
    var k = lat.toFixed(4) + ',' + lng.toFixed(4);
    (stacks[k] = stacks[k] || []).push(o);
  }
  places.forEach(function (pt) {
    if (pt._placed) stack(pt, pt.lat, pt.lng);
  });
  suppliers.forEach(function (s) {
    if (s.location) stack(s, s.location.lat, s.location.lng);
  });
  Object.keys(stacks).forEach(function (k) {
    var group = stacks[k];
    group.forEach(function (o, i) {
      if (group.length === 1) {
        o._x = o._lng;
        o._y = o._lat;
        return;
      }
      var a = (2 * Math.PI * i) / group.length;
      var r = 0.025 + 0.004 * group.length;
      o._x = o._lng + r * Math.cos(a);
      o._y = o._lat + r * Math.sin(a);
    });
  });

  // ---------------------------------------------------------------- state
  var state = {
    prog: null,
    place: null,
    attention: false,
    kinds: [],
    orgs: [],
    q: '',
  };
  (function readHash() {
    try {
      var s = JSON.parse(decodeURIComponent(location.hash.slice(1)) || '{}');
      Object.keys(state).forEach(function (k) {
        if (s[k] !== undefined) state[k] = s[k];
      });
      if (state.prog && !progById[state.prog]) state.prog = null;
      if (state.place && !placeByKey[state.place]) state.place = null;
    } catch (e) {
      /* a hand-edited hash is not worth breaking the page over */
    }
  })();
  function writeHash() {
    history.replaceState(
      null,
      '',
      '#' + encodeURIComponent(JSON.stringify(state)),
    );
  }

  function visiblePlace(pt) {
    if (state.prog && pt.program_id !== state.prog) return false;
    if (state.attention && pt._attn === 'clear') return false;
    if (state.kinds.length && state.kinds.indexOf(pt.kind) < 0) return false;
    if (state.orgs.length && state.orgs.indexOf(pt.managed_by || '') < 0)
      return false;
    if (state.q && pt._text.indexOf(state.q.toLowerCase()) < 0) return false;
    return true;
  }

  // ---------------------------------------------------------------- top bar
  function renderBar() {
    document.getElementById('pm-programs').innerHTML = programs
      .map(function (p) {
        return (
          '<button type="button" class="pm-chip' +
          (state.prog === p.program_id ? ' pm-on' : '') +
          '" data-prog="' +
          p.program_id +
          '">' +
          '<span class="pm-dot" style="background:' +
          p._color +
          '"></span>' +
          esc(p.name) +
          (p._open
            ? ' <span style="opacity:.7">· ' + p._open + '</span>'
            : '') +
          '</button>'
        );
      })
      .join('');
    document
      .getElementById('pm-attention')
      .classList.toggle('pm-on', state.attention);
    var active = state.kinds.length + state.orgs.length + (state.q ? 1 : 0);
    document.getElementById('pm-more-n').textContent = active
      ? '· ' + active
      : '';
  }
  document
    .getElementById('pm-programs')
    .addEventListener('click', function (e) {
      var b = e.target.closest('[data-prog]');
      if (!b) return;
      var id = +b.dataset.prog;
      go(state.prog === id ? null : id, null);
    });
  document
    .getElementById('pm-attention')
    .addEventListener('click', function () {
      state.attention = !state.attention;
      update(false);
    });

  var morePanel = document.getElementById('pm-more-panel');
  document.getElementById('pm-more').addEventListener('click', function () {
    morePanel.hidden = !morePanel.hidden;
    if (!morePanel.hidden) renderMore();
  });
  document.addEventListener('click', function (e) {
    if (!morePanel.hidden && !e.target.closest('.pm-pop'))
      morePanel.hidden = true;
  });
  function uniq(xs) {
    return xs
      .filter(function (x, i) {
        return xs.indexOf(x) === i;
      })
      .sort();
  }
  function renderMore() {
    var kinds = uniq(
      places.map(function (p) {
        return p.kind;
      }),
    );
    var orgs = uniq(
      places.map(function (p) {
        return p.managed_by || '';
      }),
    );
    var box = function (field, value, label) {
      return (
        '<label><input type="checkbox" data-f="' +
        field +
        '" value="' +
        esc(value) +
        '"' +
        (state[field].indexOf(value) >= 0 ? ' checked' : '') +
        '>' +
        esc(label) +
        '</label>'
      );
    };
    morePanel.innerHTML =
      '<input type="search" id="pm-q" placeholder="Search places, orgs, areas…" class="w-full text-sm border border-gray-300 rounded-md px-2 py-1" value="' +
      esc(state.q) +
      '">' +
      '<h5>Kind of place</h5>' +
      kinds
        .map(function (k) {
          return box('kinds', k, KIND_LABEL[k] || k);
        })
        .join('') +
      '<h5>Run by</h5>' +
      orgs
        .map(function (o) {
          return box('orgs', o, o || 'Not recorded');
        })
        .join('') +
      '<button type="button" class="pm-link text-sm mt-2" id="pm-clear">Clear filters</button>';
  }
  morePanel.addEventListener('change', function (e) {
    var t = e.target;
    if (!t.dataset.f) return;
    var cur = state[t.dataset.f];
    state[t.dataset.f] = t.checked
      ? cur.concat([t.value])
      : cur.filter(function (v) {
          return v !== t.value;
        });
    update(false);
  });
  morePanel.addEventListener('input', function (e) {
    if (e.target.id !== 'pm-q') return;
    state.q = e.target.value;
    update(false);
  });
  morePanel.addEventListener('click', function (e) {
    if (e.target.id !== 'pm-clear') return;
    state.kinds = [];
    state.orgs = [];
    state.q = '';
    renderMore();
    update(false);
  });

  // ---------------------------------------------------------------- panel
  var side = document.getElementById('pm-side');

  function crumbs() {
    var parts = [
      '<button type="button" data-go="">' +
        esc(DATA.portfolio.name) +
        '</button>',
    ];
    if (state.prog) {
      var p = progById[state.prog];
      parts.push(
        state.place
          ? '<button type="button" data-go="' +
              p.program_id +
              '">' +
              esc(p.name) +
              '</button>'
          : '<span>' + esc(p.name) + '</span>',
      );
    }
    if (state.place)
      parts.push('<span>' + esc(placeByKey[state.place].name) + '</span>');
    return '<div class="pm-crumbs">' + parts.join(' › ') + '</div>';
  }

  // One line per stage, from chain_summary's own counts -- the same figures
  // the program's Overview shows, so the two cannot disagree.
  function stageLine(p, stage) {
    var s = p.summary || {};
    if (stage === 'source') {
      var src = s.source || {};
      var ev = src.evaluation || {};
      var bits = [plural((src.demand || {}).rounds || 0, 'round')];
      if (ev.of)
        bits.push(ev.comparable + ' of ' + ev.of + ' quotes comparable');
      if ((src.award || {}).count) bits.push(plural(src.award.count, 'award'));
      return bits.join(' · ');
    }
    if (stage === 'order') {
      var o = s.order || {};
      var parts = [plural((o.contract || {}).count || 0, 'order')];
      if (p.orders.length) parts.push(p.orders.length + ' still to arrive');
      if ((o.dispatched || {}).in_transit)
        parts.push(o.dispatched.in_transit + ' in transit');
      if ((o.invoiced || {}).unpaid)
        parts.push(plural(o.invoiced.unpaid, 'unpaid invoice'));
      return parts.join(' · ');
    }
    var net = (s.deliver || {}).network || {};
    var lines = [plural(net.supply_points || 0, 'place')];
    if (net.user_held) lines.push(plural(net.user_held, 'field worker'));
    if (net.never_reported)
      lines.push(net.never_reported + ' never reported stock');
    return lines.join(' · ');
  }
  function stalled(p) {
    return p.orders.filter(function (o) {
      return o.overdue || !o.expected_on;
    });
  }
  function stageChecks(p, stage) {
    return p.checks.filter(function (c) {
      return c.stage === stage;
    });
  }

  function portfolioPanel() {
    var vis = places.filter(visiblePlace);
    var count = function (a) {
      return vis.filter(function (p) {
        return p._attn === a;
      }).length;
    };
    var onWay = programs.reduce(function (n, p) {
      return n + p.orders.length;
    }, 0);
    var h = crumbs();
    h +=
      '<div class="pm-sec"><div class="text-sm text-gray-700">' +
      plural(vis.length, 'place') +
      ' across ' +
      plural(programs.length, 'program') +
      '. <span style="color:#dc2626">' +
      count('blocked') +
      ' blocked</span>, <span style="color:#b45309">' +
      count('waiting') +
      ' waiting on an answer</span>, ' +
      plural(onWay, 'order') +
      ' still to arrive.</div></div><div class="pm-sec">';
    programs.forEach(function (p) {
      h +=
        '<button type="button" class="pm-card" data-go="' +
        p.program_id +
        '">' +
        '<div class="flex items-center gap-2"><span class="pm-dot" style="background:' +
        p._color +
        '"></span>' +
        '<span class="font-semibold text-gray-900 text-sm">' +
        esc(p.name) +
        '</span>' +
        '<span class="ml-auto pm-muted">' +
        (p._open ? plural(p._open, 'thing') + ' in the way' : 'nothing open') +
        '</span></div>' +
        '<div class="pm-stages">' +
        STAGES.map(function (st) {
          var n =
            stageChecks(p, st.key).length +
            (st.key === 'order' ? stalled(p).length : 0);
          return (
            '<div class="pm-stage' +
            (n ? ' pm-hot' : '') +
            '"><b>' +
            st.label +
            (n ? ' <span class="pm-n">' + n + '</span>' : '') +
            '</b>' +
            esc(stageLine(p, st.key)) +
            '</div>'
          );
        }).join('') +
        '</div>' +
        (p.unplaced.length
          ? '<div class="pm-muted mt-2"><i class="fa-solid fa-location-dot mr-1"></i>' +
            plural(p.unplaced.length, 'place') +
            ' with no location — listed, not on the map</div>'
          : '') +
        '</button>';
    });
    return h + '</div>';
  }

  function subjectLink(c, p) {
    var pt = c.supply_point_id
      ? placeByKey[p.program_id + ':' + c.supply_point_id]
      : null;
    var label = esc(c.subject.label || checkLabel(c.kind));
    if (pt)
      return (
        '<button type="button" class="pm-link" data-place="' +
        pt._key +
        '"><i class="fa-solid fa-location-dot mr-1"></i>' +
        esc(pt.name) +
        '</button>'
      );
    return c.href
      ? '<a class="pm-link" href="' + esc(c.href) + '">' + label + '</a>'
      : label;
  }
  function checkRow(c, p) {
    var color =
      c.category === 'missing' ? ATTN.waiting.color : ATTN.blocked.color;
    return (
      '<div class="pm-row"><span class="pm-dot" style="margin-top:6px;background:' +
      color +
      '"></span><div class="min-w-0">' +
      '<div class="text-gray-900">' +
      esc(checkLabel(c.kind)) +
      ' <span class="pm-muted">· ' +
      esc(audienceLabel(c.audience)) +
      '</span></div>' +
      '<div class="pm-muted">' +
      subjectLink(c, p) +
      (c.days_open != null ? ' · ' + c.days_open + ' days open' : '') +
      '</div></div></div>'
    );
  }
  // The same finding about several records is one thing to fix, not several
  // lines to read: "No ration table" for five products is one row naming them.
  function checkRows(checks, p) {
    var groups = [];
    var byKey = {};
    checks.forEach(function (c) {
      var k = c.kind + '|' + c.audience;
      if (!byKey[k]) {
        byKey[k] = {
          kind: c.kind,
          category: c.category,
          audience: c.audience,
          items: [],
        };
        groups.push(byKey[k]);
      }
      byKey[k].items.push(c);
    });
    return groups
      .map(function (g) {
        if (g.items.length === 1) return checkRow(g.items[0], p);
        var color =
          g.category === 'missing' ? ATTN.waiting.color : ATTN.blocked.color;
        return (
          '<div class="pm-row"><span class="pm-dot" style="margin-top:6px;background:' +
          color +
          '"></span><details class="min-w-0 w-full">' +
          '<summary style="cursor:pointer;list-style:none"><span class="text-gray-900">' +
          esc(checkLabel(g.kind)) +
          ' <b>×' +
          g.items.length +
          '</b></span>' +
          ' <span class="pm-muted">· ' +
          esc(audienceLabel(g.audience)) +
          '</span>' +
          '<div class="pm-muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' +
          esc(
            g.items
              .map(function (c) {
                return c.subject.label || '';
              })
              .join(', '),
          ) +
          '</div></summary>' +
          g.items
            .map(function (c) {
              return (
                '<div class="pm-muted" style="padding:3px 0 0 2px">' +
                subjectLink(c, p) +
                (c.days_open != null
                  ? ' · ' + c.days_open + ' days open'
                  : '') +
                '</div>'
              );
            })
            .join('') +
          '</details></div>'
        );
      })
      .join('');
  }
  function orderRow(o, p) {
    var supplier = p.suppliers.filter(function (s) {
      return s.id === o.supplier_id;
    })[0];
    var to = o.to_supply_point_id
      ? placeByKey[p.program_id + ':' + o.to_supply_point_id]
      : null;
    var when = !o.expected_on
      ? '<span style="color:#b45309">no arrival date promised</span>'
      : o.overdue
        ? '<span style="color:#dc2626">' +
          daysLate(o.expected_on) +
          ' days late</span>'
        : 'due ' + esc(o.expected_on);
    var colour = o.overdue ? '#dc2626' : o.expected_on ? '#0284c7' : '#d97706';
    return (
      '<div class="pm-row"><i class="fa-solid fa-truck-fast" style="margin-top:3px;color:' +
      colour +
      '"></i><div class="min-w-0">' +
      '<a class="pm-link" href="' +
      esc(o.url) +
      '">' +
      esc(o.reference || 'Order #' + o.contract_id) +
      '</a>' +
      (o.outstanding ? ' · ' + esc(figure(o.outstanding).text) : '') +
      (o.item_name ? ' ' + esc(o.item_name) : '') +
      '<div class="pm-muted">' +
      esc(supplier ? supplier.name : 'Supplier') +
      ' → ' +
      (to
        ? '<button type="button" class="pm-link" data-place="' +
          to._key +
          '">' +
          esc(to.name) +
          '</button>'
        : 'no destination recorded') +
      '</div><div class="pm-muted">' +
      when +
      '</div></div></div>'
    );
  }
  function placeRow(pt) {
    var a = ATTN[pt._attn];
    return (
      '<button type="button" class="pm-row w-full text-left" data-place="' +
      pt._key +
      '">' +
      '<span class="pm-dot" style="margin-top:6px;background:' +
      a.color +
      '"></span>' +
      '<div class="min-w-0"><div class="text-gray-900">' +
      esc(pt.name) +
      (pt._placed
        ? ''
        : ' <i class="fa-solid fa-location-dot text-gray-300" title="No location"></i>') +
      '</div>' +
      '<div class="pm-muted">' +
      esc(KIND_LABEL[pt.kind] || pt.kind) +
      (pt.managed_by ? ' · ' + esc(pt.managed_by) : '') +
      ' · ' +
      esc(a.label) +
      '</div></div></button>'
    );
  }

  function programPanel(p) {
    var h = crumbs();
    h +=
      '<div class="pm-sec"><div class="flex items-center gap-2"><span class="pm-dot" style="background:' +
      p._color +
      '"></span>' +
      '<span class="text-base font-semibold text-gray-900">' +
      esc(p.name) +
      '</span>' +
      '<a class="pm-link text-sm ml-auto" href="' +
      esc(p.home_url) +
      '">Open this chain →</a></div></div>';
    STAGES.forEach(function (st) {
      var cs = stageChecks(p, st.key);
      h +=
        '<div class="pm-sec"><h4><span>' +
        st.label +
        '</span><span style="text-transform:none;letter-spacing:0;font-weight:400">' +
        esc(stageLine(p, st.key)) +
        '</span></h4>';
      h += cs.length
        ? checkRows(cs, p)
        : '<div class="pm-muted">Nothing in the way here.</div>';
      if (st.key === 'order' && p.orders.length) {
        h +=
          '<div class="pm-muted mt-2" style="font-weight:600">Still to arrive</div>' +
          p.orders
            .map(function (o) {
              return orderRow(o, p);
            })
            .join('');
      }
      if (st.key === 'deliver') {
        var pts = places.filter(function (pt) {
          return (
            pt.program_id === p.program_id &&
            visiblePlace(pt) &&
            pt.kind !== 'user_held'
          );
        });
        var workers = places.filter(function (pt) {
          return pt.program_id === p.program_id && pt.kind === 'user_held';
        }).length;
        h +=
          '<div class="pm-muted mt-2" style="font-weight:600">Places</div>' +
          (pts.length
            ? pts.map(placeRow).join('')
            : '<div class="pm-muted">None match the filters.</div>') +
          (workers
            ? '<div class="pm-muted mt-1">' +
              plural(workers, 'field worker') +
              ' — zoom in to see them.</div>'
            : '');
      }
      h += '</div>';
    });
    return h;
  }

  function band(pt) {
    var mos = num(pt.months_of_stock);
    var lo = num(pt.min_months_of_stock);
    var hi = num(pt.max_months_of_stock);
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
      '<div class="flex justify-between pm-muted"><span>0</span><span>its band ' +
      (lo != null ? fmt(lo) : '–') +
      '–' +
      (hi != null ? fmt(hi) : '–') +
      ' months</span></div>'
    );
  }

  function placePanel(pt) {
    var p = progById[pt.program_id];
    var a = ATTN[pt._attn];
    var h = crumbs();
    h +=
      '<div class="pm-sec"><div class="text-base font-semibold text-gray-900">' +
      esc(pt.name) +
      '</div>' +
      '<div class="pm-muted">' +
      esc(KIND_LABEL[pt.kind] || pt.kind) +
      (pt.admin_area ? ' · ' + esc(pt.admin_area) : '') +
      (pt.managed_by ? ' · run by ' + esc(pt.managed_by) : '') +
      '</div>' +
      '<div class="mt-2 text-sm" style="color:' +
      a.color +
      '"><span class="pm-dot mr-1" style="background:' +
      a.color +
      '"></span>' +
      esc(a.label) +
      '</div>';
    if (!pt._placed) {
      h +=
        '<div class="pm-note" style="background:#f9fafb;border:1px solid #e5e7eb">No location at all, so it is not on the map. ' +
        '<a class="pm-link" href="' +
        esc(pt.links.edit) +
        '">Record where it is</a>.</div>';
    } else if (pt.location.source !== 'recorded') {
      h +=
        '<div class="pm-note" style="background:#fffbeb;border:1px solid #fde68a;color:#78350f"><i class="fa-solid fa-location-crosshairs mr-1"></i>Shown at ' +
        esc(pt.location.label || LOCATION_LABEL[pt.location.source]) +
        (pt.location.precision
          ? ', accurate to the ' + esc(pt.location.precision)
          : '') +
        ' — not where this place really is. <a class="underline" href="' +
        esc(pt.links.edit) +
        '">Record its location</a>.</div>';
    }
    h += '</div>';

    var onHand = figure(pt.on_hand);
    var mos = figure(pt.months_of_stock);
    var reported = figure(pt.reported);
    var dts = num(pt.days_to_stockout);
    h +=
      '<div class="pm-sec"><h4><span>Stock</span><span style="text-transform:none;letter-spacing:0;font-weight:400">' +
      esc(STATUS_LABEL[pt.status] || pt.status) +
      '</span></h4><div class="grid grid-cols-2 gap-2 text-sm">' +
      '<div><div class="pm-muted">On hand (ledger)</div><div class="font-semibold">' +
      esc(onHand.text) +
      '</div></div>' +
      '<div><div class="pm-muted">Months of stock</div><div class="font-semibold">' +
      esc(mos.text) +
      '</div></div>' +
      '<div><div class="pm-muted">Last counted</div><div>' +
      esc(reported.text) +
      (pt.reported_on
        ? ' <span class="pm-muted">on ' + esc(pt.reported_on) + '</span>'
        : '') +
      '</div></div>' +
      '<div><div class="pm-muted">Runs out in</div><div>' +
      (dts != null ? fmt(Math.round(dts)) + ' days' : '—') +
      '</div></div></div>' +
      band(pt) +
      (onHand.why
        ? '<div class="pm-muted mt-1">Why not: ' + esc(onHand.why) + '</div>'
        : '') +
      '</div>';

    h +=
      '<div class="pm-sec"><h4><span>In the way here</span></h4>' +
      (pt.checks.length
        ? checkRows(pt.checks, p)
        : '<div class="pm-muted">Nothing open at this place.</div>') +
      '</div>';
    h +=
      '<div class="pm-sec"><h4><span>Still to arrive</span></h4>' +
      (pt._orders.length
        ? pt._orders
            .map(function (o) {
              return orderRow(o, p);
            })
            .join('')
        : '<div class="pm-muted">Nothing is owed to this place.</div>') +
      '</div>';
    var restocks = places.filter(function (w) {
      return w.program_id === pt.program_id && w.parent_id === pt.id;
    });
    if (restocks.length)
      h +=
        '<div class="pm-sec"><h4><span>Restocks</span></h4>' +
        restocks.map(placeRow).join('') +
        '</div>';
    h +=
      '<div class="pm-sec text-sm flex gap-4"><a class="pm-link" href="' +
      esc(pt.links.movements) +
      '">Movements</a>' +
      '<a class="pm-link" href="' +
      esc(pt.links.network) +
      '">Network</a><a class="pm-link" href="' +
      esc(pt.links.edit) +
      '">Edit place</a></div>';
    return h;
  }

  function renderPanel() {
    if (state.place && placeByKey[state.place])
      side.innerHTML = placePanel(placeByKey[state.place]);
    else if (state.prog) side.innerHTML = programPanel(progById[state.prog]);
    else side.innerHTML = portfolioPanel();
    side.scrollTop = 0;
  }
  side.addEventListener('click', function (e) {
    var g = e.target.closest('[data-go]');
    if (g) return go(g.dataset.go ? +g.dataset.go : null, null);
    var pl = e.target.closest('[data-place]');
    if (pl) {
      var pt = placeByKey[pl.dataset.place];
      go(pt.program_id, pt._key);
    }
  });

  function go(prog, place) {
    state.prog = prog;
    state.place = place;
    update(true);
  }

  // ---------------------------------------------------------------- map
  var map = null;
  var ready = false;
  var mapEl = document.getElementById('pm-map');
  function note(msg) {
    var n = document.createElement('div');
    n.className = 'pm-nomap';
    n.innerHTML = '<div>' + msg + '</div>';
    mapEl.parentNode.appendChild(n);
    return n;
  }
  if (!window.mapboxgl || !window.ConnectMap)
    note('The map library did not load. The panel beside it is complete.');
  else if (!window.MAPBOX_TOKEN)
    note(
      'No map token is configured on this server (MAPBOX_TOKEN). The panel beside it is complete.',
    );
  else {
    map = ConnectMap.createMap(mapEl, { center: [8, 9], zoom: 4.5 });
    map.addControl(
      new mapboxgl.NavigationControl({ showCompass: false }),
      'top-left',
    );
    map.on('load', function () {
      ConnectMap.calmBasemap(map);
      [
        'pm-routes',
        'pm-links',
        'pm-places',
        'pm-workers',
        'pm-suppliers',
      ].forEach(function (id) {
        map.addSource(id, { type: 'geojson', data: empty() });
      });
      map.addLayer({
        id: 'pm-links',
        type: 'line',
        source: 'pm-links',
        paint: {
          'line-color': '#475569',
          'line-width': 1,
          'line-opacity': 0.6,
        },
      });
      map.addLayer({
        id: 'pm-routes',
        type: 'line',
        source: 'pm-routes',
        layout: { 'line-cap': 'round' },
        paint: {
          'line-color': [
            'match',
            ['get', 'state'],
            'late',
            '#ef4444',
            'undated',
            '#f59e0b',
            '#38bdf8',
          ],
          'line-width': 2.2,
          'line-opacity': 0.9,
          'line-dasharray': [
            'case',
            ['==', ['get', 'state'], 'undated'],
            ['literal', [1.5, 1.5]],
            ['literal', [1, 0]],
          ],
        },
      });
      map.addLayer({
        id: 'pm-workers',
        type: 'circle',
        source: 'pm-workers',
        minzoom: 9,
        paint: {
          'circle-radius': 3.5,
          'circle-color': ['get', 'color'],
          'circle-stroke-color': '#0b1020',
          'circle-stroke-width': 1,
        },
      });
      map.addLayer({
        id: 'pm-suppliers',
        type: 'circle',
        source: 'pm-suppliers',
        paint: {
          'circle-radius': 6,
          'circle-color': '#0b1020',
          'circle-stroke-color': '#e2e8f0',
          'circle-stroke-width': 2,
        },
      });
      map.addLayer({
        id: 'pm-places',
        type: 'circle',
        source: 'pm-places',
        paint: {
          'circle-radius': ['get', 'r'],
          'circle-color': ['get', 'color'],
          'circle-opacity': ['case', ['get', 'approx'], 0.55, 1],
          'circle-stroke-color': ['case', ['get', 'sel'], '#ffffff', '#0b1020'],
          'circle-stroke-width': ['case', ['get', 'sel'], 3, 1.2],
        },
      });
      map.addLayer({
        id: 'pm-labels',
        type: 'symbol',
        source: 'pm-places',
        minzoom: 6,
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 11,
          'text-offset': [0, 1.2],
          'text-anchor': 'top',
          'text-font': ['DIN Pro Medium', 'Arial Unicode MS Regular'],
          'text-optional': true,
        },
        paint: {
          'text-color': '#cbd5e1',
          'text-halo-color': '#0b1020',
          'text-halo-width': 1.3,
        },
      });

      var popup = new mapboxgl.Popup({
        closeButton: false,
        closeOnClick: false,
        offset: 10,
      });
      ['pm-places', 'pm-workers'].forEach(function (layer) {
        map.on('mouseenter', layer, function (e) {
          map.getCanvas().style.cursor = 'pointer';
          var pt = placeByKey[e.features[0].properties.key];
          popup
            .setLngLat([pt._x, pt._y])
            .setHTML(
              '<strong>' +
                esc(pt.name) +
                '</strong><br><span style="color:#6b7280">' +
                esc(progById[pt.program_id].name) +
                '</span><br>' +
                '<span style="color:' +
                ATTN[pt._attn].color +
                '">' +
                esc(ATTN[pt._attn].label) +
                '</span>',
            )
            .addTo(map);
        });
        map.on('mouseleave', layer, function () {
          map.getCanvas().style.cursor = '';
          popup.remove();
        });
        map.on('click', layer, function (e) {
          var pt = placeByKey[e.features[0].properties.key];
          go(pt.program_id, pt._key);
        });
      });
      map.on('mouseenter', 'pm-suppliers', function (e) {
        var f = e.features[0].properties;
        popup
          .setLngLat(e.lngLat)
          .setHTML(
            '<strong>' +
              esc(f.name) +
              '</strong><br><span style="color:#6b7280">Supplier · shown at ' +
              esc(f.where) +
              '</span>',
          )
          .addTo(map);
      });
      map.on('mouseleave', 'pm-suppliers', function () {
        popup.remove();
      });
      ready = true;
      drawMap(true);
    });
  }
  function empty() {
    return { type: 'FeatureCollection', features: [] };
  }
  function fc(features) {
    return { type: 'FeatureCollection', features: features };
  }
  // A gentle curve, so a route reads as a route rather than a border line.
  function arc(a, b) {
    var mx = (a[0] + b[0]) / 2;
    var my = (a[1] + b[1]) / 2;
    var dx = b[0] - a[0];
    var dy = b[1] - a[1];
    var c = [mx - dy * 0.2, my + dx * 0.2];
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

  var emptyNote = null;
  function drawMap(fit) {
    if (!ready) return;
    var vis = places.filter(function (pt) {
      return pt._placed && visiblePlace(pt);
    });
    var shown = {};
    vis.forEach(function (pt) {
      shown[pt._key] = true;
    });
    var feature = function (pt) {
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [pt._x, pt._y] },
        properties: {
          key: pt._key,
          name: pt.name,
          color: ATTN[pt._attn].color,
          r: KIND_RADIUS[pt.kind] || 6,
          approx: pt.location.source !== 'recorded',
          sel: state.place === pt._key,
        },
      };
    };
    map.getSource('pm-places').setData(
      fc(
        vis
          .filter(function (pt) {
            return pt.kind !== 'user_held';
          })
          .map(feature),
      ),
    );
    map.getSource('pm-workers').setData(
      fc(
        vis
          .filter(function (pt) {
            return pt.kind === 'user_held';
          })
          .map(feature),
      ),
    );

    // Resupply lines only inside one program: across a portfolio they are noise.
    var links = [];
    if (state.prog) {
      vis.forEach(function (pt) {
        var parent =
          pt.parent_id && placeByKey[pt.program_id + ':' + pt.parent_id];
        if (parent && shown[parent._key] && pt.kind !== 'user_held') {
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
    map.getSource('pm-links').setData(fc(links));

    // A route is drawn only when both ends are real places on the map; an
    // order to a store with no location is listed in the panel instead.
    var routes = [];
    var used = {};
    programs.forEach(function (p) {
      if (state.prog && p.program_id !== state.prog) return;
      p.orders.forEach(function (o) {
        var to =
          o.to_supply_point_id &&
          placeByKey[p.program_id + ':' + o.to_supply_point_id];
        var s = p.suppliers.filter(function (x) {
          return x.id === o.supplier_id;
        })[0];
        if (!to || !shown[to._key] || !s || !s.location) return;
        used[s._key] = s;
        routes.push({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: arc([s._x, s._y], [to._x, to._y]),
          },
          properties: {
            state: o.overdue ? 'late' : o.expected_on ? 'due' : 'undated',
          },
        });
      });
    });
    map.getSource('pm-routes').setData(fc(routes));
    map.getSource('pm-suppliers').setData(
      fc(
        Object.keys(used).map(function (k) {
          var s = used[k];
          return {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [s._x, s._y] },
            properties: {
              name: s.name,
              where: s.location.label + ', ' + s.location.precision + ' level',
            },
          };
        }),
      ),
    );

    if (emptyNote) {
      emptyNote.remove();
      emptyNote = null;
    }
    if (!vis.length) {
      var anyPlaced = places.some(function (pt) {
        return pt._placed;
      });
      emptyNote = note(
        anyPlaced
          ? 'No place on the map matches what is selected.'
          : 'None of these places has a location yet. Each is listed in the panel.',
      );
    }

    if (fit) {
      var sel = state.place && placeByKey[state.place];
      var target =
        sel && sel._placed
          ? [sel]
          : vis.concat(
              Object.keys(used).map(function (k) {
                return used[k];
              }),
            );
      if (target.length === 1) {
        map.flyTo({
          center: [target[0]._x, target[0]._y],
          zoom: Math.max(map.getZoom(), sel ? 10 : 8),
          speed: 1.4,
        });
      } else if (target.length) {
        var b = new mapboxgl.LngLatBounds();
        target.forEach(function (o) {
          b.extend([o._x, o._y]);
        });
        map.fitBounds(b, { padding: 70, maxZoom: 9, duration: 700 });
      }
    }
  }

  // The three states are always visible; the rest of the key folds away, so
  // it does not sit on top of the places it explains.
  function renderLegend() {
    var row = function (swatch, label) {
      return '<div>' + swatch + label + '</div>';
    };
    var h = ['blocked', 'waiting', 'clear']
      .map(function (k) {
        return row(
          '<span class="pm-dot" style="background:' +
            ATTN[k].color +
            '"></span>',
          ATTN[k].label,
        );
      })
      .join('');
    h +=
      '<details><summary style="cursor:pointer;opacity:.7;list-style:none">More…</summary>' +
      row(
        '<span class="pm-dot" style="background:#94a3b8;opacity:.5"></span>',
        'Faded: location is a stand-in',
      ) +
      row(
        '<span class="pm-dot" style="background:#0b1020;border:2px solid #e2e8f0"></span>',
        'Supplier',
      ) +
      row(
        '<span style="width:16px;border-top:2px solid #38bdf8"></span>',
        'Order on its way',
      ) +
      row(
        '<span style="width:16px;border-top:2px solid #ef4444"></span>',
        'Order past its promised date',
      ) +
      row(
        '<span style="width:16px;border-top:2px dashed #f59e0b"></span>',
        'No arrival date promised',
      ) +
      '</details>';
    document.getElementById('pm-legend').innerHTML = h;
  }

  function update(fit) {
    renderBar();
    renderPanel();
    drawMap(fit);
    writeHash();
  }

  renderLegend();
  update(true);
})();
