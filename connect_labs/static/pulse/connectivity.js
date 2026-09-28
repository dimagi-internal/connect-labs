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

  function pct(v) {
    return v == null ? '—' : Math.round(v * 100) + '%';
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

  /* ── where: one circle per ~11 km cell of workers ───────────────── */
  function cellFeatures(cells) {
    return cells.map(function (c) {
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
        properties: c,
      };
    });
  }
  var MAP_COLOUR = [
    'interpolate',
    ['linear'],
    ['get', 'connected_rate'],
    0,
    COLOURS.offline,
    0.5,
    COLOURS.sometimes,
    1,
    COLOURS.online,
  ];
  function popupHtml(c) {
    return (
      '<b>' +
      nf.format(c.workers) +
      ' workers</b><br>' +
      CLASSES.map(function (k) {
        return LABELS[k] + ': ' + nf.format(c[k]);
      }).join('<br>') +
      '<br><span class="net-pop-tier">average ' +
      pct(c.share) +
      ' of visits sent promptly</span>'
    );
  }
  function liveMap(container, cells) {
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
          'circle-color': MAP_COLOUR,
          'circle-opacity': 0.75,
          'circle-stroke-color': MAP_COLOUR,
          'circle-stroke-width': 1,
        },
      });
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
  }
  function flatMap(cells) {
    var W = 1000,
      H = 520;
    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      role: 'img',
      'aria-label': 'Where workers are, coloured by connectivity',
    });
    if (!cells.length) return svg;
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
      var col =
        c.connected_rate >= 0.75
          ? COLOURS.online
          : c.connected_rate >= 0.25
            ? COLOURS.sometimes
            : COLOURS.offline;
      svg.appendChild(
        titled(
          el('circle', {
            cx: ((c.lon - lo0) / (lo1 - lo0)) * W,
            cy: H - ((c.lat - la0) / (la1 - la0)) * H,
            r: 3 + Math.sqrt(c.workers) * 1.6,
            fill: col,
            opacity: 0.75,
          }),
          nf.format(c.workers) +
            ' workers · ' +
            pct(c.connected_rate) +
            ' online at least some of the time',
        ),
      );
    });
    return svg;
  }

  /* ── filters: program and partner ───────────────────────────────── */
  function filters(data) {
    var params = new URLSearchParams(location.search);
    var bar = h('div', 'conn-filters');
    function select(label, key, options, current) {
      var wrap = h('label', 'conn-select');
      wrap.appendChild(h('span', '', label));
      var s = document.createElement('select');
      s.className = 'net-filter';
      s.appendChild(new Option('All', ''));
      options.forEach(function (o) {
        s.appendChild(new Option(o.name, o.value));
      });
      s.value = current || '';
      s.addEventListener('change', function () {
        if (s.value) params.set(key, s.value);
        else params.delete(key);
        location.search = params.toString();
      });
      wrap.appendChild(s);
      bar.appendChild(wrap);
    }
    select(
      'Program',
      'program',
      (data.programs || []).map(function (p) {
        return { name: p.name, value: String(p.id) };
      }),
      params.get('program'),
    );
    if ((data.orgs || []).length) {
      select(
        'Partner',
        'org',
        data.orgs.map(function (o) {
          return { name: o.name, value: o.slug };
        }),
        params.get('org'),
      );
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

  function render(data) {
    root.innerHTML = '';
    var dist = data.distribution || {};
    var m = data.method || {};
    var judged = dist.workers || 0;
    var classes = dist.classes || {};

    root.appendChild(filters(data));

    var kpis = h('div', 'net-kpis conn-kpis');
    kpis.appendChild(kpi(nf.format(judged), 'workers judged'));
    CLASSES.forEach(function (c) {
      kpis.appendChild(
        kpi(
          judged ? pct((classes[c] || 0) / judged) : '—',
          LABELS[c] + ' · ' + nf.format(classes[c] || 0),
          COLOURS[c],
        ),
      );
    });
    root.appendChild(kpis);

    var p1 = panel('Every worker, by how often they send as they go', legend());
    chartbox(p1, histogram({ histogram: dist.histogram, method: m }));
    note(
      p1,
      'Each bar counts workers by the share of their visits that reached Connect before they ' +
        'started their next visit. ' +
        nf.format(dist.unjudged || 0) +
        ' workers with fewer than ' +
        m.min_pairs_worker +
        ' usable visit pairs are not judged.',
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

    var mp = data.map || { cells: [] };
    var p3 = panel(
      'Where',
      '<span><i style="background:' +
        COLOURS.offline +
        '"></i>mostly batches</span><span><i style="background:' +
        COLOURS.sometimes +
        '"></i>mixed</span><span><i style="background:' +
        COLOURS.online +
        '"></i>mostly online</span>',
    );
    var canMap = window.ConnectMap && window.mapboxgl && window.MAPBOX_TOKEN;
    var box = h('div', canMap ? 'net-globe' : 'net-chartbox');
    if (!canMap) box.appendChild(flatMap(mp.cells || []));
    p3.appendChild(box);
    note(
      p3,
      'Each circle is an area about 11 km across, placed where its workers do most of their visits, ' +
        'sized by how many workers it holds and coloured by the share of them online at least some of the time. ' +
        'Areas with fewer than ' +
        mp.min_workers_per_cell +
        ' workers are left off (' +
        nf.format(mp.withheld_workers || 0) +
        ' workers), so no circle points at one person.',
    );
    root.appendChild(p3);
    if (canMap) liveMap(box, mp.cells || []);

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
      'A visit counts as sent promptly when it reached Connect before the worker opened their next ' +
        'visit. Only visits started ' +
        m.pair_min_gap_minutes +
        ' minutes to ' +
        m.pair_max_gap_minutes / 60 +
        ' hours apart count: closer than that there is no time to send, further apart is usually the end ' +
        'of the day. Visits whose phone clock is wrong are skipped. Online means ' +
        pct(m.online_share) +
        ' or more of a worker’s visits were sent promptly; some of the time means ' +
        pct(m.sometimes_share) +
        ' or more. Connect has recorded when visits arrive only since ' +
        m.reliable_from +
        ', so nothing earlier is judged.',
    );
    note(
      p5,
      backlog.length
        ? backlog.length +
            ' day' +
            (backlog.length === 1 ? '' : 's') +
            ' left out because forwarding to Connect was backed up (even the fastest tenth of ' +
            'visits took over ' +
            m.backlog_p10_minutes +
            ' minutes to arrive): ' +
            backlog
              .map(function (d) {
                return d.date + ' (' + Math.round(d.p10_minutes) + ' min)';
              })
              .join(', ') +
            '.'
        : 'No days were left out for a forwarding backlog (a day on which even the fastest tenth of ' +
            'visits took over ' +
            m.backlog_p10_minutes +
            ' minutes to arrive).',
    );
    root.appendChild(p5);
  }

  fetch(root.dataset.endpoint + location.search, { credentials: 'same-origin' })
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
