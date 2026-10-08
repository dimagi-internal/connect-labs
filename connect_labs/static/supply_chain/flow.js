/*
 * flow.js -- draws "Where it went" (templates/supply_chain/flow.html).
 *
 * The page embeds the `stock_flow` payload: places (`nodes`, each in a
 * column: arrivals, stores by depth, workers, then the ends) and routes
 * (`links`, each carrying the running total moved along it at the end of
 * every week). `layout` turns one week into boxes and ribbons; the rest
 * draws them, and the slider / Play step through the weeks without asking
 * the server again.
 *
 * One scale for every week -- the biggest column at its biggest -- so the
 * picture fills up as the weeks play instead of every frame being stretched
 * to full height. A place's bar is what reached it; the part it sent on is
 * solid and the part it still holds is hatched. Workers are ordered by the
 * store that restocks them, then by name: nothing here ranks anyone.
 */
(function (root) {
  'use strict';

  var BAR = 14;
  var GAP = 14;
  var WORKER_GAP = 2;
  var PAD = 8;
  var TOP = 26; // room for the column headings

  function valueAt(link, week) {
    var v = link.series[Math.min(week, link.series.length - 1)] || 0;
    return v > 0 ? v : 0;
  }

  // In, out and size of every node for one week.
  function throughput(data, week) {
    var by = {};
    data.nodes.forEach(function (n) {
      by[n.id] = { node: n, inn: 0, out: 0 };
    });
    data.links.forEach(function (l) {
      var v = valueAt(l, week);
      if (by[l.source]) by[l.source].out += v;
      if (by[l.target]) by[l.target].inn += v;
    });
    Object.keys(by).forEach(function (id) {
      var t = by[id];
      var kind = t.node.kind;
      t.size =
        kind === 'source'
          ? t.out
          : kind === 'sink'
            ? t.inn
            : Math.max(t.inn, t.out);
      t.held =
        kind === 'source' || kind === 'sink' ? 0 : Math.max(t.inn - t.out, 0);
    });
    return by;
  }

  function columns(data) {
    var cols = {};
    data.nodes.forEach(function (n) {
      (cols[n.column] = cols[n.column] || []).push(n);
    });
    return Object.keys(cols)
      .map(Number)
      .sort(function (a, b) {
        return a - b;
      })
      .map(function (c) {
        return cols[c];
      });
  }

  var SINK_ORDER = { given: 0, lost: 1, elsewhere: 2 };

  // Order inside each column: arrivals and stores by name, workers by their
  // store's place then name, the ends in a fixed order.
  function ordered(data) {
    var cols = columns(data);
    var rank = {};
    cols.forEach(function (col) {
      col.sort(function (a, b) {
        if (a.kind === 'sink') return SINK_ORDER[a.id] - SINK_ORDER[b.id];
        if (a.kind === 'worker') {
          var pa = rank[a.parent] === undefined ? 1e9 : rank[a.parent];
          var pb = rank[b.parent] === undefined ? 1e9 : rank[b.parent];
          if (pa !== pb) return pa - pb;
        } else if (a.kind === 'store') {
          var qa = rank[a.parent] === undefined ? -1 : rank[a.parent];
          var qb = rank[b.parent] === undefined ? -1 : rank[b.parent];
          if (qa !== qb) return qa - qb;
        }
        return a.name.localeCompare(b.name);
      });
      col.forEach(function (n, i) {
        rank[n.id] = i;
      });
    });
    return cols;
  }

  function gapOf(col) {
    return col.length && col[0].kind === 'worker' ? WORKER_GAP : GAP;
  }

  // The scale every week shares: the tallest column, at its tallest week.
  function scaleFor(data, cols, height) {
    var k = Infinity;
    for (var w = 0; w < data.weeks.length; w++) {
      var t = throughput(data, w);
      cols.forEach(function (col) {
        var total = 0;
        col.forEach(function (n) {
          total += t[n.id].size;
        });
        var room =
          height - TOP - PAD - gapOf(col) * Math.max(col.length - 1, 0);
        if (total > 0 && room > 0) k = Math.min(k, room / total);
      });
    }
    return k === Infinity ? 0 : k;
  }

  // How tall the drawing needs to be for every worker to get a sliver.
  function heightFor(cols) {
    var most = 0;
    cols.forEach(function (col) {
      most = Math.max(most, col.length * (gapOf(col) + 6));
    });
    return Math.max(520, most + TOP + PAD);
  }

  function layout(data, week, opts) {
    var cols = (opts && opts.cols) || ordered(data);
    var width = (opts && opts.width) || 960;
    var height = (opts && opts.height) || heightFor(cols);
    var k =
      opts && opts.k !== undefined ? opts.k : scaleFor(data, cols, height);
    var t = throughput(data, week);
    var span =
      cols.length > 1 ? (width - 2 * 170 - BAR) / (cols.length - 1) : 0;
    var boxes = {};
    cols.forEach(function (col, ci) {
      var y = TOP;
      var x = 170 + ci * span;
      col.forEach(function (n) {
        var h = t[n.id].size * k;
        boxes[n.id] = {
          node: n,
          col: ci,
          x: x,
          y: y,
          h: h,
          inn: t[n.id].inn,
          out: t[n.id].out,
          held: t[n.id].held,
          heldH: t[n.id].held * k,
        };
        y += h + gapOf(col);
      });
    });
    // Ribbons leave a box top-down in the order of where they go, and arrive
    // top-down in the order of where they came from, so none cross inside a box.
    var outs = {};
    var ins = {};
    var ribbons = [];
    data.links.forEach(function (l) {
      var v = valueAt(l, week);
      if (!v || !boxes[l.source] || !boxes[l.target]) return;
      var r = {
        link: l,
        value: v,
        h: v * k,
        from: boxes[l.source],
        to: boxes[l.target],
      };
      (outs[l.source] = outs[l.source] || []).push(r);
      (ins[l.target] = ins[l.target] || []).push(r);
      ribbons.push(r);
    });
    Object.keys(outs).forEach(function (id) {
      var y = boxes[id].y;
      outs[id]
        .sort(function (a, b) {
          return a.to.y - b.to.y;
        })
        .forEach(function (r) {
          r.y0 = y;
          y += r.h;
        });
    });
    Object.keys(ins).forEach(function (id) {
      var y = boxes[id].y;
      ins[id]
        .sort(function (a, b) {
          return a.from.y - b.from.y;
        })
        .forEach(function (r) {
          r.y1 = y;
          y += r.h;
        });
    });
    return {
      boxes: boxes,
      ribbons: ribbons,
      width: width,
      height: height,
      k: k,
      cols: cols,
      span: span,
    };
  }

  // The figures above the drawing, for one week.
  function tiles(data, week) {
    var t = throughput(data, week);
    var out = { arrived: 0, stores: 0, workers: 0, given: 0, lost: 0 };
    Object.keys(t).forEach(function (id) {
      var n = t[id].node;
      if (n.kind === 'source') out.arrived += t[id].out;
      else if (n.kind === 'store') out.stores += t[id].held;
      else if (n.kind === 'worker') out.workers += t[id].held;
      else if (id === 'given') out.given += t[id].inn;
      else out.lost += t[id].inn;
    });
    return out;
  }

  // The close-up: the stores that restock workers, the workers, and where it
  // ended -- scaled to those columns alone, so a central stockpile thousands of
  // times a worker's bag does not shrink every worker to a hairline.
  function closeUp(data) {
    var workerColumn = null;
    data.nodes.forEach(function (n) {
      if (n.kind === 'worker') workerColumn = n.column;
    });
    if (workerColumn === null) return data;
    var keep = {};
    data.nodes.forEach(function (n) {
      if (n.column >= workerColumn) keep[n.id] = true;
    });
    data.links.forEach(function (l) {
      var target = data.nodes.filter(function (n) {
        return n.id === l.target;
      })[0];
      if (target && target.kind === 'worker') keep[l.source] = true;
    });
    return {
      weeks: data.weeks,
      as_of: data.as_of,
      unit: data.unit,
      nodes: data.nodes
        .filter(function (n) {
          return keep[n.id];
        })
        .map(function (n) {
          // What reached this store is out of the picture, so what it holds is too.
          return n.kind === 'store'
            ? Object.assign({}, n, { cropped: true })
            : n;
        }),
      links: data.links.filter(function (l) {
        return keep[l.source] && keep[l.target];
      }),
    };
  }

  // A label cut to the room it has, so a long name never runs into the next
  // column; the place's tooltip still carries it whole. Widths are estimated
  // from the font size (Work Sans averages a little over half an em).
  function fit(text, room, fontSize) {
    var most = Math.max(4, Math.floor(room / (fontSize * 0.56)));
    return text.length <= most ? text : text.slice(0, most - 1) + '…';
  }

  root.SupplyFlow = {
    layout: layout,
    tiles: tiles,
    throughput: throughput,
    ordered: ordered,
    closeUp: closeUp,
    fit: fit,
  };

  // ---- the page ------------------------------------------------------------

  if (typeof document === 'undefined') return;
  var dataEl = document.getElementById('flow-data');
  var svg = document.getElementById('flow-svg');
  if (!dataEl || !svg) return;
  var full = JSON.parse(dataEl.textContent);
  var data = full;
  var viewButtons = document.querySelectorAll('[data-flow-view]');
  var NS = 'http://www.w3.org/2000/svg';
  var slider = document.getElementById('flow-week');
  var label = document.getElementById('flow-week-label');
  var play = document.getElementById('flow-play');
  var tbody = document.querySelector('#flow-table tbody');
  var fmt = new Intl.NumberFormat();
  var unit = full.unit_plural || full.unit || '';
  var width = Math.max(svg.parentNode.clientWidth || 960, 760);
  var cols, height, k;
  var highlighted = null;

  function setView(mode) {
    data = mode === 'workers' ? closeUp(full) : full;
    cols = ordered(data);
    height = heightFor(cols);
    k = scaleFor(data, cols, height);
    Array.prototype.forEach.call(viewButtons, function (b) {
      var on = b.getAttribute('data-flow-view') === mode;
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.className =
        'px-3 py-1.5 text-sm ' +
        (on
          ? 'bg-gray-900 text-white'
          : 'bg-white text-gray-700 hover:bg-gray-50');
    });
  }

  function el(name, attrs, text) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (a) {
      node.setAttribute(a, attrs[a]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function colourOf(r) {
    if (r.to.node.id === 'given') return '#34d399';
    if (r.to.node.kind === 'sink') return '#f87171';
    return '#818cf8';
  }

  function weekLabel(i) {
    var start = new Date(data.weeks[i] + 'T00:00:00');
    var end = new Date(start.getTime() + 6 * 86400000);
    var asOf = new Date(data.as_of + 'T00:00:00');
    if (end > asOf) end = asOf;
    return (
      'To ' +
      end.toLocaleDateString(undefined, {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    );
  }

  function describe(b) {
    var n = b.node;
    if (n.kind === 'source')
      return n.name + ': ' + fmt.format(b.out) + ' ' + unit;
    if (n.cropped)
      return (
        n.name +
        ': sent ' +
        fmt.format(b.out) +
        ' ' +
        unit +
        ' to these workers'
      );
    if (n.kind === 'sink')
      return n.name + ': ' + fmt.format(b.inn) + ' ' + unit;
    return (
      n.name +
      (n.parent_name && n.kind === 'worker'
        ? ' (restocked by ' + n.parent_name + ')'
        : '') +
      ': received ' +
      fmt.format(b.inn) +
      ', ' +
      (n.kind === 'worker' ? 'gave out ' : 'sent on ') +
      fmt.format(b.out) +
      ', holds ' +
      fmt.format(b.held) +
      ' ' +
      unit
    );
  }

  function draw(week) {
    var L = layout(data, week, {
      cols: cols,
      width: width,
      height: height,
      k: k,
    });
    while (svg.lastChild && svg.lastChild.nodeName !== 'title')
      svg.removeChild(svg.lastChild);
    svg.setAttribute('width', L.width);
    svg.setAttribute('height', L.height);
    svg.setAttribute('viewBox', '0 0 ' + L.width + ' ' + L.height);
    var defs = el('defs');
    var pattern = el('pattern', {
      id: 'flow-held',
      width: 4,
      height: 4,
      patternUnits: 'userSpaceOnUse',
      patternTransform: 'rotate(45)',
    });
    pattern.appendChild(el('rect', { width: 4, height: 4, fill: '#ffffff' }));
    pattern.appendChild(el('rect', { width: 2, height: 4, fill: '#d1d5db' }));
    defs.appendChild(pattern);
    svg.appendChild(defs);

    var ribbonLayer = el('g');
    L.ribbons.forEach(function (r) {
      var x0 = r.from.x + BAR;
      var x1 = r.to.x;
      var mid = (x0 + x1) / 2;
      var h = Math.max(r.h, 0.5);
      var d =
        'M' +
        x0 +
        ',' +
        r.y0 +
        'C' +
        mid +
        ',' +
        r.y0 +
        ' ' +
        mid +
        ',' +
        r.y1 +
        ' ' +
        x1 +
        ',' +
        r.y1 +
        'L' +
        x1 +
        ',' +
        (r.y1 + h) +
        'C' +
        mid +
        ',' +
        (r.y1 + h) +
        ' ' +
        mid +
        ',' +
        (r.y0 + h) +
        ' ' +
        x0 +
        ',' +
        (r.y0 + h) +
        'Z';
      var on =
        !highlighted ||
        r.link.source === highlighted ||
        r.link.target === highlighted;
      var path = el('path', {
        d: d,
        fill: colourOf(r),
        'fill-opacity': on ? 0.45 : 0.08,
      });
      path.appendChild(
        el(
          'title',
          {},
          r.from.node.name +
            ' → ' +
            r.to.node.name +
            ': ' +
            fmt.format(r.value) +
            ' ' +
            unit,
        ),
      );
      ribbonLayer.appendChild(path);
    });
    svg.appendChild(ribbonLayer);

    var nodeLayer = el('g');
    var lastColumn = L.cols.length - 1;
    Object.keys(L.boxes).forEach(function (id) {
      var b = L.boxes[id];
      var n = b.node;
      var g = el('g', {
        'data-node': id,
        style: 'cursor:' + (n.url ? 'pointer' : 'default'),
      });
      var solid = Math.max(b.h - b.heldH, 0);
      var colour =
        n.kind === 'sink'
          ? id === 'given'
            ? '#059669'
            : '#dc2626'
          : n.kind === 'source'
            ? '#374151'
            : '#4f46e5';
      if (solid > 0)
        g.appendChild(
          el('rect', {
            x: b.x,
            y: b.y,
            width: BAR,
            height: solid,
            fill: colour,
            rx: 1.5,
          }),
        );
      if (b.heldH > 0)
        g.appendChild(
          el('rect', {
            x: b.x,
            y: b.y + solid,
            width: BAR,
            height: b.heldH,
            fill: 'url(#flow-held)',
            stroke: colour,
            'stroke-width': 1,
          }),
        );
      g.appendChild(el('title', {}, describe(b)));
      var big = b.h >= 11 || n.kind !== 'worker';
      if (big && b.h > 0) {
        var leftSide = b.col === lastColumn && lastColumn > 0;
        var detail =
          n.kind === 'sink'
            ? fmt.format(b.inn)
            : n.kind === 'source'
              ? fmt.format(b.out)
              : n.kind === 'worker'
                ? 'holds ' +
                  fmt.format(b.held) +
                  ' · gave out ' +
                  fmt.format(b.out)
                : n.cropped
                  ? 'sent ' +
                    fmt.format(b.out) +
                    ' ' +
                    unit +
                    ' to these workers'
                  : 'holds ' +
                    fmt.format(b.held) +
                    ' · sent on ' +
                    fmt.format(b.out);
        var x = leftSide ? b.x - 6 : b.x + BAR + 6;
        var anchor = leftSide ? 'end' : 'start';
        var halo = {
          stroke: '#ffffff',
          'stroke-width': 3,
          'paint-order': 'stroke',
          'stroke-linejoin': 'round',
        };
        var room = L.span - BAR - 14;
        if (n.kind === 'worker' && b.h < 24) {
          var one = el(
            'text',
            Object.assign(
              {
                x: x,
                y: b.y + b.h / 2 + 3.5,
                'text-anchor': anchor,
                'font-size': 10.5,
                fill: '#111827',
              },
              halo,
            ),
          );
          var line = fit(n.name + '  ' + detail, room, 10.5);
          one.appendChild(el('tspan', {}, line.slice(0, n.name.length)));
          one.appendChild(
            el('tspan', { fill: '#4b5563' }, line.slice(n.name.length)),
          );
          g.appendChild(one);
        } else {
          var top = b.y + 12;
          g.appendChild(
            el(
              'text',
              Object.assign(
                {
                  x: x,
                  y: top,
                  'text-anchor': anchor,
                  'font-size': 12,
                  'font-weight': 600,
                  fill: '#111827',
                },
                halo,
              ),
              fit(n.name, room, 12),
            ),
          );
          g.appendChild(
            el(
              'text',
              Object.assign(
                {
                  x: x,
                  y: top + 14,
                  'text-anchor': anchor,
                  'font-size': 11,
                  fill: '#4b5563',
                },
                halo,
              ),
              fit(n.cropped ? detail : detail + ' ' + unit, room, 11),
            ),
          );
        }
      }
      g.addEventListener('mouseenter', function () {
        highlighted = id;
        draw(Number(slider.value));
      });
      g.addEventListener('mouseleave', function () {
        highlighted = null;
        draw(Number(slider.value));
      });
      if (n.url)
        g.addEventListener('click', function () {
          window.location.href = n.url;
        });
      nodeLayer.appendChild(g);
    });
    svg.appendChild(nodeLayer);

    // Column headings.
    L.cols.forEach(function (col) {
      var kind = col[0].kind;
      var heading =
        kind === 'source'
          ? 'Arrived'
          : kind === 'worker'
            ? 'Field workers (' + col.length + ')'
            : kind === 'sink'
              ? 'Where it ended'
              : col.length === 1
                ? 'Store'
                : 'Stores';
      var x = L.boxes[col[0].id].x;
      svg.appendChild(
        el(
          'text',
          { x: x, y: 14, 'font-size': 11, 'font-weight': 600, fill: '#6b7280' },
          heading.toUpperCase(),
        ),
      );
    });

    var tile = tiles(full, week);
    Object.keys(tile).forEach(function (key) {
      var cell = document.querySelector('[data-tile="' + key + '"]');
      if (cell) cell.textContent = fmt.format(Math.round(tile[key]));
    });
    label.textContent = weekLabel(week);
    if (tbody) {
      tbody.textContent = '';
      L.cols.forEach(function (col) {
        col.forEach(function (n) {
          var b = L.boxes[n.id];
          var tr = document.createElement('tr');
          [
            n.name,
            b.inn,
            b.out,
            n.kind === 'store' || n.kind === 'worker' ? b.held : '',
          ].forEach(function (v, i) {
            var td = document.createElement('td');
            td.className =
              'px-3 py-1.5' + (i ? ' text-right tabular-nums' : '');
            td.textContent = typeof v === 'number' ? fmt.format(v) : v;
            tr.appendChild(td);
          });
          tbody.appendChild(tr);
        });
      });
    }
  }

  Array.prototype.forEach.call(viewButtons, function (b) {
    b.addEventListener('click', function () {
      setView(b.getAttribute('data-flow-view'));
      draw(Number(slider.value));
    });
  });
  setView(
    full.nodes.some(function (n) {
      return n.kind === 'worker';
    })
      ? 'workers'
      : 'all',
  );
  slider.max = String(data.weeks.length - 1);
  slider.value = slider.max;
  slider.addEventListener('input', function () {
    draw(Number(slider.value));
  });
  var timer = null;
  function stop() {
    clearInterval(timer);
    timer = null;
    play.textContent = 'Play';
  }
  play.addEventListener('click', function () {
    if (timer) return stop();
    if (Number(slider.value) >= data.weeks.length - 1) slider.value = '0';
    play.textContent = 'Pause';
    draw(Number(slider.value));
    timer = setInterval(function () {
      var next = Number(slider.value) + 1;
      if (next > data.weeks.length - 1) return stop();
      slider.value = String(next);
      draw(next);
    }, 700);
  });
  draw(Number(slider.value));
})(typeof window !== 'undefined' ? window : globalThis);
