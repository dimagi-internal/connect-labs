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

  // Set once the payload lands; the country detail rows read partners from it.
  var ALL_POINTS = [];
  var PULSE_ORG_URL = root.dataset.orgUrl || '';

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
  /* Fallback plot, used only when there is no Mapbox token: an equirectangular
     scatter with no basemap. Better than an empty box, worse than the globe. */
  function flatMap(points) {
    var W = 1000,
      M = 30;
    if (!points.length) {
      return el('svg', {
        viewBox: '0 0 ' + W + ' 200',
        role: 'img',
        'aria-label': 'No partner locations to draw',
      });
    }

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
    // Built here rather than above: the height comes out of the fit, and an
    // element created before it is known carries viewBox="0 0 1000 undefined"
    // until it is corrected -- which the browser reports as an error even
    // though the drawing ends up right.
    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      role: 'img',
      'aria-label': 'Partner organisation locations',
    });
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
          'net-dot' +
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

  /* The globe. Same stack as the wall display -- Mapbox GL through the shared
   * ConnectMap helper -- so the network reads as part of Pulse rather than a
   * different product that happens to plot dots.
   *
   * Precision survives the move to a real basemap, which is the whole reason
   * the payload carries it: a partner located to a town is a filled point, and
   * one known only to its country is a hollow ring, because the middle of that
   * country is exactly what we do not know.
   */
  function globeMap(container, points) {
    var map = window.ConnectMap.createMap(container, {
      center: [22, 4],
      zoom: 1.7,
      projection: 'globe',
      interactive: true,
    });
    map.addControl(
      new window.mapboxgl.NavigationControl({ showCompass: false }),
      'top-right',
    );
    map.scrollZoom.disable(); // a wheel over the page should scroll the page
    // Mapbox reports style, source and expression failures through this event
    // rather than by throwing, so without it a broken layer is a blank map and
    // no explanation.
    map.on('error', function (e) {
      console.error(
        '[pulse:network] map error:',
        (e && e.error && e.error.message) || e,
      );
    });

    var features = points.map(function (p) {
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
        properties: {
          name: p.short || p.name,
          full: p.name,
          place: p.place || 'location unknown',
          country: p.country || '',
          delivering: !!p.delivering,
          since: p.since || '',
          joined: p.joined || '',
        },
      };
    });

    map.on('load', function () {
      window.ConnectMap.calmBasemap(map, { text: 0.45 });
      map.setFog({
        color: '#100a3d',
        'high-color': '#16006d',
        'horizon-blend': 0.06,
        'space-color': '#08042a',
        'star-intensity': 0.08,
      });
      map.addSource('partners', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: features },
      });

      var LIGHT = '#feaf31';
      var QUIET = '#a9b3e8';
      var colour = ['case', ['get', 'delivering'], LIGHT, QUIET];

      map.addLayer({
        id: 'partners-glow',
        type: 'circle',
        source: 'partners',
        filter: ['get', 'delivering'],
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 7, 6, 20],
          'circle-color': LIGHT,
          'circle-opacity': 0.14,
          'circle-blur': 0.9,
        },
      });
      map.addLayer({
        id: 'partners',
        type: 'circle',
        source: 'partners',
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            1,
            3.6,
            4,
            6,
            8,
            11,
          ],
          'circle-color': colour,
          'circle-opacity': 0.85,
          'circle-stroke-color': colour,
          'circle-stroke-width': 0.8,
          'circle-stroke-opacity': 0.9,
        },
      });

      var popup = new window.mapboxgl.Popup({
        closeButton: false,
        offset: 10,
        className: 'net-pop',
      });
      map.on('mouseenter', 'partners', function (e) {
        map.getCanvas().style.cursor = 'pointer';
        var f = e.features[0].properties;
        popup
          .setLngLat(e.features[0].geometry.coordinates.slice())
          .setHTML(
            '<b>' +
              f.full +
              '</b><br>' +
              f.place +
              (f.country ? ', ' + f.country : '') +
              '<br>' +
              (f.delivering === true || f.delivering === 'true'
                ? 'delivering since ' + f.since
                : 'not yet delivering') +
              (f.joined ? '<br>joined ' + f.joined : ''),
          )
          .addTo(map);
      });
      map.on('mouseleave', 'partners', function () {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });
    });
    return map;
  }

  /* The country table. Two facts sit side by side in it that are easy to
   * conflate: where partners are headquartered, and where work actually
   * happens. The network reaches far more countries than delivery has, and
   * that gap is the same one the chart draws — this is its geography.
   *
   * Filterable because 21 rows is past the point where scanning for one
   * country beats typing its name, and sortable because "who delivers most"
   * and "where is the biggest bench" are different questions of the same rows.
   */

  /* The partners inside one country, opened from its row.
   *
   * This is where the table stops being a summary and becomes a way in: a
   * country is 93 organisations or it is one, and "who are they" is the next
   * question every time. Each partner Connect has a workspace for links
   * through to its own Pulse view; the ones without a workspace have never
   * delivered, so there is nothing there to look at yet and no link.
   */

  /* This page opens Pulse's own partner window. It declares the two things
   * that module asks for — a URL builder and the label tables — and holds no
   * store, no polling clock and no filter state, none of which a network view
   * has any use for.
   */
  function configureWindows(labels) {
    if (!window.PulseWindows) return;
    var base = (root.dataset.endpoint || '').replace(/\/api\/network\/$/, '');
    window.PulseWindows.configure({
      urlFor: function (path, params) {
        var q = new URLSearchParams(params || {}).toString();
        return base + path + (q ? '?' + q : '');
      },
      labels: function () {
        return labels || {};
      },
    });
  }

  function openPartnerWindow(slug) {
    if (!window.PulseWindows || !window.PulseCards) return false;
    window.PulseWindows.openPartner(slug);
    return true;
  }

  function partnersRow(country, span) {
    var tr = document.createElement('tr');
    tr.className = 'net-detail-row';
    var td = document.createElement('td');
    td.colSpan = span;
    td.className = 'net-detail';

    var here = ALL_POINTS.filter(function (p) {
      return p.iso3 === country.iso3;
    }).sort(function (a, b) {
      // Delivering first, then most recently joined: the ones doing the work,
      // then the ones who might next.
      if (a.delivering !== b.delivering) return a.delivering ? -1 : 1;
      return (b.joined || '').localeCompare(a.joined || '');
    });

    if (!here.length) {
      td.textContent =
        'No partner is headquartered in ' +
        country.name +
        ' — the delivery here is run by a partner based elsewhere.';
      tr.appendChild(td);
      return tr;
    }

    var list = document.createElement('table');
    list.className = 'net-orgtable';
    var body = document.createElement('tbody');
    list.appendChild(body);

    here.forEach(function (p) {
      var row = document.createElement('tr');
      row.className = 'net-org' + (p.delivering ? ' net-org-live' : '');

      var name = document.createElement('td');
      name.className = 'net-org-name';
      name.textContent = p.name;
      row.appendChild(name);

      var where = document.createElement('td');
      where.className = 'net-org-where';
      where.textContent = p.place || '';
      row.appendChild(where);

      var meta = document.createElement('td');
      meta.className = 'net-org-meta';
      meta.textContent = p.delivering
        ? 'delivering since ' + p.since
        : p.joined
        ? 'joined ' + p.joined
        : '';
      row.appendChild(meta);

      var go = document.createElement('td');
      go.className = 'net-org-go';
      if (p.slug) {
        // Opens Pulse's own partner window over this page. Only partners with a
        // Connect workspace have one to open — the rest have never delivered.
        row.classList.add('net-org-open');
        row.tabIndex = 0;
        go.textContent = 'open ›';
        var open = function (e) {
          e.preventDefault();
          if (!openPartnerWindow(p.slug) && PULSE_ORG_URL) {
            window.location.href = PULSE_ORG_URL.replace(
              '__SLUG__',
              encodeURIComponent(p.slug),
            );
          }
        };
        row.addEventListener('click', open);
        row.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') open(e);
        });
      }
      row.appendChild(go);
      body.appendChild(row);
    });
    td.appendChild(list);
    tr.appendChild(td);
    return tr;
  }

  function countryTable(rows, onPick) {
    var wrap = document.createElement('div');
    wrap.className = 'net-tablewrap';

    var sort = { key: 'services', dir: -1 };
    var query = '';
    var top = rows.reduce(function (m, r) {
      return Math.max(m, r.services);
    }, 0);

    var table = document.createElement('table');
    table.className = 'net-table';
    var thead = document.createElement('thead');
    var tbody = document.createElement('tbody');
    table.appendChild(thead);
    table.appendChild(tbody);

    var COLS = [
      { key: 'name', label: 'Country', align: 'left' },
      { key: 'partners', label: 'In network', align: 'right' },
      { key: 'delivering', label: 'Delivering', align: 'right' },
      { key: 'services', label: 'Services delivered', align: 'right' },
    ];

    function head() {
      thead.innerHTML = '';
      var tr = document.createElement('tr');
      COLS.forEach(function (c) {
        var th = document.createElement('th');
        th.textContent = c.label;
        th.className =
          'net-th net-' + c.align + (sort.key === c.key ? ' net-sorted' : '');
        if (sort.key === c.key) th.textContent += sort.dir < 0 ? ' ↓' : ' ↑';
        th.tabIndex = 0;
        function pick() {
          if (sort.key === c.key) sort.dir = -sort.dir;
          else {
            sort.key = c.key;
            sort.dir = c.key === 'name' ? 1 : -1;
          }
          draw();
        }
        th.addEventListener('click', pick);
        th.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            pick();
          }
        });
        tr.appendChild(th);
      });
      thead.appendChild(tr);
    }

    function draw() {
      head();
      tbody.innerHTML = '';
      var shown = rows
        .filter(function (r) {
          return !query || r.name.toLowerCase().indexOf(query) !== -1;
        })
        .slice()
        .sort(function (a, b) {
          var x = a[sort.key],
            y = b[sort.key];
          if (typeof x === 'string') return x.localeCompare(y) * sort.dir;
          return (x - y) * sort.dir;
        });

      shown.forEach(function (r) {
        var tr = document.createElement('tr');
        tr.className = 'net-tr' + (r.services ? '' : ' net-tr-quiet');
        tr.tabIndex = 0;

        var name = document.createElement('td');
        name.className = 'net-td';
        name.textContent = r.name;
        tr.appendChild(name);

        [r.partners, r.delivering].forEach(function (v, i) {
          var td = document.createElement('td');
          td.className =
            'net-td net-right' + (i === 1 && v ? ' net-live-num' : '');
          td.textContent = v || '—';
          tr.appendChild(td);
        });

        var svc = document.createElement('td');
        svc.className = 'net-td net-services';
        var num = document.createElement('span');
        num.className = 'net-barnum';
        num.textContent = r.services ? r.services.toLocaleString() : 'not yet';
        svc.appendChild(num);
        // The bar lives in a fixed track of its own. Sizing it as a percentage
        // of the cell put the largest country's bar across the full width,
        // where it read as a stray rule through the neighbouring column.
        var track = document.createElement('span');
        track.className = 'net-track';
        var bar = document.createElement('span');
        bar.className = 'net-bar';
        bar.style.width =
          top && r.services ? Math.max(2, (r.services / top) * 100) + '%' : '0';
        track.appendChild(bar);
        svc.appendChild(track);
        tr.appendChild(svc);

        var open = false;
        var detail = null;
        function toggle() {
          if (open) {
            if (detail) detail.parentNode.removeChild(detail);
            detail = null;
            open = false;
            tr.classList.remove('net-tr-open');
            return;
          }
          detail = partnersRow(r, COLS.length);
          tr.parentNode.insertBefore(detail, tr.nextSibling);
          open = true;
          tr.classList.add('net-tr-open');
          if (onPick) onPick(r);
        }
        tr.addEventListener('click', toggle);
        tr.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggle();
          }
        });
        tbody.appendChild(tr);
      });

      if (!shown.length) {
        var tr = document.createElement('tr');
        var td = document.createElement('td');
        td.colSpan = COLS.length;
        td.className = 'net-td net-empty';
        td.textContent = 'No country matches “' + query + '”.';
        tr.appendChild(td);
        tbody.appendChild(tr);
      }
    }

    var filter = document.createElement('input');
    filter.type = 'search';
    filter.className = 'net-filter';
    filter.placeholder = 'Filter countries…';
    filter.setAttribute('aria-label', 'Filter countries');
    filter.addEventListener('input', function () {
      query = filter.value.trim().toLowerCase();
      draw();
    });

    draw();
    wrap.appendChild(table);
    return { node: wrap, filter: filter };
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
    ALL_POINTS = data.points || [];
    configureWindows(data.labels);
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
      t.dated_from_eoi +
      ' of ' +
      t.partners +
      ' are dated that way. The rest are shown as joining when we first saw them. ' +
      'Delivering is their first verified service on Connect.';
    growth.appendChild(gnote);
    root.appendChild(growth);

    var geo = panel(
      'Where the partners are',
      '<span><i class="sw-all"></i>in network</span><span><i class="sw-live"></i>delivering</span>',
    );
    var mbox = document.createElement('div');
    var canUseGlobe =
      window.ConnectMap && window.mapboxgl && window.MAPBOX_TOKEN;
    if (canUseGlobe) {
      mbox.className = 'net-globe';
      geo.appendChild(mbox);
    } else {
      mbox.className = 'net-chartbox';
      mbox.appendChild(flatMap(data.points));
      geo.appendChild(mbox);
    }
    var note = document.createElement('p');
    note.className = 'net-note';
    note.textContent =
      t.located +
      ' of ' +
      t.partners +
      ' partner organisations placed. The rest appear as we collect their coordinates.';
    geo.appendChild(note);
    root.appendChild(geo);
    // After the panel is in the document: Mapbox measures its container.
    var liveMap = canUseGlobe ? globeMap(mbox, data.points) : null;

    // Countries last: the chart says how fast, the map says where, and this
    // says which — the level someone actually asks a follow-up question at.
    var geoPanel = panel('Countries', '');
    var built = countryTable(data.countries || [], function (row) {
      if (!liveMap) return;
      var here = data.points.filter(function (p) {
        return p.iso3 === row.iso3;
      });
      if (!here.length) return;
      var lat =
        here.reduce(function (a, p) {
          return a + p.lat;
        }, 0) / here.length;
      var lon =
        here.reduce(function (a, p) {
          return a + p.lon;
        }, 0) / here.length;
      liveMap.flyTo({ center: [lon, lat], zoom: 4.6, duration: 1400 });
      mbox.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    geoPanel.querySelector('.net-panel-bar').appendChild(built.filter);
    geoPanel.appendChild(built.node);
    var cnote = document.createElement('p');
    cnote.className = 'net-note';
    cnote.textContent =
      'Partners are headquartered in ' +
      t.countries +
      ' countries; Connect delivery has started in ' +
      t.countries_delivering +
      '. A country can appear with no partner headquartered in it — that is a partner working across a border.';
    geoPanel.appendChild(cnote);
    root.appendChild(geoPanel);
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
