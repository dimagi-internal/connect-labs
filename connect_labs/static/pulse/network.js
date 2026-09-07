/* The partner network: growth over time, and where the partners are.
 *
 * Two drawings of one dataset. The chart answers "how fast is the network
 * growing, and how much of it is actually working"; the map answers "where is
 * it". Both are plain SVG built here rather than a charting library or a tile
 * basemap -- pulse/geo.py already made that call for the printed maps, and the
 * reasons hold harder on a page that may be opened by a funder on a slow link:
 * no token, no external request, crisp at any size.
 */
(function () {
  'use strict';

  var root = document.getElementById('net');
  if (!root) return;

  var PRECISION_COPY = {
    city: 'town matched in the office address',
    region: 'region of operation',
    country: 'country only',
  };

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
  function text(tag, attrs, content) {
    var node = el(tag, attrs);
    node.textContent = content;
    return node;
  }
  function monthIndex(m) {
    return parseInt(m.slice(0, 4), 10) * 12 + parseInt(m.slice(5, 7), 10);
  }

  /* ---- the chart: two cumulative lines, stepped, because a partner joins on a
     day rather than easing in over the month. */
  function chart(series) {
    var W = 1000,
      H = 320,
      PL = 56,
      PR = 122,
      PT = 24,
      PB = 34;
    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      role: 'img',
      'aria-label':
        'Organisations in the network and organisations delivering, by month',
    });
    if (!series.length) return svg;

    var lo = monthIndex(series[0].m),
      hi = monthIndex(series[series.length - 1].m);
    var span = Math.max(1, hi - lo);
    var top = series[series.length - 1].network;
    var step = top > 150 ? 50 : 25;
    var max = Math.ceil(top / step) * step;
    var x = function (m) {
      return PL + ((monthIndex(m) - lo) / span) * (W - PL - PR);
    };
    var y = function (v) {
      return H - PB - (v / max) * (H - PT - PB);
    };

    for (var v = 0; v <= max; v += step) {
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
        text(
          'text',
          { x: PL - 10, y: y(v) + 4, class: 'net-ax net-end' },
          String(v),
        ),
      );
    }

    function build(key) {
      var d = 'M' + x(series[0].m) + ',' + y(series[0][key]);
      for (var i = 1; i < series.length; i++) {
        d += ' L' + x(series[i].m) + ',' + y(series[i - 1][key]);
        d += ' L' + x(series[i].m) + ',' + y(series[i][key]);
      }
      return d;
    }
    function close(d) {
      return (
        d +
        ' L' +
        x(series[series.length - 1].m) +
        ',' +
        y(0) +
        ' L' +
        x(series[0].m) +
        ',' +
        y(0) +
        ' Z'
      );
    }
    var net = build('network'),
      act = build('delivering');
    svg.appendChild(el('path', { d: close(net), class: 'net-area-all' }));
    svg.appendChild(el('path', { d: net, class: 'net-line-all' }));
    svg.appendChild(el('path', { d: close(act), class: 'net-area-live' }));
    svg.appendChild(el('path', { d: act, class: 'net-line-live' }));

    // Label the ends rather than every point: the two numbers a reader wants
    // are "how many now" and "how many of those are working".
    var last = series[series.length - 1];
    svg.appendChild(
      text(
        'text',
        {
          x: x(last.m) + 8,
          y: y(last.network) + 4,
          class: 'net-endlab net-all',
        },
        last.network + ' in network',
      ),
    );
    svg.appendChild(
      text(
        'text',
        {
          x: x(last.m) + 8,
          y: y(last.delivering) + 4,
          class: 'net-endlab net-live',
        },
        last.delivering + ' delivering',
      ),
    );

    var seen = {};
    series.forEach(function (s) {
      var year = s.m.slice(0, 4);
      if (seen[year]) return;
      seen[year] = 1;
      svg.appendChild(
        text('text', { x: x(s.m), y: H - 10, class: 'net-ax net-mid' }, year),
      );
    });
    return svg;
  }

  /* ---- the map: equirectangular, fitted to the points themselves.
     No basemap. The network draws its own geography, which is also the only
     honest option when a third of the points are country centroids. */
  function map(points) {
    var W = 1000,
      M = 30;
    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      role: 'img',
      'aria-label': 'Partner organisation locations',
    });
    if (!points.length) return svg;

    // Fit to where the partners actually are, not to the extremes. Nearly all
    // of them are in one region; a single partner on another continent doubles
    // the bounding box and shrinks everyone else to a smudge. So the frame
    // takes the bulk, and anything outside is pinned to the edge and labelled
    // as such -- dropping it would be the dishonest fix.
    function span(values) {
      var sorted = values.slice().sort(function (a, b) {
        return a - b;
      });
      var at = function (q) {
        return sorted[
          Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))
        ];
      };
      return [at(0.02), at(0.98)];
    }
    var latSpan = span(
      points.map(function (p) {
        return p.lat;
      }),
    );
    var lonSpan = span(
      points.map(function (p) {
        return p.lon;
      }),
    );
    var la0 = latSpan[0] - 4,
      la1 = latSpan[1] + 4;
    var lo0 = lonSpan[0] - 4,
      lo1 = lonSpan[1] + 4;
    // Keep degrees square so countries are not stretched into unrecognisable shapes.
    // Degrees stay square so no country is stretched out of shape, and the
    // canvas takes its height from that fit -- a fixed height leaves empty
    // bands above and below the continent nearly every partner is on.
    var scale = (W - 2 * M) / (lo1 - lo0);
    var H = Math.round((la1 - la0) * scale) + 2 * M;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    var cx = (lo0 + lo1) / 2,
      cy = (la0 + la1) / 2;
    var X = function (lon) {
      return W / 2 + (lon - cx) * scale;
    };
    var Y = function (lat) {
      return H / 2 - (lat - cy) * scale;
    };

    for (var g = -180; g <= 180; g += 10) {
      if (g > lo0 && g < lo1)
        svg.appendChild(
          el('line', {
            x1: X(g),
            y1: M,
            x2: X(g),
            y2: H - M,
            class: 'net-grat',
          }),
        );
      if (g > la0 && g < la1)
        svg.appendChild(
          el('line', {
            x1: M,
            y1: Y(g),
            x2: W - M,
            y2: Y(g),
            class: 'net-grat',
          }),
        );
    }

    // Country-only points cluster exactly on one another; spread them on a
    // small ring so a country with 90 partners reads as ninety, not as one.
    var atCountry = {};
    points.forEach(function (p) {
      if (p.precision === 'country') {
        atCountry[p.iso3] = (atCountry[p.iso3] || 0) + 1;
      }
    });
    var placed = {};
    var offCount = 0;
    points
      .slice()
      .sort(function (a, b) {
        return a.precision === 'country' ? -1 : 1;
      })
      .forEach(function (p) {
        var px = X(p.lon),
          py = Y(p.lat);
        var offView = px < M || px > W - M || py < M || py > H - M;
        if (offView) {
          px = Math.max(M, Math.min(W - M, px));
          py = Math.max(M, Math.min(H - M, py));
          offCount++;
        }
        if (p.precision === 'country' && atCountry[p.iso3] > 1) {
          var i = (placed[p.iso3] = placed[p.iso3] || 0);
          placed[p.iso3]++;
          var ring = 7 + Math.floor(i / 10) * 7;
          var angle = (i % 10) * ((Math.PI * 2) / 10);
          px += Math.cos(angle) * ring;
          py += Math.sin(angle) * ring;
        }
        var cls =
          'net-dot net-dot-' +
          p.precision +
          (p.delivering ? ' net-dot-live' : '') +
          (offView ? ' net-dot-off' : '');
        var dot = el('circle', {
          cx: px.toFixed(1),
          cy: py.toFixed(1),
          r: p.delivering ? 4.6 : 3.4,
          class: cls,
        });
        var title = el('title');
        title.textContent =
          (offView ? 'outside this view — ' : '') +
          (p.short || p.name) +
          ' — ' +
          (p.place || 'location unknown') +
          ' (' +
          (PRECISION_COPY[p.precision] || p.precision) +
          ')' +
          (p.delivering
            ? ' · delivering since ' + p.since
            : ' · not yet delivering');
        dot.appendChild(title);
        svg.appendChild(dot);
      });

    // Name the countries carrying enough partners to be worth finding. Without
    // this the map is dots in space: the shapes are recognisable to someone who
    // knows the region and to nobody else.
    var byCountry = {};
    points.forEach(function (p) {
      var c =
        byCountry[p.iso3] ||
        (byCountry[p.iso3] = { n: 0, lat: 0, lon: 0, name: p.country });
      c.n++;
      c.lat += p.lat;
      c.lon += p.lon;
    });
    Object.keys(byCountry).forEach(function (iso) {
      var c = byCountry[iso];
      if (c.n < 4 || !c.name) return;
      var lx = X(c.lon / c.n),
        ly = Y(c.lat / c.n);
      if (lx < M || lx > W - M || ly < M || ly > H - M) return;
      svg.appendChild(
        text(
          'text',
          { x: lx.toFixed(1), y: (ly - 15).toFixed(1), class: 'net-mlab' },
          // ISO names carry a formal tail -- "Congo, the Democratic Republic
          // of the" -- that is longer than the country it labels.
          c.name.split(',')[0] + ' · ' + c.n,
        ),
      );
    });
    return svg;
  }

  function kpi(n, label) {
    var d = document.createElement('div');
    d.className = 'net-kpi';
    d.innerHTML = '<div class="net-kpi-n"></div><div class="net-kpi-l"></div>';
    d.querySelector('.net-kpi-n').textContent = n;
    d.querySelector('.net-kpi-l').textContent = label;
    return d;
  }

  function panel(title, legend) {
    var p = document.createElement('section');
    p.className = 'net-panel';
    var bar = document.createElement('div');
    bar.className = 'net-panel-bar';
    var h = document.createElement('h2');
    h.textContent = title;
    bar.appendChild(h);
    if (legend) {
      var l = document.createElement('div');
      l.className = 'net-legend';
      l.innerHTML = legend;
      bar.appendChild(l);
    }
    p.appendChild(bar);
    return p;
  }

  function render(data) {
    root.innerHTML = '';
    var t = data.totals;

    var kpis = document.createElement('div');
    kpis.className = 'net-kpis';
    kpis.appendChild(kpi(t.partners, 'partner organisations'));
    kpis.appendChild(kpi(t.delivering, 'have delivered'));
    kpis.appendChild(kpi(t.countries, 'countries'));
    kpis.appendChild(
      kpi(
        t.partners ? Math.round((t.delivering / t.partners) * 100) + '%' : '—',
        'have activated',
      ),
    );
    root.appendChild(kpis);

    var growth = panel(
      'Growth of the network',
      '<span><i class="sw-all"></i>in network</span><span><i class="sw-live"></i>delivering</span>',
    );
    var cbox = document.createElement('div');
    cbox.className = 'net-chartbox';
    cbox.appendChild(chart(data.series));
    growth.appendChild(cbox);
    var gnote = document.createElement('p');
    gnote.className = 'net-note';
    gnote.textContent =
      'Joining is the date a partner answered an EOI, from the LLO Directory — ' +
      t.with_join_date +
      ' of ' +
      t.partners +
      ' have one. Delivering is their first verified service on Connect.';
    growth.appendChild(gnote);
    root.appendChild(growth);

    var pr = data.precision || {};
    var geo = panel(
      'Where the partners are',
      '<span><i class="sw-city"></i>town</span><span><i class="sw-region"></i>region</span>' +
        '<span><i class="sw-country"></i>country only</span><span><i class="sw-live"></i>delivering</span>',
    );
    var mbox = document.createElement('div');
    mbox.className = 'net-chartbox';
    mbox.appendChild(map(data.points));
    geo.appendChild(mbox);
    var note = document.createElement('p');
    note.className = 'net-note';
    note.textContent =
      'Located as precisely as each record allows: ' +
      (pr.city || 0) +
      ' to a town named in the office address, ' +
      (pr.region || 0) +
      ' to a region of operation, ' +
      (pr.country || 0) +
      ' to the country only — drawn as a loose ring, not a pin, because that is all we know. ' +
      (t.partners - t.located) +
      ' could not be placed.';
    geo.appendChild(note);
    root.appendChild(geo);
  }

  fetch(root.dataset.endpoint, { credentials: 'same-origin' })
    .then(function (r) {
      if (r.status === 403)
        throw new Error(
          'This view names partner organisations, so it needs a labs session.',
        );
      if (!r.ok)
        throw new Error('The network endpoint returned ' + r.status + '.');
      return r.json();
    })
    .then(function (data) {
      if (data.empty_reason) {
        document.getElementById('net-loading').textContent = data.empty_reason;
        return;
      }
      render(data);
    })
    .catch(function (err) {
      var box = document.getElementById('net-loading');
      if (box) box.textContent = err.message;
    });
})();
