(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('cea-data').textContent);
  var BAR = D.bar_x;
  var CSS = getComputedStyle(document.querySelector('.cea'));
  var COL = {
    chc: CSS.getPropertyValue('--chc').trim(),
    mbw: CSS.getPropertyValue('--mbw').trim(),
    kc: CSS.getPropertyValue('--kc').trim(),
    none: CSS.getPropertyValue('--none').trim(),
  };
  var NS = 'http://www.w3.org/2000/svg';
  var tip = document.getElementById('tip');

  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function usd(v) {
    if (v == null) return '–';
    var a = Math.abs(v);
    if (a >= 1e6) return '$' + (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
    if (a >= 1e3) return '$' + (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k';
    return '$' + v.toFixed(a < 10 ? 2 : 0);
  }
  function num(v, d) {
    return v == null
      ? '–'
      : v.toLocaleString(undefined, {
          maximumFractionDigits: d || 0,
          minimumFractionDigits: d || 0,
        });
  }
  function xfmt(v) {
    return v == null ? '–' : (v >= 10 ? v.toFixed(0) : v.toFixed(1)) + 'x';
  }
  function showTip(evt, html) {
    tip.innerHTML = html;
    tip.style.display = 'block';
    moveTip(evt);
  }
  function moveTip(evt) {
    var w = tip.offsetWidth || 320,
      h = tip.offsetHeight || 120;
    var x = evt.clientX + 14,
      y = evt.clientY + 14;
    if (x + w + 8 > window.innerWidth) x = evt.clientX - w - 14;
    x = Math.max(8, Math.min(x, window.innerWidth - w - 8));
    if (y + h + 8 > window.innerHeight) y = Math.max(8, evt.clientY - h - 14);
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }
  function hideTip() {
    tip.style.display = 'none';
  }
  // A phone has no hover: a tap shows the tip and stays until the next tap elsewhere.
  var tipAt = 0,
    TOUCH = window.matchMedia('(hover: none)').matches;
  function tapTip(evt, html) {
    tipAt = Date.now();
    showTip(evt, html);
  }
  document.addEventListener('click', function (e) {
    if (Date.now() - tipAt > 300 && !tip.contains(e.target)) hideTip();
  });
  function bindTip(node, r) {
    node.addEventListener('mousemove', function (e) {
      showTip(e, rowTip(r));
    });
    node.addEventListener('mouseleave', hideTip);
    node.addEventListener('click', function (e) {
      tapTip(e, rowTip(r));
    });
  }
  function rowTip(r) {
    var s =
      '<b>' +
      esc(r.name) +
      '</b><br>' +
      esc(r.service_name) +
      ' · ' +
      esc(r.country_name) +
      (r.org_slug ? ' · ' + esc(r.org_slug) : '');
    s +=
      '<br>Total cost ' +
      usd(r.total_usd) +
      ' · ' +
      num(r.reached) +
      (r.reach_basis === 'busiest_payment_unit'
        ? ' people (estimate)'
        : ' distinct people');
    if (r.works_per_person != null)
      s += ' · ' + num(r.works_per_person, 2) + ' approved work each';
    if (r.pooled_usd)
      s +=
        '<br>Includes ' +
        usd(r.pooled_usd) +
        ' paid through opportunit' +
        (r.pooled_from.length === 1 ? 'y ' : 'ies ') +
        r.pooled_from.join(', ');
    if (r.pooled_into && r.pooled_into.length)
      s +=
        '<br>Its ' +
        usd(r.own_usd) +
        ' is counted in opportunit' +
        (r.pooled_into.length === 1 ? 'y ' : 'ies ') +
        r.pooled_into.join(', ');
    if (r.status === 'modelled') {
      s +=
        '<br>Cost per person ' +
        usd(r.cost_per_person) +
        ' (model ' +
        usd(r.model_cost_per_person) +
        ')';
      s +=
        '<br><b>' +
        xfmt(r.x) +
        '</b> at actual cost · ' +
        xfmt(r.model_x) +
        ' at model price';
      s +=
        '<br>~' +
        num(r.deaths_averted, 1) +
        ' deaths averted · ' +
        usd(r.cost_per_death) +
        ' per death';
    } else {
      s += '<br><i>' + esc(r.status_text) + '</i>';
    }
    if (TOUCH && r.model)
      s +=
        '<br><a href="/labs/cea/implementation/opp/' +
        r.id +
        '/">How this was worked out →</a>';
    return s;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  document.getElementById('model-prices').textContent = Object.keys(D.models)
    .map(function (k) {
      return (
        D.models[k].label +
        ' ' +
        usd(D.models[k].price) +
        ' per ' +
        D.models[k].unit
      );
    })
    .join('; ');

  // ---------- what-if inputs ----------
  var DEF = { basis: 'actual', rank: 'x', bar: D.bar_x, m: {} };
  Object.keys(D.models).forEach(function (k) {
    DEF.m[k] = { extra: 0, effect: 100, reach: 100 };
  });
  // The server read the URL (what_if_from) and sent what it understood; the page does not parse it again.
  var W = JSON.parse(JSON.stringify(D.view.what_if));
  var V = D.view;
  function writeUrl() {
    var p = new URLSearchParams();
    if (W.basis !== DEF.basis) p.set('basis', W.basis);
    if (W.rank !== DEF.rank) p.set('rank', W.rank);
    if (W.bar !== DEF.bar) p.set('bar', W.bar);
    Object.keys(W.m).forEach(function (k) {
      ['extra', 'effect', 'reach'].forEach(function (f) {
        if (W.m[k][f] !== DEF.m[k][f]) p.set(k + '_' + f, W.m[k][f]);
      });
    });
    var q = p.toString();
    try {
      history.replaceState(null, '', location.pathname + (q ? '?' + q : ''));
    } catch (e) {
      /* a sandboxed frame: the scenario still applies */
    }
  }
  var wiRows = document.getElementById('wi-rows');
  wiRows.innerHTML = Object.keys(D.models)
    .map(function (k) {
      var m = D.models[k];
      return (
        '<tr><td><span class="sw" style="background:' +
        COL[k] +
        ';margin-right:6px"></span>' +
        esc(m.label) +
        '</td>' +
        '<td data-l="Extra cost per person"><span class="wi-unit">$<input type="number" min="0" step="0.1" data-k="' +
        k +
        '" data-f="extra"></span></td>' +
        '<td data-l="Effect, % of model"><span class="wi-unit"><input type="number" min="0" max="300" step="5" data-k="' +
        k +
        '" data-f="effect">%</span></td>' +
        '<td data-l="People counted, %"><span class="wi-unit"><input type="number" min="1" max="100" step="5" data-k="' +
        k +
        '" data-f="reach">%</span></td>' +
        '<td data-l="Model price" style="color:#6b7280">' +
        usd(m.price) +
        ' per ' +
        esc(m.unit) +
        '</td></tr>'
      );
    })
    .join('');
  function fillInputs() {
    document.getElementById('wi-basis').value = W.basis;
    document.getElementById('wi-rank').value = W.rank;
    document.getElementById('wi-bar').value = W.bar;
    wiRows.querySelectorAll('input').forEach(function (i) {
      i.value = W.m[i.dataset.k][i.dataset.f];
      i.disabled = W.basis === 'model' && i.dataset.f === 'extra';
    });
  }
  fillInputs();
  document.getElementById('wi-basis').addEventListener('change', function (e) {
    W.basis = e.target.value;
    fillInputs();
    writeUrl();
    draw();
  });
  document.getElementById('wi-rank').addEventListener('change', function (e) {
    W.rank = e.target.value;
    writeUrl();
    draw();
  });
  document.getElementById('wi-bar').addEventListener('input', function (e) {
    var v = +e.target.value;
    if (v > 0) {
      W.bar = v;
      BAR = v;
      writeUrl();
      draw();
    }
  });
  wiRows.addEventListener('input', function (e) {
    var i = e.target;
    if (!i.dataset.k) return;
    var v = +i.value;
    if (i.value === '' || isNaN(v) || v < 0) return;
    W.m[i.dataset.k][i.dataset.f] = v;
    writeUrl();
    draw();
  });
  document.getElementById('wi-reset').addEventListener('click', function () {
    W = JSON.parse(JSON.stringify(DEF));
    BAR = W.bar;
    fillInputs();
    writeUrl();
    draw();
  });
  BAR = W.bar;

  var RANK_LABEL = {
    x: "multiple of GiveWell's benchmark",
    deaths_averted: 'deaths averted',
    cost_per_death: 'cost per death averted',
    total_usd: 'total cost',
  };

  // ---------- filters ----------
  var state = {
    models: { chc: true, mbw: true, kc: true },
    active: false,
    q: '',
    modelled: false,
    country: '',
    minSpend: 0,
  };
  var csel = document.getElementById('country');
  D.countries.forEach(function (c) {
    var o = document.createElement('option');
    o.value = c;
    o.textContent = c;
    csel.appendChild(o);
  });
  csel.addEventListener('change', function (e) {
    state.country = e.target.value;
    draw();
  });
  document
    .getElementById('modelled-only')
    .addEventListener('change', function (e) {
      state.modelled = e.target.checked;
      draw();
    });
  document.getElementById('min-spend').addEventListener('input', function (e) {
    state.minSpend = +e.target.value || 0;
    draw();
  });
  document
    .querySelectorAll('#controls input[data-model]')
    .forEach(function (cb) {
      cb.addEventListener('change', function () {
        state.models[cb.dataset.model] = cb.checked;
        draw();
      });
    });
  document
    .getElementById('active-only')
    .addEventListener('change', function (e) {
      state.active = e.target.checked;
      draw();
    });
  document.getElementById('q').addEventListener('input', function (e) {
    state.q = e.target.value.toLowerCase();
    draw();
  });

  // ---------- tiles ----------
  function tiles(t) {
    // Every figure is the server's roll-up of the rows shown: nothing is added up here.
    var tiles = [
      [
        'Opportunities',
        num(t.opportunities),
        num(t.modelled) + ' with a model',
      ],
      [
        'Total spend',
        usd(t.spend),
        usd(t.spend_modelled) +
          ' modelled (' +
          (t.spend ? Math.round((100 * t.spend_modelled) / t.spend) : 0) +
          '%)',
      ],
      [
        'Portfolio multiple',
        xfmt(t.portfolio_x),
        W.basis === 'model'
          ? "at the models' prices"
          : 'cost-weighted, modelled only',
      ],
      [
        "Clear GiveWell's bar",
        num(t.clearing_bar) + ' / ' + num(t.modelled),
        BAR + 'x benchmark',
      ],
      [
        'Deaths averted (modelled)',
        '~' + num(t.deaths_averted, 0),
        t.deaths_averted && t.cost_per_death
          ? usd(t.cost_per_death) + ' per death'
          : '',
      ],
    ];
    var box = document.getElementById('tiles');
    while (box.firstChild) box.removeChild(box.firstChild);
    tiles.forEach(function (x) {
      var d = document.createElement('div');
      d.className = 'tile';
      [
        ['k', x[0]],
        ['v', x[1]],
        ['s', x[2]],
      ].forEach(function (kv) {
        var c = document.createElement('div');
        c.className = kv[0];
        c.textContent = kv[1];
        d.appendChild(c);
      });
      box.appendChild(d);
    });
  }

  // ---------- programmes ----------
  function programmes(list) {
    var t = document.getElementById('programmes');
    while (t.firstChild) t.removeChild(t.firstChild);
    var head = document.createElement('thead'),
      hr = document.createElement('tr');
    [
      ['Programme', ''],
      ['Partners', ''],
      ['Opps', 'n'],
      ['Spend', 'n'],
      ['People', 'n'],
      ['Deaths averted', 'n'],
      ['$ / death', 'n'],
      ['Multiple', 'n'],
    ].forEach(function (c) {
      var th = document.createElement('th');
      th.textContent = c[0];
      th.className = c[1];
      hr.appendChild(th);
    });
    head.appendChild(hr);
    t.appendChild(head);
    var body = document.createElement('tbody');
    list.forEach(function (g) {
      var tr = document.createElement('tr');
      [
        [g.name, ''],
        [g.partners.join(', '), ''],
        [num(g.opportunities), 'n'],
        [usd(g.spend), 'n'],
        [g.modelled ? num(g.people) : '–', 'n'],
        [g.modelled ? num(g.deaths_averted, 1) : '–', 'n'],
        [g.cost_per_death != null ? usd(g.cost_per_death) : '–', 'n'],
        [xfmt(g.portfolio_x), 'n'],
      ].forEach(function (c) {
        var td = document.createElement('td');
        td.textContent = c[0];
        td.className = c[1];
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    t.appendChild(body);
  }

  function rankChart(rows) {
    var svg = document.getElementById('rank');
    svg.innerHTML = '';
    var m = rows.filter(function (r) {
      return r.status === 'modelled';
    });
    // On a phone each name gets its own line above its bar, so nothing needs to scroll sideways.
    var narrow = svg.parentNode.clientWidth < 560;
    var W = narrow
      ? Math.max(svg.parentNode.clientWidth, 240)
      : Math.max(svg.parentNode.clientWidth, 520);
    var L = narrow ? 14 : Math.min(330, W * 0.38),
      R = narrow ? 8 : 70,
      T = 26,
      LH = narrow ? 16 : 0;
    var maxSpend = Math.max.apply(
      null,
      m
        .map(function (r) {
          return r.total_usd;
        })
        .concat([1]),
    );
    var hOf = function (r) {
      return 6 + 14 * Math.sqrt(r.total_usd / maxSpend);
    };
    var gap = narrow ? 8 : 6,
      y = T,
      ys = m.map(function (r) {
        var h = hOf(r),
          yy = y;
        y += LH + h + gap;
        return [yy, h];
      });
    var H = Math.max(y + 10, 80);
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);
    if (!m.length) {
      el(
        'text',
        { x: 12, y: 40, fill: '#6b7280', 'font-size': 13 },
        svg,
      ).textContent = 'No modelled opportunities match.';
      return;
    }
    var xmax = Math.max(
      BAR * 1.5,
      Math.max.apply(
        null,
        m.map(function (r) {
          return Math.max(r.x, r.model_x);
        }),
      ) * 1.05,
    );
    var sx = function (v) {
      return L + ((W - L - R) * Math.min(v, xmax)) / xmax;
    };
    var ax = el('g', { class: 'axis' }, svg);
    var step = xmax > 60 ? 20 : xmax > 30 ? 10 : 5;
    for (var v = 0; v <= xmax; v += step) {
      el('line', { x1: sx(v), x2: sx(v), y1: T - 6, y2: H - 4 }, ax);
      el(
        'text',
        { x: sx(v), y: T - 10, 'text-anchor': 'middle' },
        ax,
      ).textContent = v + 'x';
    }
    el(
      'line',
      {
        x1: sx(BAR),
        x2: sx(BAR),
        y1: T - 6,
        y2: H - 4,
        stroke: '#7c3aed',
        'stroke-dasharray': '4 3',
        'stroke-width': 1.5,
      },
      svg,
    );
    el(
      'text',
      { x: sx(BAR) + 4, y: H - 8, fill: '#7c3aed', 'font-size': 11 },
      svg,
    ).textContent = 'GiveWell bar ' + BAR + 'x';
    m.forEach(function (r, i) {
      var top = ys[i][0],
        h = ys[i][1],
        yy = top + LH,
        cy = yy + h / 2;
      var g = el('g', { style: 'cursor:default' }, svg);
      var label = r.rank + '. ' + r.name;
      var maxc = narrow ? Math.max(12, Math.floor((W - 96) / 6.3)) : 46;
      var t = el(
        'text',
        narrow
          ? { x: L, y: top + 11, 'font-size': 12, fill: '#1f2433' }
          : {
              x: L - 8,
              y: cy + 4,
              'text-anchor': 'end',
              'font-size': 12,
              fill: '#1f2433',
            },
        g,
      );
      t.textContent =
        label.length > maxc ? label.slice(0, maxc - 1) + '…' : label;
      el(
        'rect',
        {
          x: L,
          y: yy,
          width: Math.max(1, sx(r.x) - L),
          height: h,
          rx: 2,
          fill: COL[r.model],
          opacity: r.clears_bar ? 0.95 : 0.55,
        },
        g,
      );
      el(
        'line',
        {
          x1: sx(r.model_x),
          x2: sx(r.model_x),
          y1: yy - 2,
          y2: yy + h + 2,
          stroke: '#111827',
          'stroke-width': 1.5,
        },
        g,
      );
      var val = el(
        'text',
        narrow
          ? {
              x: W - R,
              y: top + 11,
              'text-anchor': 'end',
              'font-size': 11,
              fill: '#6b7280',
            }
          : {
              x: Math.min(sx(r.x), W - R) + 5,
              y: cy + 4,
              'font-size': 11,
              fill: '#6b7280',
            },
        g,
      );
      val.textContent = narrow
        ? xfmt(r.x) + ' · ' + usd(r.total_usd)
        : xfmt(r.x) + ' · ' + r.country + ' · ' + usd(r.total_usd);
      el(
        'rect',
        {
          x: 0,
          y: top - gap / 2,
          width: W,
          height: LH + h + gap,
          fill: 'transparent',
        },
        g,
      );
      bindTip(g, r);
    });
  }

  // ---------- scatter ----------
  function scatter(rows) {
    var svg = document.getElementById('scatter');
    svg.innerHTML = '';
    var m = rows.filter(function (r) {
      return r.status === 'modelled';
    });
    var W = Math.max(svg.parentNode.clientWidth - 4, 240),
      H = 400,
      L = 46,
      R = 14,
      T = 12,
      B = 40;
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);
    if (!m.length) return;
    var ratios = m.map(function (r) {
      return r.cost_per_person / r.model_cost_per_person;
    });
    var lo = Math.min(0.25, Math.min.apply(null, ratios) / 1.3),
      hi = Math.max(4, Math.max.apply(null, ratios) * 1.3);
    var ymax = Math.max(
      BAR * 1.5,
      Math.max.apply(
        null,
        m.map(function (r) {
          return r.x;
        }),
      ) * 1.1,
    );
    var sx = function (v) {
      return (
        L +
        ((W - L - R) * (Math.log(v) - Math.log(lo))) /
          (Math.log(hi) - Math.log(lo))
      );
    };
    var sy = function (v) {
      return T + (H - T - B) * (1 - Math.min(v, ymax) / ymax);
    };
    var ax = el('g', { class: 'axis' }, svg);
    [0.25, 0.5, 1, 2, 4, 8, 16].forEach(function (v) {
      if (v < lo || v > hi) return;
      el('line', { x1: sx(v), x2: sx(v), y1: T, y2: H - B }, ax);
      el(
        'text',
        { x: sx(v), y: H - B + 14, 'text-anchor': 'middle' },
        ax,
      ).textContent = v + 'x';
    });
    var ystep = ymax > 60 ? 20 : ymax > 30 ? 10 : 5;
    for (var v = 0; v <= ymax; v += ystep) {
      el('line', { x1: L, x2: W - R, y1: sy(v), y2: sy(v) }, ax);
      el(
        'text',
        { x: L - 6, y: sy(v) + 4, 'text-anchor': 'end' },
        ax,
      ).textContent = v + 'x';
    }
    el(
      'text',
      {
        x: (L + W - R) / 2,
        y: H - 6,
        'text-anchor': 'middle',
        fill: '#6b7280',
        'font-size': 11,
      },
      svg,
    ).textContent = 'actual cost per person ÷ model price';
    el(
      'line',
      {
        x1: L,
        x2: W - R,
        y1: sy(BAR),
        y2: sy(BAR),
        stroke: '#7c3aed',
        'stroke-dasharray': '4 3',
        'stroke-width': 1.5,
      },
      svg,
    );
    el(
      'line',
      {
        x1: sx(1),
        x2: sx(1),
        y1: T,
        y2: H - B,
        stroke: '#9ca3af',
        'stroke-dasharray': '2 3',
      },
      svg,
    );
    var maxSpend = Math.max.apply(
      null,
      m
        .map(function (r) {
          return r.total_usd;
        })
        .concat([1]),
    );
    m.slice()
      .sort(function (a, b) {
        return b.total_usd - a.total_usd;
      })
      .forEach(function (r) {
        var c = el(
          'circle',
          {
            cx: sx(r.cost_per_person / r.model_cost_per_person),
            cy: sy(r.x),
            r: 3 + 18 * Math.sqrt(r.total_usd / maxSpend),
            fill: COL[r.model],
            'fill-opacity': 0.55,
            stroke: COL[r.model],
            'stroke-width': 1,
          },
          svg,
        );
        bindTip(c, r);
      });
  }

  // ---------- unmodelled spend ----------
  function noModel(rows) {
    var svg = document.getElementById('nomodel');
    svg.innerHTML = '';
    var by = {};
    rows
      .filter(function (r) {
        return r.status === 'no_model' || r.status === 'no_country_data';
      })
      .forEach(function (r) {
        var k =
          r.status === 'no_country_data'
            ? r.service_name + " (outside the models' countries)"
            : r.service_name;
        by[k] = by[k] || { k: k, usd: 0, n: 0 };
        by[k].usd += r.total_usd;
        by[k].n += 1;
      });
    var items = Object.values(by).sort(function (a, b) {
      return b.usd - a.usd;
    });
    var W = Math.max(svg.parentNode.clientWidth - 4, 260),
      R = 120;
    var max = Math.max.apply(
      null,
      items
        .map(function (i) {
          return i.usd;
        })
        .concat([1]),
    );
    function amount(it) {
      return usd(it.usd) + ' · ' + it.n + ' opp' + (it.n === 1 ? '' : 's');
    }
    // Size the label column to the longest label, so "Kangaroo Mother Care (outside the models'
    // countries)" is not cut off on the left. Floor by character count: measured before the web
    // font loads, the text reads narrower.
    var longest = 0;
    items.forEach(function (it) {
      var t = el('text', { 'font-size': 12 }, svg);
      t.textContent = it.k;
      longest = Math.max(longest, t.getComputedTextLength(), it.k.length * 6.6);
      svg.removeChild(t);
    });
    var L = Math.max(longest + 16, 120);
    if (L <= W * 0.55) {
      // Wide: label column on the left, bar to its right.
      var rh = 22;
      svg.setAttribute('width', W);
      svg.setAttribute('height', Math.max(items.length * rh + 10, 40));
      items.forEach(function (it, i) {
        var y = 4 + i * rh,
          bw = ((W - L - R) * it.usd) / max;
        el(
          'text',
          {
            x: L - 8,
            y: y + 14,
            'text-anchor': 'end',
            'font-size': 12,
            fill: '#1f2433',
          },
          svg,
        ).textContent = it.k;
        el(
          'rect',
          {
            x: L,
            y: y + 3,
            width: Math.max(1, bw),
            height: rh - 8,
            rx: 2,
            fill: COL.none,
          },
          svg,
        );
        el(
          'text',
          { x: L + bw + 6, y: y + 14, 'font-size': 11, fill: '#6b7280' },
          svg,
        ).textContent = amount(it);
      });
    } else {
      // Narrow (a phone): each label on its own line, its bar underneath.
      var rh2 = 38;
      svg.setAttribute('width', W);
      svg.setAttribute('height', Math.max(items.length * rh2 + 6, 40));
      items.forEach(function (it, i) {
        var y = 4 + i * rh2,
          bw = ((W - R) * it.usd) / max;
        el(
          'text',
          { x: 0, y: y + 12, 'font-size': 12, fill: '#1f2433' },
          svg,
        ).textContent = it.k;
        el(
          'rect',
          {
            x: 0,
            y: y + 18,
            width: Math.max(1, bw),
            height: 12,
            rx: 2,
            fill: COL.none,
          },
          svg,
        );
        el(
          'text',
          { x: bw + 6, y: y + 28, 'font-size': 11, fill: '#6b7280' },
          svg,
        ).textContent = amount(it);
      });
    }
  }

  // ---------- table ----------
  var sortKey = null,
    sortDir = 1;
  // The result columns come straight after the name: the table is wider than its box, and the
  // multiple and status are what a reader came for, so they must not sit past the right edge.
  var COLS = [
    ['rank', '#', 'n'],
    ['name', 'Opportunity'],
    ['x', 'x at actual cost', 'n', xfmt],
    ['status_text', 'Status'],
    [
      'baseline_level',
      'Baseline from',
      '',
      function (v) {
        return v || '–';
      },
      'Where the place figures came from: the most local area Labs holds for where the visits happened',
    ],
    [
      'reached',
      'People',
      'n',
      function (v) {
        return num(v);
      },
      'Distinct people with approved work (see the notes below)',
    ],
    ['cost_per_person', 'Cost / person', 'n', usd],
    ['model_cost_per_person', 'Model price', 'n', usd],
    ['model_x', 'Model x', 'n', xfmt],
    [
      'deaths_averted',
      'Deaths averted',
      'n',
      function (v) {
        return v == null ? '–' : num(v, 1);
      },
    ],
    ['cost_per_death', '$ / death', 'n', usd],
    ['total_usd', 'Total cost', 'n', usd],
    [
      'works_per_person',
      'Work / person',
      'n',
      function (v) {
        return v == null ? '–' : num(v, 2);
      },
      'Approved work per person: how far people got through a longitudinal programme',
    ],
    ['service_name', 'Delivery type'],
    ['country_name', 'Country'],
  ];
  // On a phone the header row is hidden (the rows are cards), so sorting comes from this control.
  var sortSel = document.getElementById('sort-sm');
  sortSel.innerHTML =
    '<option value="">Rank</option>' +
    COLS.filter(function (c) {
      return ['rank', 'status_text', 'baseline_level'].indexOf(c[0]) < 0;
    })
      .map(function (c) {
        return '<option value="' + c[0] + '">' + esc(c[1]) + '</option>';
      })
      .join('');
  sortSel.addEventListener('change', function () {
    sortKey = sortSel.value || null;
    sortDir = sortKey === 'name' || sortKey === 'country_name' ? 1 : -1;
    table(current);
  });
  document.getElementById('sort-dir').addEventListener('click', function () {
    sortDir = -sortDir;
    table(current);
  });
  function table(rows) {
    var t = document.getElementById('tbl');
    var list = rows.slice();
    if (sortKey)
      list.sort(function (a, b) {
        var x = a[sortKey],
          y = b[sortKey];
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
      });
    // Built as nodes with textContent, so nothing from the data is ever parsed as HTML.
    function node(tag, cls, text) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    }
    var thead = node('thead'),
      hr = node('tr');
    COLS.forEach(function (c) {
      var th = node(
        'th',
        c[2] || '',
        c[1] + (sortKey === c[0] ? (sortDir > 0 ? ' ▲' : ' ▼') : ''),
      );
      th.dataset.k = c[0];
      if (c[4]) th.title = c[4];
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    var tbody = node('tbody');
    list.forEach(function (r) {
      var tr = node('tr');
      COLS.forEach(function (c) {
        var v = r[c[0]],
          td = node('td', 'c-' + c[0] + ' ' + (c[2] || ''));
        td.setAttribute('data-l', c[1]);
        if (c[0] === 'status_text') {
          var cls = r.status === 'modelled' ? (r.clears_bar ? 'ok' : 'lo') : '';
          td.appendChild(
            node(
              'span',
              'pill ' + cls,
              r.status === 'modelled'
                ? r.clears_bar
                  ? 'Clears bar'
                  : 'Below bar'
                : v,
            ),
          );
        } else if (c[0] === 'name') {
          if (r.rank) td.appendChild(node('span', 'rk', '#' + r.rank + ' '));
          var a = node('a', '', v);
          a.href = '/labs/pulse/opp/' + encodeURIComponent(r.id) + '/';
          td.appendChild(a);
          var sub = node(
            'div',
            '',
            (r.org_slug || '') + (r.active ? ' · active' : ''),
          );
          sub.style.cssText = 'color:#9ca3af;font-size:11px';
          td.appendChild(sub);
        } else if (c[0] === 'service_name' && r.model) {
          var sw = node('span', 'sw');
          sw.style.background = COL[r.model];
          sw.style.marginRight = '5px';
          td.appendChild(sw);
          td.appendChild(document.createTextNode(v == null ? '–' : v));
        } else if (c[0] === 'x' && r.model) {
          var xa = node('a', '', xfmt(v));
          xa.href =
            '/labs/cea/implementation/opp/' + encodeURIComponent(r.id) + '/';
          xa.title = 'How this was worked out, end to end';
          td.appendChild(xa);
        } else {
          td.appendChild(
            document.createTextNode(c[3] ? c[3](v, r) : v == null ? '–' : v),
          );
          if (c[0] === 'reached' && r.reach_basis === 'busiest_payment_unit') {
            var est = node('i', '', 'estimate');
            est.title = 'Busiest payment unit, not yet a distinct count';
            td.appendChild(document.createTextNode(' '));
            td.appendChild(est);
          }
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    t.replaceChildren(thead, tbody);
    sortSel.value = sortKey || '';
    t.querySelectorAll('th').forEach(function (th) {
      th.addEventListener('click', function () {
        var k = th.dataset.k;
        if (sortKey === k) sortDir = -sortDir;
        else {
          sortKey = k;
          sortDir =
            k === 'name' || k === 'rank' || k === 'country_name' ? 1 : -1;
        }
        table(current);
      });
    });
  }

  // ---------- map ----------
  var map = null,
    mapReady = false;
  function xColour(x) {
    if (x == null) return COL.none;
    var t = Math.max(0, Math.min(1, Math.log(x / 1) / Math.log(30)));
    var stops = [
      [185, 28, 28],
      [234, 179, 8],
      [21, 128, 61],
    ];
    var a = t < 0.5 ? stops[0] : stops[1],
      b = t < 0.5 ? stops[1] : stops[2],
      u = t < 0.5 ? t * 2 : (t - 0.5) * 2;
    return (
      'rgb(' +
      a
        .map(function (v, i) {
          return Math.round(v + (b[i] - v) * u);
        })
        .join(',') +
      ')'
    );
  }
  function geo(rows) {
    var maxSpend = Math.max.apply(
      null,
      rows
        .map(function (r) {
          return r.total_usd;
        })
        .concat([1]),
    );
    return {
      type: 'FeatureCollection',
      features: rows
        .filter(function (r) {
          return r.lat != null && r.total_usd > 0;
        })
        .sort(function (a, b) {
          return b.total_usd - a.total_usd;
        })
        .map(function (r) {
          return {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [r.lon, r.lat] },
            properties: {
              id: r.id,
              colour: xColour(r.status === 'modelled' ? r.x : null),
              radius: 3 + 20 * Math.sqrt(r.total_usd / maxSpend),
            },
          };
        }),
    };
  }
  function initMap() {
    var box = document.getElementById('map');
    if (!window.mapboxgl || !window.MAPBOX_TOKEN) {
      box.innerHTML =
        '<p style="padding:16px;color:#6b7280;font-size:13px">Map unavailable here (no map token).</p>';
      return;
    }
    try {
      mapboxgl.accessToken = window.MAPBOX_TOKEN;
      map = new mapboxgl.Map({
        container: box,
        style: 'mapbox://styles/mapbox/light-v11',
        center: [15, 3],
        zoom: 2.4,
        cooperativeGestures: window.matchMedia('(max-width: 700px)').matches,
      });
    } catch (e) {
      box.innerHTML =
        '<p style="padding:16px;color:#6b7280;font-size:13px">Map unavailable in this browser.</p>';
      return;
    }
    map.on('load', function () {
      map.addSource('opps', { type: 'geojson', data: geo(current) });
      map.addLayer({
        id: 'opps',
        type: 'circle',
        source: 'opps',
        paint: {
          'circle-color': ['get', 'colour'],
          'circle-radius': ['get', 'radius'],
          'circle-opacity': 0.7,
          'circle-stroke-color': '#fff',
          'circle-stroke-width': 1,
        },
      });
      map.on('mousemove', 'opps', function (e) {
        map.getCanvas().style.cursor = 'pointer';
        var id = e.features[0].properties.id,
          r = current.find(function (x) {
            return x.id === id;
          });
        if (r) showTip(e.originalEvent, rowTip(r));
      });
      map.on('mouseleave', 'opps', function () {
        map.getCanvas().style.cursor = '';
        hideTip();
      });
      map.on('click', 'opps', function (e) {
        var id = e.features[0].properties.id,
          r = current.find(function (x) {
            return x.id === id;
          });
        if (r) tapTip(e.originalEvent, rowTip(r));
      });
      mapReady = true;
      fit();
    });
  }
  function fit() {
    if (!mapReady) return;
    var pts = current.filter(function (r) {
      return r.lat != null && r.total_usd > 0;
    });
    if (!pts.length) return;
    var b = new mapboxgl.LngLatBounds();
    pts.forEach(function (r) {
      b.extend([r.lon, r.lat]);
    });
    map.fitBounds(b, { padding: 40, maxZoom: 6, duration: 0 });
  }

  var current = [];
  function params() {
    // What the server needs to work the view out: the what-if (as the URL keeps it) and the filters.
    var p = new URLSearchParams();
    if (W.basis !== DEF.basis) p.set('basis', W.basis);
    if (W.rank !== DEF.rank) p.set('rank', W.rank);
    if (W.bar !== DEF.bar) p.set('bar', W.bar);
    Object.keys(W.m).forEach(function (k) {
      ['extra', 'effect', 'reach'].forEach(function (f) {
        if (W.m[k][f] !== DEF.m[k][f]) p.set(k + '_' + f, W.m[k][f]);
      });
    });
    var shown = Object.keys(state.models).filter(function (k) {
      return state.models[k];
    });
    if (shown.length !== Object.keys(state.models).length)
      p.set('models', shown.join(','));
    if (state.country) p.set('country', state.country);
    if (state.active) p.set('active', '1');
    if (state.modelled) p.set('modelled', '1');
    if (state.minSpend) p.set('min_spend', state.minSpend);
    if (state.q) p.set('q', state.q);
    return p.toString();
  }
  function render() {
    current = V.rows;
    BAR = V.bar_x;
    document.getElementById('rank-title').textContent =
      'Ranked by ' +
      RANK_LABEL[W.rank] +
      (W.basis === 'model'
        ? ", at the models' prices"
        : ', at what each opportunity cost');
    tiles(V.totals);
    programmes(V.programmes);
    rankChart(current);
    scatter(current);
    noModel(current);
    table(current);
    if (mapReady) {
      map.getSource('opps').setData(geo(current));
      fit();
    }
  }
  var asking = null,
    asked = 0;
  function draw() {
    clearTimeout(asking);
    asking = setTimeout(function () {
      var mine = ++asked;
      fetch(D.view_url + '?' + params(), { credentials: 'same-origin' })
        .then(function (r) {
          return r.json();
        })
        .then(function (v) {
          if (mine !== asked || !v.rows) return;
          V = v;
          render();
        });
    }, 150);
  }
  render();
  initMap();
  var rt;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () {
      rankChart(current);
      scatter(current);
      noModel(current);
    }, 150);
  });
})();
