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

  /* ── what the map shows ─────────────────────────────────────────────
     A grid of tiles about 110 km across, each in one of three bands for the
     chosen measure: no problem, watch, or problem. Every tile with workers is
     drawn, so the map answers both halves of the question -- where
     connectivity is a problem AND where it is not -- and the bar above it
     counts the proportion of areas (and of workers) in each band.

     A tile's band depends only on the RATE inside it. It replaced a heatmap
     that summed counts through a blur, which lit up wherever busy areas sat
     close together (northern Nigeria) rather than where connectivity was bad.

     Each measure is oriented so that higher is worse (`rate`), and names the
     two cut-offs between bands (`watch`, `problem`). Tiles holding few workers
     are drawn faint: one worker's bad month should not read like a region. */
  var BAND = {
    ok: { label: 'No problem', colour: '#35b39d' },
    watch: { label: 'Watch', colour: '#feaf31' },
    problem: { label: 'Problem', colour: '#e44434' },
    none: { label: 'Not enough data', colour: '#4a4470' },
  };
  var BANDS = ['ok', 'watch', 'problem'];
  var NO_DATA = BAND.none.colour;
  var FAINT_BELOW_WORKERS = 10;
  function inv(v) {
    return v == null ? null : 1 - v;
  }
  var MEASURES = {
    connected: {
      label: 'Rarely or never online',
      of: 'of judged workers rarely or never online',
      rate: function (c) {
        return inv(c.connected_rate);
      },
      watch: 0.2,
      problem: 0.5,
      fmt: pct,
    },
    online: {
      label: 'Not online most of the time',
      of: 'of judged workers without steady signal',
      rate: function (c) {
        return inv(c.online_rate);
      },
      watch: 0.5,
      problem: 0.8,
      fmt: pct,
    },
    seen: {
      label: 'Never seen online',
      of: 'of workers never seen online',
      rate: function (c) {
        return inv(c.seen_online_rate);
      },
      watch: 0.05,
      problem: 0.2,
      fmt: pct,
    },
    delayed_1d: {
      label: 'Visits delayed over a day',
      of: 'of visits delayed over a day',
      rate: function (c) {
        return c.delayed_1d_rate;
      },
      watch: 0.05,
      problem: 0.2,
      fmt: pct,
    },
    delayed_3d: {
      label: 'Visits delayed over 3 days',
      of: 'of visits delayed over 3 days',
      rate: function (c) {
        return c.delayed_3d_rate;
      },
      watch: 0.02,
      problem: 0.1,
      fmt: pct,
    },
    delayed_7d: {
      label: 'Visits delayed over a week',
      of: 'of visits delayed over a week',
      rate: function (c) {
        return c.delayed_7d_rate;
      },
      watch: 0.01,
      problem: 0.05,
      fmt: pct,
    },
    median: {
      label: 'Typical delay',
      of: 'typical delay',
      rate: function (c) {
        return c.median_delay_minutes;
      },
      watch: 60,
      problem: 720,
      fmt: delay,
    },
  };
  function band(m, v) {
    if (v == null) return 'none';
    return v >= m.problem ? 'problem' : v >= m.watch ? 'watch' : 'ok';
  }
  // For a table cell or headline figure: the band colour for that value.
  function colourFor(m, v) {
    return BAND[band(m, v)].colour;
  }
  function measureLegend(m) {
    return (
      '<span><i style="background:' +
      BAND.ok.colour +
      '"></i>No problem &lt; ' +
      m.fmt(m.watch) +
      '</span><span><i style="background:' +
      BAND.watch.colour +
      '"></i>Watch ' +
      m.fmt(m.watch) +
      '–' +
      m.fmt(m.problem) +
      '</span><span><i style="background:' +
      BAND.problem.colour +
      '"></i>Problem ≥ ' +
      m.fmt(m.problem) +
      '</span>'
    );
  }

  /* ── how much of Connect is in each band ────────────────────────── */
  function bandCounts(tiles, m) {
    var out = {};
    BANDS.concat(['none']).forEach(function (b) {
      out[b] = { tiles: 0, workers: 0 };
    });
    tiles.forEach(function (t) {
      var b = band(m, m.rate(t));
      out[b].tiles += 1;
      out[b].workers += t.workers || 0;
    });
    return out;
  }
  function proportionBar(node, tiles, m) {
    var counts = bandCounts(tiles, m);
    var rows = [
      ['tiles', 'Areas'],
      ['workers', 'Workers in those areas'],
    ];
    node.innerHTML = '';
    rows.forEach(function (r) {
      var key = r[0];
      var total = BANDS.concat(['none']).reduce(function (a, b) {
        return a + counts[b][key];
      }, 0);
      var row = h('div', 'conn-prop');
      row.appendChild(h('div', 'conn-prop-lab', r[1]));
      var bar = h('div', 'conn-prop-bar');
      var parts = h('div', 'conn-prop-parts');
      BANDS.concat(['none']).forEach(function (b) {
        var n = counts[b][key];
        if (!n) return;
        var seg = h('i');
        seg.style.width = ((100 * n) / (total || 1)).toFixed(2) + '%';
        seg.style.background = BAND[b].colour;
        seg.title =
          BAND[b].label + ': ' + nf.format(n) + ' ' + r[1].toLowerCase();
        bar.appendChild(seg);
        var p = h('span', 'conn-prop-part');
        p.innerHTML =
          '<i style="background:' +
          BAND[b].colour +
          '"></i>' +
          BAND[b].label +
          ' <b>' +
          pct(n / (total || 1)) +
          '</b> · ' +
          nf.format(n);
        parts.appendChild(p);
      });
      row.appendChild(bar);
      row.appendChild(parts);
      node.appendChild(row);
    });
  }

  /* ── where: squares that stay a readable size at any zoom ─────────
     The server sends tiles at several sizes (largest first), each nesting
     exactly inside the one above. The page shows whichever size is closest to
     the on-screen size chosen with Small / Medium / Large, and swaps as you
     zoom: zoomed out the squares are large, and each splits into four as you
     zoom in.

     When a square splits, the parts with no workers are still drawn, as a
     faint outline, so a gap reads as "no workers here" rather than vanishing.
     Squares with workers fade in with how many they hold: one worker is one
     person's habit, ten or more is the area's network. */
  var SQUARE_PX = { small: 8, medium: 16, large: 28 };
  var EMPTY = 'empty';
  function km(deg) {
    return Math.round(deg * 111);
  }
  function squarePixels(deg, zoom) {
    return (deg * 512 * Math.pow(2, zoom)) / 360;
  }
  // The level whose squares are closest (on a log scale) to the target size.
  function levelFor(levels, zoom, px) {
    var best = 0,
      bestErr = Infinity;
    levels.forEach(function (lv, i) {
      var err = Math.abs(Math.log(squarePixels(lv.degrees, zoom) / px));
      if (err < bestErr) {
        best = i;
        bestErr = err;
      }
    });
    return best;
  }
  function square(lat, lon, size, props) {
    return {
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [lon, lat],
            [lon + size, lat],
            [lon + size, lat + size],
            [lon, lat + size],
            [lon, lat],
          ],
        ],
      },
      properties: props,
    };
  }
  function key2(lat, lon) {
    return lat.toFixed(2) + ',' + lon.toFixed(2);
  }
  // Squares with workers at level i, plus the empty parts of the squares one
  // level up, so splitting a square never makes part of it disappear.
  function levelFeatures(levels, i) {
    var lv = levels[i];
    var size = lv.degrees;
    var feats = lv.tiles.map(function (t) {
      var props = {};
      Object.keys(t).forEach(function (k) {
        if (t[k] != null) props[k] = t[k];
      });
      Object.keys(MEASURES).forEach(function (k) {
        props['b_' + k] = band(MEASURES[k], MEASURES[k].rate(t));
      });
      return square(t.lat, t.lon, size, props);
    });
    if (i > 0) {
      var have = {};
      lv.tiles.forEach(function (t) {
        have[key2(t.lat, t.lon)] = true;
      });
      var parent = levels[i - 1];
      var n = Math.round(parent.degrees / size);
      parent.tiles.forEach(function (p) {
        for (var a = 0; a < n; a++) {
          for (var b = 0; b < n; b++) {
            var lat = p.lat + a * size,
              lon = p.lon + b * size;
            if (have[key2(lat, lon)]) continue;
            var props = { workers: 0 };
            Object.keys(MEASURES).forEach(function (k) {
              props['b_' + k] = EMPTY;
            });
            feats.push(square(lat, lon, size, props));
          }
        }
      });
    }
    return { type: 'FeatureCollection', features: feats };
  }
  function popupHtml(t, m) {
    if (!t.workers) {
      return '<span class="net-pop-tier">No workers do most of their visits here.</span>';
    }
    var b = band(m, m.rate(t));
    return (
      '<b>' +
      nf.format(t.workers) +
      ' worker' +
      (t.workers === 1 ? '' : 's') +
      '</b> · <span style="color:' +
      BAND[b].colour +
      '">' +
      BAND[b].label +
      '</span><br>' +
      m.label +
      ': ' +
      m.fmt(m.rate(t)) +
      '<br><span class="net-pop-tier">' +
      CLASSES.map(function (k) {
        return LABELS[k] + ' ' + nf.format(t[k] || 0);
      }).join(' · ') +
      '<br>Seen online at least once ' +
      pct(t.seen_online_rate) +
      ' · typical delay ' +
      delay(t.median_delay_minutes) +
      '<br>Delayed over a day / 3 days / a week: ' +
      pct(t.delayed_1d_rate) +
      ' / ' +
      pct(t.delayed_3d_rate) +
      ' / ' +
      pct(t.delayed_7d_rate) +
      (t.workers < FAINT_BELOW_WORKERS
        ? '<br>Few workers: faint, so read it as an individual habit, not the area.'
        : '') +
      '</span>'
    );
  }
  function bandColourExpr(k) {
    return [
      'match',
      ['get', 'b_' + k],
      'ok',
      BAND.ok.colour,
      'watch',
      BAND.watch.colour,
      'problem',
      BAND.problem.colour,
      EMPTY,
      '#a9b3e8',
      NO_DATA,
    ];
  }
  // Empty squares are a ghost; one worker is faint; ten or more is full.
  var TILE_OPACITY = [
    'case',
    ['==', ['get', 'workers'], 0],
    0.06,
    [
      'interpolate',
      ['linear'],
      ['get', 'workers'],
      1,
      0.3,
      FAINT_BELOW_WORKERS,
      0.85,
    ],
  ];
  function liveMap(container, levels, key, scale, onLevel) {
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
    var ready = false;
    var state = { key: key, scale: scale, level: -1 };
    function refresh() {
      if (!ready || !levels.length) return;
      var i = levelFor(levels, map.getZoom(), SQUARE_PX[state.scale]);
      if (i === state.level) return;
      state.level = i;
      map.getSource('tiles').setData(levelFeatures(levels, i));
      onLevel(i);
    }
    map.on('load', function () {
      window.ConnectMap.calmBasemap(map, { text: 0.45 });
      map.addSource('tiles', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      map.addLayer({
        id: 'tiles',
        type: 'fill',
        source: 'tiles',
        paint: {
          'fill-color': bandColourExpr(state.key),
          'fill-opacity': TILE_OPACITY,
        },
      });
      map.addLayer({
        id: 'tiles-edge',
        type: 'line',
        source: 'tiles',
        paint: {
          'line-color': [
            'case',
            ['==', ['get', 'workers'], 0],
            '#a9b3e8',
            '#08042a',
          ],
          'line-width': 0.6,
          'line-opacity': ['case', ['==', ['get', 'workers'], 0], 0.25, 0.8],
        },
      });
      ready = true;
      if (levels.length && levels[0].tiles.length) {
        window.ConnectMap.fit(map, levelFeatures(levels, 0), 60);
      }
      refresh();
      map.on('zoomend', refresh);
      var popup = new window.mapboxgl.Popup({
        closeButton: false,
        offset: 6,
        className: 'net-pop',
      });
      map.on('mousemove', 'tiles', function (e) {
        map.getCanvas().style.cursor = 'pointer';
        popup
          .setLngLat(e.lngLat)
          .setHTML(popupHtml(e.features[0].properties, MEASURES[state.key]))
          .addTo(map);
      });
      map.on('mouseleave', 'tiles', function () {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });
    });
    return {
      set: function (k) {
        state.key = k;
        if (ready) {
          map.setPaintProperty('tiles', 'fill-color', bandColourExpr(k));
        }
      },
      scale: function (s) {
        state.scale = s;
        state.level = -1;
        refresh();
      },
    };
  }
  // No Mapbox token: a flat plot of one middle size, with the same bands.
  function flatMap(box, levels, key, scale, onLevel) {
    var i = Math.min(2, levels.length - 1);
    function draw(k) {
      var m = MEASURES[k];
      var tiles = i >= 0 ? levels[i].tiles : [];
      var size = i >= 0 ? levels[i].degrees : 1;
      var W = 1000,
        H = 520;
      var svg = el('svg', {
        viewBox: '0 0 ' + W + ' ' + H,
        role: 'img',
        'aria-label': 'Areas with workers, by ' + m.label,
      });
      if (tiles.length) {
        var la0 =
            Math.min.apply(
              null,
              tiles.map((t) => t.lat),
            ) - 1,
          la1 =
            Math.max.apply(
              null,
              tiles.map((t) => t.lat + size),
            ) + 1,
          lo0 =
            Math.min.apply(
              null,
              tiles.map((t) => t.lon),
            ) - 1,
          lo1 =
            Math.max.apply(
              null,
              tiles.map((t) => t.lon + size),
            ) + 1;
        var sx = W / (lo1 - lo0),
          sy = H / (la1 - la0);
        tiles.forEach(function (t) {
          svg.appendChild(
            titled(
              el('rect', {
                x: (t.lon - lo0) * sx,
                y: H - (t.lat + size - la0) * sy,
                width: Math.max(size * sx, 2),
                height: Math.max(size * sy, 2),
                fill: colourFor(m, m.rate(t)),
                opacity: Math.min(
                  0.85,
                  0.3 + (0.55 * (t.workers - 1)) / (FAINT_BELOW_WORKERS - 1),
                ),
              }),
              nf.format(t.workers) +
                ' workers · ' +
                m.label +
                ': ' +
                m.fmt(m.rate(t)),
            ),
          );
        });
      }
      box.innerHTML = '';
      box.appendChild(svg);
    }
    draw(key);
    if (i >= 0) onLevel(i);
    return { set: draw, scale: function () {} };
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
    q.delete('view');
    q.delete('squares');
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
    if (Array.from(params.keys()).some((k) => k !== 'colour' && k !== 'view')) {
      var clear = h('button', 'net-back conn-clear', 'Clear filters');
      clear.type = 'button';
      clear.addEventListener('click', function () {
        var keep = params.get('colour');
        var view = params.get('view');
        params = new URLSearchParams();
        if (keep) params.set('colour', keep);
        if (view) params.set('view', view);
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
    var levels = (data.map && data.map.levels) || [];
    var shown = 0;
    var chosen = MEASURES[params.get('colour')]
      ? params.get('colour')
      : 'delayed_3d';
    var scale = SQUARE_PX[params.get('squares')]
      ? params.get('squares')
      : 'medium';
    var p3 = panel('Where', measureLegend(MEASURES[chosen]));
    var bar3 = p3.querySelector('.net-panel-bar');
    var legendNode = p3.querySelector('.net-legend');
    var pick = h('label', 'conn-select conn-colour');
    pick.appendChild(h('span', '', 'Show'));
    var sel = document.createElement('select');
    sel.className = 'net-filter';
    Object.keys(MEASURES).forEach(function (k) {
      sel.appendChild(new Option(MEASURES[k].label, k));
    });
    sel.value = chosen;
    pick.appendChild(sel);
    bar3.insertBefore(pick, legendNode);
    var sizes = h('div', 'conn-modes');
    sizes.setAttribute('role', 'group');
    sizes.setAttribute('aria-label', 'Square size');
    [
      ['small', 'Small'],
      ['medium', 'Medium'],
      ['large', 'Large'],
    ].forEach(function (d) {
      var b = h('button', 'conn-mode', d[1]);
      b.type = 'button';
      b.dataset.scale = d[0];
      b.setAttribute('aria-pressed', String(d[0] === scale));
      sizes.appendChild(b);
    });
    bar3.insertBefore(sizes, legendNode);
    var sizeNote = h('span', 'conn-size-note', '');
    bar3.insertBefore(sizeNote, legendNode);
    var props = h('div', 'conn-props');
    p3.appendChild(props);
    var canMap = window.ConnectMap && window.mapboxgl && window.MAPBOX_TOKEN;
    var box = h('div', canMap ? 'net-globe' : 'net-chartbox');
    p3.appendChild(box);
    note(
      p3,
      'Each square holds the workers who do most of their visits there, and its colour depends only on ' +
        'what happens inside it — the share of its workers or visits with the problem you chose — so a ' +
        'busy area and a quiet one are judged the same way, and every area with workers is shown, problem ' +
        'or not. Squares keep roughly the size you pick on screen: zoom in and each splits into four ' +
        'smaller ones (down to about 11 km). Squares with one worker are faint and reach full colour at ' +
        FAINT_BELOW_WORKERS +
        ' or more; parts of a split square with no workers are a faint outline. The "Areas" bar counts ' +
        'squares at the size on screen, so it changes as you zoom; the "Workers" bar does not. Hover a ' +
        'square for every measure.',
    );
    root.appendChild(p3);
    function onLevel(i) {
      shown = i;
      sizeNote.textContent =
        '≈ ' + km(levels[i].degrees) + ' km squares at this zoom';
      proportionBar(props, levels[i].tiles, MEASURES[chosen]);
    }
    var painter = canMap
      ? liveMap(box, levels, chosen, scale, onLevel)
      : flatMap(box, levels, chosen, scale, onLevel);
    function remember() {
      params.set('colour', chosen);
      if (scale === 'medium') params.delete('squares');
      else params.set('squares', scale);
      params.delete('view');
      history.replaceState(null, '', '?' + params.toString());
    }
    sel.addEventListener('change', function () {
      chosen = sel.value;
      painter.set(chosen);
      legendNode.innerHTML = measureLegend(MEASURES[chosen]);
      if (levels[shown]) {
        proportionBar(props, levels[shown].tiles, MEASURES[chosen]);
      }
      remember();
    });
    sizes.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      scale = b.dataset.scale;
      sizes.querySelectorAll('button').forEach(function (x) {
        x.setAttribute('aria-pressed', String(x.dataset.scale === scale));
      });
      painter.scale(scale);
      remember();
    });
    if (!canMap) sizes.hidden = true;

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
