/* Worker connectivity: who sends their work as they go, where, and when.
 *
 * Everything drawn here comes from /api/connectivity/, which applies the test
 * in pulse/connectivity.py: a visit is "sent promptly" when it reached Connect
 * before the worker started their next one. The thresholds arrive in
 * `method` and are stated on the page rather than restated here.
 *
 * Plain SVG for the charts, the same call network.js makes; Mapbox for the map
 * when a token is configured, a flat plot otherwise.
 */
(function () {
  'use strict';

  var root = document.getElementById('conn');
  if (!root) return;

  var COLOURS = {
    online: '#35b39d',
    sometimes: '#8ea1ff',
    offline: '#6d76b5',
  };
  var LABELS = {
    online: 'Online',
    sometimes: 'Online some of the time',
    offline: 'Sends in batches',
  };
  var CLASSES = ['online', 'sometimes', 'offline'];
  var nf = new Intl.NumberFormat('en');

  // Under 10%, one decimal: 0.3% of visits delayed a week is a finding, and
  // rounding it to 0% would hide it.
  function pct(v) {
    if (v == null) return '—';
    if (v > 0 && v < 0.1) return (v * 100).toFixed(1) + '%';
    return Math.round(v * 100) + '%';
  }
  function el(tag, attrs, kids) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs || {}).forEach(function (k) {
      node.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (k) {
      node.appendChild(k);
    });
    return node;
  }
  function text(attrs, content) {
    var node = el('text', attrs);
    node.textContent = content;
    return node;
  }
  function titled(node, t) {
    var tt = el('title');
    tt.textContent = t;
    node.appendChild(tt);
    return node;
  }
  function h(tag, cls, content) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (content != null) n.textContent = content;
    return n;
  }
  function panel(title, legend) {
    var p = h('section', 'net-panel');
    var bar = h('div', 'net-panel-bar');
    bar.appendChild(h('h2', '', title));
    if (legend) {
      var l = h('div', 'net-legend');
      l.innerHTML = legend;
      bar.appendChild(l);
    }
    p.appendChild(bar);
    return p;
  }
  function legend() {
    return CLASSES.map(function (c) {
      return (
        '<span><i style="background:' +
        COLOURS[c] +
        '"></i>' +
        LABELS[c] +
        '</span>'
      );
    }).join('');
  }
  function note(p, content) {
    var n = h('p', 'net-note', content);
    p.appendChild(n);
    return n;
  }
  function chartbox(p, svg) {
    var box = h('div', 'net-chartbox');
    box.appendChild(svg);
    p.appendChild(box);
    return box;
  }

  /* ── every worker, by share of visits sent promptly ─────────────── */
  function histogram(dist) {
    var W = 1000,
      H = 260,
      PL = 48,
      PR = 12,
      PT = 16,
      PB = 40;
    var bins = dist.histogram || [];
    var m = dist.method || {};
    var mx = Math.max.apply(null, bins.concat([1]));
    var bw = (W - PL - PR) / (bins.length || 1);
    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      role: 'img',
      'aria-label': 'Workers by share of their visits sent promptly',
    });
    var y = function (v) {
      return H - PB - (v / mx) * (H - PT - PB);
    };
    [0, 0.5, 1].forEach(function (f) {
      var v = Math.round(mx * f);
      svg.appendChild(
        el('line', {
          x1: PL,
          y1: y(v),
          x2: W - PR,
          y2: y(v),
          class: 'net-grid',
        }),
      );
      svg.appendChild(
        text({ x: PL - 8, y: y(v) + 3, class: 'net-ax net-end' }, nf.format(v)),
      );
    });
    bins.forEach(function (n, i) {
      var lo = i / bins.length;
      var cls =
        lo >= (m.online_share || 0.8)
          ? 'online'
          : lo >= (m.sometimes_share || 0.2)
            ? 'sometimes'
            : 'offline';
      var hgt = H - PB - y(n);
      svg.appendChild(
        titled(
          el('rect', {
            x: PL + i * bw + 2,
            y: y(n),
            width: bw - 4,
            height: Math.max(hgt, n ? 1 : 0),
            fill: COLOURS[cls],
            rx: 2,
          }),
          i * 10 +
            '–' +
            (i + 1) * 10 +
            '% of visits sent promptly: ' +
            nf.format(n) +
            ' workers',
        ),
      );
      svg.appendChild(
        text(
          { x: PL + i * bw + bw / 2, y: H - PB + 16, class: 'net-ax net-mid' },
          i * 10 + '–' + (i + 1) * 10 + '%',
        ),
      );
    });
    svg.appendChild(
      text({ x: PL, y: H - 4, class: 'net-ax' }, '← never sent promptly'),
    );
    svg.appendChild(
      text({ x: W - PR, y: H - 4, class: 'net-ax net-end' }, 'always →'),
    );
    return svg;
  }

  /* ── week by week, as shares of the workers judged that week ────── */
  function weeklyChart(weeks) {
    var W = 1000,
      H = 240,
      PL = 48,
      PR = 12,
      PT = 16,
      PB = 30;
    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      role: 'img',
      'aria-label': 'Share of workers in each group, by week',
    });
    if (!weeks.length) return svg;
    var bw = (W - PL - PR) / weeks.length;
    var y = function (f) {
      return PT + (1 - f) * (H - PT - PB);
    };
    [0, 0.5, 1].forEach(function (f) {
      svg.appendChild(
        el('line', {
          x1: PL,
          y1: y(f),
          x2: W - PR,
          y2: y(f),
          class: 'net-grid',
        }),
      );
      svg.appendChild(
        text({ x: PL - 8, y: y(f) + 3, class: 'net-ax net-end' }, pct(f)),
      );
    });
    var lastYear = null;
    weeks.forEach(function (w, i) {
      var base = 0;
      var x = PL + i * bw;
      CLASSES.forEach(function (c) {
        var f = w.workers ? w[c] / w.workers : 0;
        svg.appendChild(
          titled(
            el('rect', {
              x: x + 0.3,
              y: y(base + f),
              width: Math.max(bw - 0.6, 0.5),
              height: (H - PT - PB) * f,
              fill: COLOURS[c],
              opacity: w.partial ? 0.35 : 0.9,
            }),
            new Date(w.t * 1000).toISOString().slice(0, 10) +
              ' · ' +
              LABELS[c] +
              ': ' +
              nf.format(w[c]) +
              ' of ' +
              nf.format(w.workers) +
              ' workers' +
              (w.partial ? ' (week in progress)' : ''),
          ),
        );
        base += f;
      });
      var d = new Date(w.t * 1000);
      var yr = d.getUTCFullYear();
      if (d.getUTCMonth() % 3 === 0 && d.getUTCDate() <= 7) {
        svg.appendChild(
          text(
            { x: x, y: H - PB + 16, class: 'net-ax' },
            d.toLocaleString('en', { month: 'short', timeZone: 'UTC' }) +
              (yr !== lastYear ? ' ' + yr : ''),
          ),
        );
        lastYear = yr;
      }
    });
    return svg;
  }

  /* ── hour of day, local to where the visit happened ─────────────── */
  function hourChart(hours) {
    var W = 1000,
      H = 240,
      PL = 48,
      PR = 12,
      PT = 16,
      PB = 30;
    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      role: 'img',
      'aria-label': 'Share of visits sent promptly, by local hour of day',
    });
    var bw = (W - PL - PR) / 24;
    var maxPairs = Math.max.apply(
      null,
      hours
        .map(function (r) {
          return r.pairs;
        })
        .concat([1]),
    );
    var y = function (f) {
      return PT + (1 - f) * (H - PT - PB);
    };
    [0, 0.5, 1].forEach(function (f) {
      svg.appendChild(
        el('line', {
          x1: PL,
          y1: y(f),
          x2: W - PR,
          y2: y(f),
          class: 'net-grid',
        }),
      );
      svg.appendChild(
        text({ x: PL - 8, y: y(f) + 3, class: 'net-ax net-end' }, pct(f)),
      );
    });
    var pts = [];
    hours.forEach(function (r, i) {
      var x = PL + i * bw;
      var vh = ((H - PT - PB) * r.pairs) / maxPairs;
      svg.appendChild(
        titled(
          el('rect', {
            x: x + 2,
            y: H - PB - vh,
            width: bw - 4,
            height: vh,
            fill: '#a9b3e8',
            opacity: 0.18,
          }),
          String(r.hour).padStart(2, '0') +
            ':00 · ' +
            nf.format(r.pairs) +
            ' visit pairs, ' +
            pct(r.share) +
            ' sent promptly',
        ),
      );
      if (r.share != null && r.pairs >= 20) {
        pts.push([x + bw / 2, y(r.share)]);
      }
      if (i % 3 === 0) {
        svg.appendChild(
          text(
            { x: x + bw / 2, y: H - PB + 16, class: 'net-ax net-mid' },
            String(r.hour).padStart(2, '0') + ':00',
          ),
        );
      }
    });
    if (pts.length > 1) {
      svg.appendChild(
        el('path', {
          d: pts
            .map(function (p, i) {
              return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1);
            })
            .join(' '),
          fill: 'none',
          stroke: COLOURS.online,
          'stroke-width': 2.4,
          'stroke-linejoin': 'round',
        }),
      );
      pts.forEach(function (p) {
        svg.appendChild(
          el('circle', { cx: p[0], cy: p[1], r: 3.2, fill: COLOURS.online }),
        );
      });
    }
    return svg;
  }

  /* ── formatting ─────────────────────────────────────────────────── */
  function delay(minutes) {
    if (minutes == null) return '—';
    if (minutes < 60) return Math.round(minutes) + ' min';
    if (minutes < 48 * 60)
      return (minutes / 60).toFixed(minutes < 600 ? 1 : 0) + ' h';
    return (minutes / 1440).toFixed(1) + ' days';
  }

  /* ── what the map can be coloured by ────────────────────────────────
     Each measure names the field it reads off a cell and a colour ramp.
     Connectivity measures run grey-blue (little) to green (much); delay
     measures run green (little) to marigold and sunset (much). A delay is the
     design working, so the ramp says "held longer", not "wrong". */
  var GOOD = ['#6d76b5', '#8ea1ff', '#35b39d'];
  var HELD = ['#35b39d', '#feaf31', '#e44434'];
  var MEASURES = {
    connected: {
      label: 'Online at least some of the time',
      field: 'connected_rate',
      stops: [0, 0.5, 1],
      colours: GOOD,
      fmt: pct,
    },
    online: {
      label: 'Online (sends as they go)',
      field: 'online_rate',
      stops: [0, 0.5, 1],
      colours: GOOD,
      fmt: pct,
    },
    seen: {
      label: 'Seen online at least once',
      field: 'seen_online_rate',
      stops: [0.5, 0.8, 1],
      colours: GOOD,
      fmt: pct,
    },
    delayed_1d: {
      label: 'Visits delayed over a day',
      field: 'delayed_1d_rate',
      stops: [0, 0.1, 0.3],
      colours: HELD,
      fmt: pct,
    },
    delayed_3d: {
      label: 'Visits delayed over 3 days',
      field: 'delayed_3d_rate',
      stops: [0, 0.05, 0.2],
      colours: HELD,
      fmt: pct,
    },
    delayed_7d: {
      label: 'Visits delayed over a week',
      field: 'delayed_7d_rate',
      stops: [0, 0.02, 0.1],
      colours: HELD,
      fmt: pct,
    },
    median: {
      label: 'Typical delay',
      field: 'median_delay_minutes',
      stops: [0, 240, 1440],
      colours: HELD,
      fmt: delay,
    },
  };
  var NO_DATA = '#3a3470';

  function colourExpr(m) {
    return [
      'case',
      ['has', m.field],
      [
        'interpolate',
        ['linear'],
        ['get', m.field],
        m.stops[0],
        m.colours[0],
        m.stops[1],
        m.colours[1],
        m.stops[2],
        m.colours[2],
      ],
      NO_DATA,
    ];
  }
  function colourFor(m, v) {
    if (v == null) return NO_DATA;
    var s = m.stops,
      c = m.colours;
    return v >= s[2] ? c[2] : v >= s[1] ? c[1] : c[0];
  }
  function measureLegend(m) {
    return m.stops
      .map(function (s, i) {
        return (
          '<span><i style="background:' +
          m.colours[i] +
          '"></i>' +
          (i === 2 ? '≥ ' : i === 0 ? '' : '') +
          m.fmt(s) +
          '</span>'
        );
      })
      .join('');
  }

  /* ── where: one circle per ~11 km cell of workers ───────────────── */
  function cellFeatures(cells) {
    return cells.map(function (c) {
      var props = {};
      Object.keys(c).forEach(function (k) {
        if (c[k] != null) props[k] = c[k];
      });
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
        properties: props,
      };
    });
  }
  function popupHtml(c) {
    return (
      '<b>' +
      nf.format(c.workers) +
      ' workers</b>' +
      '<br>' +
      CLASSES.map(function (k) {
        return LABELS[k] + ': ' + nf.format(c[k] || 0);
      }).join('<br>') +
      '<br>Seen online at least once: ' +
      pct(c.seen_online_rate) +
      '<br>Typical delay: ' +
      delay(c.median_delay_minutes) +
      '<br>Delayed over a day / 3 days / a week: ' +
      pct(c.delayed_1d_rate) +
      ' / ' +
      pct(c.delayed_3d_rate) +
      ' / ' +
      pct(c.delayed_7d_rate)
    );
  }
  function liveMap(container, cells, measure) {
    var map = window.ConnectMap.createMap(container, {
      center: [20, 5],
      zoom: 2.4,
      interactive: true,
    });
    map.addControl(
      new window.mapboxgl.NavigationControl({ showCompass: false }),
      'top-right',
    );
    map.scrollZoom.disable();
    map.on('error', function (e) {
      console.error(
        '[pulse:connectivity] map error:',
        (e && e.error && e.error.message) || e,
      );
    });
    var data = { type: 'FeatureCollection', features: cellFeatures(cells) };
    var ready = false;
    var pending = measure;
    map.on('load', function () {
      window.ConnectMap.calmBasemap(map, { text: 0.45 });
      map.addSource('cells', { type: 'geojson', data: data });
      map.addLayer({
        id: 'cells',
        type: 'circle',
        source: 'cells',
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['sqrt', ['get', 'workers']],
            1,
            4,
            10,
            18,
            30,
            40,
          ],
          'circle-color': colourExpr(pending),
          'circle-opacity': 0.78,
          'circle-stroke-color': colourExpr(pending),
          'circle-stroke-width': 1,
        },
      });
      ready = true;
      if (cells.length) window.ConnectMap.fit(map, data, 60);
      var popup = new window.mapboxgl.Popup({
        closeButton: false,
        offset: 10,
        className: 'net-pop',
      });
      map.on('mouseenter', 'cells', function (e) {
        map.getCanvas().style.cursor = 'pointer';
        popup
          .setLngLat(e.features[0].geometry.coordinates.slice())
          .setHTML(popupHtml(e.features[0].properties))
          .addTo(map);
      });
      map.on('mouseleave', 'cells', function () {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });
    });
    return {
      colourBy: function (m) {
        pending = m;
        if (!ready) return;
        map.setPaintProperty('cells', 'circle-color', colourExpr(m));
        map.setPaintProperty('cells', 'circle-stroke-color', colourExpr(m));
      },
    };
  }
  function flatMap(box, cells, measure) {
    function draw(m) {
      var W = 1000,
        H = 520;
      var svg = el('svg', {
        viewBox: '0 0 ' + W + ' ' + H,
        role: 'img',
        'aria-label': 'Where workers are, coloured by ' + m.label,
      });
      if (cells.length) {
        var lats = cells.map(function (c) {
          return c.lat;
        });
        var lons = cells.map(function (c) {
          return c.lon;
        });
        var la0 = Math.min.apply(null, lats) - 1,
          la1 = Math.max.apply(null, lats) + 1,
          lo0 = Math.min.apply(null, lons) - 1,
          lo1 = Math.max.apply(null, lons) + 1;
        cells.forEach(function (c) {
          svg.appendChild(
            titled(
              el('circle', {
                cx: ((c.lon - lo0) / (lo1 - lo0)) * W,
                cy: H - ((c.lat - la0) / (la1 - la0)) * H,
                r: 3 + Math.sqrt(c.workers) * 1.6,
                fill: colourFor(m, c[m.field]),
                opacity: 0.78,
              }),
              nf.format(c.workers) +
                ' workers · ' +
                m.label +
                ': ' +
                m.fmt(c[m.field]),
            ),
          );
        });
      }
      box.innerHTML = '';
      box.appendChild(svg);
    }
    draw(measure);
    return { colourBy: draw };
  }

  /* ── the URL is the state ───────────────────────────────────────── */
  var params = new URLSearchParams(location.search);
  function go(changes) {
    Object.keys(changes).forEach(function (k) {
      if (changes[k]) params.set(k, changes[k]);
      else params.delete(k);
    });
    location.search = params.toString();
  }
  // `colour` is the page's own choice; the API never sees it, so switching it
  // is a repaint rather than a refetch and does not split the server's cache.
  function apiQuery() {
    var q = new URLSearchParams(params);
    q.delete('colour');
    var s = q.toString();
    return s ? '?' + s : '';
  }

  /* ── filters ────────────────────────────────────────────────────── */
  function filters(data) {
    var bar = h('div', 'conn-filters');
    function select(label, key, options) {
      var wrap = h('label', 'conn-select');
      wrap.appendChild(h('span', '', label));
      var s = document.createElement('select');
      s.className = 'net-filter';
      s.appendChild(new Option('All', ''));
      options.forEach(function (o) {
        s.appendChild(new Option(o.name, o.value));
      });
      var current = params.get(key) || '';
      if (current && !options.some((o) => o.value === current)) {
        s.appendChild(new Option(current, current));
      }
      s.value = current;
      s.addEventListener('change', function () {
        var ch = {};
        ch[key] = s.value;
        go(ch);
      });
      wrap.appendChild(s);
      bar.appendChild(wrap);
    }
    function date(label, key) {
      var wrap = h('label', 'conn-select');
      wrap.appendChild(h('span', '', label));
      var d = document.createElement('input');
      d.type = 'date';
      d.className = 'net-filter conn-date';
      d.min = (data.method || {}).reliable_from || '';
      d.value = params.get(key) || '';
      d.addEventListener('change', function () {
        var ch = {};
        ch[key] = d.value;
        go(ch);
      });
      wrap.appendChild(d);
      bar.appendChild(wrap);
    }
    select(
      'Program',
      'program',
      (data.programs || []).map(function (p) {
        return { name: p.name, value: String(p.id) };
      }),
    );
    if ((data.orgs || []).length) {
      select(
        'Partner',
        'org',
        data.orgs.map(function (o) {
          return { name: o.name, value: o.slug };
        }),
      );
    }
    select(
      'Delivery type',
      'service',
      (data.services || []).map(function (s) {
        return { name: s.name, value: s.slug };
      }),
    );
    select(
      'Country',
      'country',
      (data.countries || []).map(function (c) {
        return { name: c.name, value: c.code };
      }),
    );
    select(
      'Opportunity',
      'opportunity',
      (data.by_opportunity || [])
        .slice()
        .sort(function (a, b) {
          return a.name.localeCompare(b.name);
        })
        .map(function (o) {
          return { name: o.name, value: String(o.key) };
        }),
    );
    date('From', 'from');
    date('To', 'to');
    if (Array.from(params.keys()).some((k) => k !== 'colour')) {
      var clear = h('button', 'net-back conn-clear', 'Clear filters');
      clear.type = 'button';
      clear.addEventListener('click', function () {
        var keep = params.get('colour');
        params = new URLSearchParams();
        if (keep) params.set('colour', keep);
        location.search = params.toString();
      });
      bar.appendChild(clear);
    }
    return bar;
  }

  function kpi(n, label, colour) {
    var d = h('div', 'net-kpi');
    var num = h('div', 'net-kpi-n', n);
    if (colour) num.style.color = colour;
    d.appendChild(num);
    d.appendChild(h('div', 'net-kpi-l', label));
    return d;
  }

  /* ── partner and opportunity tables: sortable, click to narrow ──── */
  var TABLE_COLS = [
    ['workers', 'Workers', nf.format.bind(nf)],
    ['seen_online_rate', 'Seen online', pct],
    ['online_rate', 'Online', pct],
    ['connected_rate', 'At least sometimes', pct],
    ['median_delay_minutes', 'Typical delay', delay],
    ['delayed_1d_rate', 'Delayed >1 day', pct],
    ['delayed_3d_rate', 'Delayed >3 days', pct],
    ['delayed_7d_rate', 'Delayed >7 days', pct],
  ];
  function table(rows, opts) {
    var wrap = h('div', 'net-tablewrap');
    var sort = { key: 'delayed_3d_rate', dir: -1 };
    var showAll = false;
    var LIMIT = 25;
    function paint() {
      var sorted = rows.slice().sort(function (a, b) {
        var x = sort.key === 'name' ? a.name : a[sort.key],
          y = sort.key === 'name' ? b.name : b[sort.key];
        if (x == null) return 1;
        if (y == null) return -1;
        return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
      });
      var shown = showAll ? sorted : sorted.slice(0, LIMIT);
      var t = document.createElement('table');
      t.className = 'net-table conn-table';
      var head = document.createElement('tr');
      [['name', opts.nameLabel]].concat(TABLE_COLS).forEach(function (c) {
        var th = h(
          'th',
          'net-th' + (c[0] === sort.key ? ' net-sorted' : ''),
          c[1],
        );
        th.tabIndex = 0;
        th.setAttribute(
          'aria-sort',
          c[0] === sort.key
            ? sort.dir === 1
              ? 'ascending'
              : 'descending'
            : 'none',
        );
        th.addEventListener('click', function () {
          sort = {
            key: c[0],
            dir: sort.key === c[0] ? -sort.dir : c[0] === 'name' ? 1 : -1,
          };
          paint();
        });
        head.appendChild(th);
      });
      var thead = document.createElement('thead');
      thead.appendChild(head);
      t.appendChild(thead);
      var body = document.createElement('tbody');
      shown.forEach(function (r) {
        var tr = document.createElement('tr');
        tr.className = 'conn-row';
        tr.tabIndex = 0;
        var nameCell = h('td', 'net-td');
        nameCell.appendChild(h('div', 'conn-name', r.name));
        if (opts.sub) {
          var sub = opts.sub(r);
          if (sub) nameCell.appendChild(h('div', 'conn-sub', sub));
        }
        tr.appendChild(nameCell);
        TABLE_COLS.forEach(function (c) {
          var td = h('td', 'net-td conn-num', c[2](r[c[0]]));
          if (c[0].indexOf('delayed') === 0 && r[c[0]] != null) {
            var m = MEASURES[c[0].replace('_rate', '')];
            td.style.color = colourFor(m, r[c[0]]);
          }
          tr.appendChild(td);
        });
        var open = function () {
          opts.onPick(r);
        };
        tr.addEventListener('click', open);
        tr.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') open();
        });
        body.appendChild(tr);
      });
      t.appendChild(body);
      wrap.innerHTML = '';
      wrap.appendChild(t);
      if (rows.length > LIMIT) {
        var more = h(
          'button',
          'net-back conn-more',
          showAll ? 'Show the first ' + LIMIT : 'Show all ' + rows.length,
        );
        more.type = 'button';
        more.addEventListener('click', function () {
          showAll = !showAll;
          paint();
        });
        wrap.appendChild(more);
      }
    }
    paint();
    return wrap;
  }

  function render(data) {
    root.innerHTML = '';
    var dist = data.distribution || {};
    var sum = data.summary || {};
    var m = data.method || {};
    var bands = m.delay_bands_days || {};

    root.appendChild(filters(data));

    var kpis = h('div', 'net-kpis conn-kpis');
    kpis.appendChild(kpi(nf.format(sum.workers || 0), 'workers in view'));
    kpis.appendChild(
      kpi(
        pct(sum.seen_online_rate),
        'seen online at least once · ' + nf.format(sum.seen_online || 0),
        COLOURS.online,
      ),
    );
    CLASSES.forEach(function (c) {
      kpis.appendChild(
        kpi(
          sum.judged ? pct((sum[c] || 0) / sum.judged) : '—',
          LABELS[c] + ' · ' + nf.format(sum[c] || 0),
          COLOURS[c],
        ),
      );
    });
    root.appendChild(kpis);

    var dk = h('div', 'net-kpis conn-kpis');
    dk.appendChild(kpi(delay(sum.median_delay_minutes), 'typical delay'));
    [
      ['delayed_1d', 'visits delayed over a day'],
      ['delayed_3d', 'visits delayed over 3 days'],
      ['delayed_7d', 'visits delayed over a week'],
    ].forEach(function (b) {
      dk.appendChild(
        kpi(
          pct(sum[b[0] + '_rate']),
          b[1] + ' · ' + nf.format(sum[b[0]] || 0),
          colourFor(MEASURES[b[0]], sum[b[0] + '_rate']),
        ),
      );
    });
    root.appendChild(dk);

    // The map comes first: it is where a targeted concern shows.
    var mp = data.map || { cells: [] };
    var chosen = MEASURES[params.get('colour')]
      ? params.get('colour')
      : 'connected';
    var p3 = panel('Where', measureLegend(MEASURES[chosen]));
    var legendNode = p3.querySelector('.net-legend');
    var pick = h('label', 'conn-select conn-colour');
    pick.appendChild(h('span', '', 'Colour by'));
    var sel = document.createElement('select');
    sel.className = 'net-filter';
    Object.keys(MEASURES).forEach(function (k) {
      sel.appendChild(new Option(MEASURES[k].label, k));
    });
    sel.value = chosen;
    pick.appendChild(sel);
    p3.querySelector('.net-panel-bar').insertBefore(pick, legendNode);
    var canMap = window.ConnectMap && window.mapboxgl && window.MAPBOX_TOKEN;
    var box = h('div', canMap ? 'net-globe' : 'net-chartbox');
    p3.appendChild(box);
    note(
      p3,
      'Each circle is an area about 11 km across, placed where its workers do most of their visits and ' +
        'sized by how many workers it holds. Hover for every measure. Areas with fewer than ' +
        mp.min_workers_per_cell +
        ' workers are left off (' +
        nf.format(mp.withheld_workers || 0) +
        ' workers), so no circle points at one person.',
    );
    root.appendChild(p3);
    var painter = canMap
      ? liveMap(box, mp.cells || [], MEASURES[chosen])
      : flatMap(box, mp.cells || [], MEASURES[chosen]);
    sel.addEventListener('change', function () {
      var k = sel.value;
      painter.colourBy(MEASURES[k]);
      legendNode.innerHTML = measureLegend(MEASURES[k]);
      params.set('colour', k);
      history.replaceState(null, '', '?' + params.toString());
    });

    var pOrg = panel('Partners');
    note(
      pOrg,
      'Every partner in view, sorted by how much of their work was delayed over 3 days. Click a partner ' +
        'to narrow the whole page to them. Seen online: had at least one visit reach Connect within ' +
        m.seen_online_within_minutes +
        ' minutes of starting it.',
    );
    pOrg.appendChild(
      table(data.by_org || [], {
        nameLabel: 'Partner',
        onPick: function (r) {
          go({ org: r.key, opportunity: '' });
        },
      }),
    );
    root.appendChild(pOrg);

    var pOpp = panel('Opportunities');
    note(
      pOpp,
      'The same, per opportunity. Click one to narrow the page to it.',
    );
    pOpp.appendChild(
      table(data.by_opportunity || [], {
        nameLabel: 'Opportunity',
        sub: function (r) {
          return (
            (r.org_name || '') +
            (r.active
              ? ' · active'
              : r.end_date
                ? ' · ended ' + r.end_date
                : '')
          );
        },
        onPick: function (r) {
          go({ opportunity: String(r.key) });
        },
      }),
    );
    root.appendChild(pOpp);

    var p1 = panel('Every worker, by how often they send as they go', legend());
    chartbox(p1, histogram({ histogram: dist.histogram, method: m }));
    note(
      p1,
      'Each bar counts workers by the share of their visits that reached Connect before they ' +
        'started their next visit. ' +
        nf.format(dist.unjudged || 0) +
        ' workers with fewer than ' +
        m.min_pairs_worker +
        ' usable visit pairs are not placed here; the "seen online" figure above still counts them.',
    );
    root.appendChild(p1);

    var p2 = panel('Week by week', legend());
    chartbox(p2, weeklyChart(data.weekly || []));
    note(
      p2,
      'Each week, the workers with at least ' +
        m.min_pairs_week +
        ' usable visit pairs, split by how promptly they sent that week. The week in progress is faded.',
    );
    root.appendChild(p2);

    var p4 = panel('When there is signal');
    chartbox(p4, hourChart(data.hours || []));
    note(
      p4,
      'The line is the share of visits sent promptly, by the local hour the visit started. ' +
        'The faint bars are how many visits each hour holds. A climb late in the day means ' +
        'signal at home rather than in the field.',
    );
    root.appendChild(p4);

    var p5 = panel('How this is measured');
    var backlog = data.backlog_days || [];
    note(
      p5,
      'Offline-first is the design: a worker with no signal keeps working and their visits wait on the ' +
        'phone until it has one. So a delay here is not a fault. It is how long the work waited, which ' +
        'matters because verification, payment and supervision all wait with it, and a phone lost before ' +
        'it syncs loses the work.',
    );
    note(
      p5,
      'A visit counts as sent promptly when it reached Connect before the worker opened their next ' +
        'visit. Only visits started ' +
        m.pair_min_gap_minutes +
        ' minutes to ' +
        m.pair_max_gap_minutes / 60 +
        ' hours apart count: closer than that there is no time to send, further apart is usually the end ' +
        'of the day. Online means ' +
        pct(m.online_share) +
        ' or more of a worker’s visits were sent promptly; some of the time means ' +
        pct(m.sometimes_share) +
        ' or more. Seen online means at least one visit reached Connect within ' +
        m.seen_online_within_minutes +
        ' minutes of being started, which counts workers whose visits are too sparse to pair. Delay is ' +
        'from starting a visit to Connect receiving it, in bands of ' +
        [bands.delayed_1d, bands.delayed_3d, bands.delayed_7d].join(', ') +
        ' days. Visits whose phone clock is wrong are skipped. Connect has recorded when visits arrive ' +
        'only since ' +
        m.reliable_from +
        ', so nothing earlier is counted.',
    );
    note(
      p5,
      backlog.length
        ? backlog.length +
            ' day' +
            (backlog.length === 1 ? '' : 's') +
            ' left out of "sent promptly" because forwarding to Connect was stalled (even the fastest ' +
            'tenth of visits took over ' +
            m.backlog_p10_minutes / 60 +
            ' hours to arrive): ' +
            backlog
              .map(function (d) {
                return d.date;
              })
              .join(', ') +
            '.'
        : 'No days were left out for stalled forwarding (a day on which even the fastest tenth of ' +
            'visits took over ' +
            m.backlog_p10_minutes / 60 +
            ' hours to arrive).',
    );
    root.appendChild(p5);
  }

  fetch(root.dataset.endpoint + apiQuery(), { credentials: 'same-origin' })
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(render)
    .catch(function (e) {
      console.error('[pulse:connectivity]', e);
      root.innerHTML = '';
      root.appendChild(
        h('p', 'net-loading', 'Could not load connectivity: ' + e.message),
      );
    });
})();
