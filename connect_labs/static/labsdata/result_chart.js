// How a query result reads and how it is drawn: the column kinds, the formats of the
// result's `display` (labsdata/display.py: labels, formats, chart), and the chart as SVG.
//
// ONE copy, used by both places a result is shown:
//   - the results card the agent's data_query returns (connect_labs/mcp/ui/data_result.html,
//     an MCP Apps View): the View must load nothing over the network, so labs inlines this
//     file into it where it is served (at the card's include marker,
//     connect_labs/mcp/ui/__init__.py);
//   - the Data page (static/labsdata/data.js), which loads it as a script, to draw a saved
//     query's chart the way the card drew it when it was saved.
//
// No DOM, no network: strings in, strings out. ES5 so it runs anywhere the card runs.
// `LabsResultChart.view(result, display)` binds a result ({columns, rows, column_types?})
// and its display; its `axes()` chooses what to chart and `draw()` returns the SVG (with
// its legend and notes) as HTML. Every value drawn is escaped here.
var LabsResultChart = (function () {
  'use strict';

  var SERIES = ['--s1', '--s2', '--s3', '--s4', '--s5'];
  var MAX_BARS = 30;

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c];
    });
  }
  function hasRows(r) {
    return !!(r && Array.isArray(r.columns) && Array.isArray(r.rows));
  }
  function cellText(v) {
    if (v === null || v === undefined) return 'null';
    if (typeof v === 'object') return JSON.stringify(v);
    if (typeof v === 'number') return v.toLocaleString();
    return String(v);
  }
  function isNum(v) {
    return typeof v === 'number' && isFinite(v);
  }
  function looksLikeDate(v) {
    return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v);
  }
  // ------------------------------------------------------------ formatting
  // What each column holds: Labs' own column_types when the result carries them,
  // else read from the name and the values. An id is never "10,234".
  var ID_NAME = /(^|_)(id|ids|uuid|year)$|^(id|ids|uuid|year)$/i;
  var TYPED = {
    integer: 1,
    numeric: 1,
    date: 1,
    timestamp: 1,
    text: 1,
    bool: 1,
    json: 1,
  };
  function columnKinds(r) {
    var types =
      Array.isArray(r.column_types) &&
      r.column_types.length === r.columns.length
        ? r.column_types
        : null;
    return r.columns.map(function (c, i) {
      if (ID_NAME.test(String(c))) return 'id';
      if (types && TYPED[types[i]]) return types[i];
      var seen = r.rows
        .map(function (row) {
          return row[i];
        })
        .filter(function (v) {
          return v !== null && v !== undefined;
        });
      function all(test) {
        return seen.length > 0 && seen.every(test);
      }
      if (all(isNum)) return all(Number.isInteger) ? 'integer' : 'numeric';
      if (
        all(function (v) {
          return typeof v === 'boolean';
        })
      )
        return 'bool';
      if (
        all(function (v) {
          return typeof v === 'object';
        })
      )
        return 'json';
      if (
        all(function (v) {
          return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v);
        })
      )
        return 'date';
      if (
        all(function (v) {
          return (
            typeof v === 'string' && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(v)
          );
        })
      )
        return 'timestamp';
      return 'text';
    });
  }
  function isNumeric(kind) {
    return kind === 'integer' || kind === 'numeric';
  }
  function shortDate(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v));
    if (!m) return String(v);
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).toLocaleDateString(
      undefined,
      {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        timeZone: 'UTC',
      },
    );
  }
  // A month bucket (date_trunc('month', ...)) as the person reads it: "Jan 2026".
  var MONTH_START =
    /^(\d{4})-(\d{2})-01(?:[T ]00:00(?::00(?:\.0+)?)?(?:Z|[+-]00(?::?00)?)?)?$/;
  function monthLabel(v) {
    var m = MONTH_START.exec(String(v));
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, 1)).toLocaleDateString(
      undefined,
      { year: 'numeric', month: 'short', timeZone: 'UTC' },
    );
  }
  function shortTimestamp(v) {
    var d = new Date(String(v));
    if (isNaN(d.getTime())) return String(v);
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
  // A cell as the person reads it: {text, cls}, or {chip} for JSON.
  function display(v, kind) {
    if (v === null || v === undefined) return { text: '—', cls: 'null' };
    if (kind === 'json' || (typeof v === 'object' && kind !== 'id')) {
      if (typeof v !== 'object') return { text: String(v), cls: '' };
      return {
        chip: Array.isArray(v)
          ? '[' + v.length + (v.length === 1 ? ' item]' : ' items]')
          : '{' +
            Object.keys(v).length +
            (Object.keys(v).length === 1 ? ' key}' : ' keys}'),
      };
    }
    if (kind === 'id')
      return {
        text: typeof v === 'object' ? JSON.stringify(v) : String(v),
        cls: '',
      };
    if (isNumeric(kind) && (isNum(v) || (v !== '' && isFinite(v)))) {
      var n = Number(v);
      var places = kind === 'integer' ? 0 : Math.abs(n) >= 100 ? 1 : 2;
      return {
        text: n.toLocaleString(undefined, {
          maximumFractionDigits: places,
        }),
        cls: 'num',
      };
    }
    if (kind === 'date') return { text: shortDate(v), cls: '' };
    if (kind === 'timestamp') return { text: shortTimestamp(v), cls: '' };
    return { text: cellText(v), cls: isNum(v) ? 'num' : '' };
  }
  // snake_case as words: "pct_first_fu_0_3d" reads "Pct first FU 0–3d".
  var ACRONYMS = {
    id: 'ID',
    ids: 'IDs',
    flw: 'FLW',
    flws: 'FLWs',
    llo: 'LLO',
    llos: 'LLOs',
    fu: 'FU',
    url: 'URL',
    sql: 'SQL',
    gps: 'GPS',
    muac: 'MUAC',
    dhs: 'DHS',
    uuid: 'UUID',
    anc: 'ANC',
    pnc: 'PNC',
    kmc: 'KMC',
    lbw: 'LBW',
    pmc: 'PMC',
  };
  function label(name) {
    var words = String(name)
      .split(/[_\s]+/)
      .filter(Boolean);
    var out = [];
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      if (
        /^\d+$/.test(w) &&
        i + 1 < words.length &&
        /^\d+[a-z]*$/i.test(words[i + 1])
      ) {
        out.push(w + '–' + words[i + 1]);
        i++;
        continue;
      }
      var lower = w.toLowerCase();
      if (ACRONYMS[lower]) out.push(ACRONYMS[lower]);
      else if (!out.length && w === lower)
        out.push(lower.charAt(0).toUpperCase() + lower.slice(1));
      else out.push(w);
    }
    return out.join(' ') || String(name);
  }

  // Axis labels that tell the bars apart. A prefix every label shares ("[Synthetic]
  // KMC - ") is said once under the chart instead of on every bar, and a long label
  // is cut in the MIDDLE, so its end -- often the part that differs -- still shows.
  // The whole label is each bar's tooltip.
  var AXIS_LABEL_MAX = 18;
  function middleCut(t, max) {
    t = String(t).trim();
    if (t.length <= max) return t;
    var head = Math.ceil((max - 1) / 2);
    return t.slice(0, head) + '\u2026' + t.slice(t.length - (max - 1 - head));
  }
  function sharedPrefix(texts) {
    if (texts.length < 2) return '';
    var p = texts[0];
    texts.forEach(function (t) {
      while (p && t.indexOf(p) !== 0) p = p.slice(0, -1);
    });
    // Back to the last word boundary, so no label starts mid-word.
    var m = p.match(/^[\s\S]*[\s\-\u2013\u2014:\u00b7|\/]/);
    p = m ? m[0] : '';
    if (p.trim().length < 3) return '';
    var keeps = texts.every(function (t) {
      return t.slice(p.length).trim();
    });
    return keeps ? p : '';
  }

  // A result and its display, bound: everything below reads them, and caches what it
  // reads once per column on the result itself (r._pct, r._month).
  function view(r, given) {
    var shown = given && typeof given === 'object' ? given : null;
    // What is charted: set by axes() (chosen) or by draw()'s options (the person's pick).
    var state = { x: null, ys: [], chartType: '' };
    // ------------------------------------------------------------ the agent's display
    // How the agent said the result reads (data_query's `display`, checked by Labs
    // and kept in the result: labsdata/display.py): labels, formats, a chart.
    function shownAs() {
      return shown;
    }
    function spec(name) {
      var d = shownAs();
      var c = d && d.columns && d.columns[name];
      return c && typeof c === 'object' ? c : {};
    }
    // The agent's label, else the column's own name in words. Plain text: always
    // escaped where it is drawn.
    function heading(name) {
      var l = spec(name).label;
      return typeof l === 'string' && l ? l : label(name);
    }
    var FORMAT = /^(number|percent|currency|compact)_([0-4])$/;
    // A value in the agent's format, or null when the format does not fit it (a
    // number format on text): the cell then reads as its type says.
    function formatted(v, format) {
      if (v === null || v === undefined || !format) return null;
      if (format === 'month') {
        var ml = monthLabel(String(v).slice(0, 10));
        return ml ? { text: ml, cls: '' } : null;
      }
      if (format === 'date') {
        if (/^\d{4}-\d{2}-\d{2}$/.test(String(v)))
          return { text: shortDate(v), cls: '' };
        if (/^\d{4}-\d{2}-\d{2}[T ]/.test(String(v)))
          return { text: shortDate(String(v).slice(0, 10)), cls: '' };
        return null;
      }
      var m = FORMAT.exec(format);
      var n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
      if (!m || !isNum(n)) return null;
      var places = Number(m[2]);
      var opts = {
        minimumFractionDigits: places,
        maximumFractionDigits: places,
      };
      if (m[1] === 'percent') opts.style = 'percent';
      if (m[1] === 'currency') {
        opts.style = 'currency';
        opts.currency = (shownAs() && shownAs().currency) || 'USD';
      }
      if (m[1] === 'compact') {
        opts.notation = 'compact';
        opts.minimumFractionDigits = 0;
      }
      try {
        return { text: n.toLocaleString(undefined, opts), cls: 'num' };
      } catch (e) {
        return null;
      }
    }
    // A column whose name says it is a percentage (pct_*, *_pct, *_rate, percent)
    // reads as one with no hint: 0-100 values as "70%", a column of fractions
    // (all within 0-1, some not whole) as "70.0%". Anything outside 0-100 is not.
    var PCT_NAME = /(^pct_|_pct$|^pct$|_rate$|^rate$|percent)/i;
    function percentScale(name) {
      if (!r || !PCT_NAME.test(String(name))) return '';
      r._pct = r._pct || {};
      if (name in r._pct) return r._pct[name];
      var i = r.columns.indexOf(name);
      var vals = (r.rows || [])
        .map(function (row) {
          var v = row[i];
          return typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
        })
        .filter(isNum);
      var scale = '';
      if (
        vals.length &&
        vals.every(function (v) {
          return v >= 0 && v <= 100;
        })
      ) {
        var fraction =
          vals.every(function (v) {
            return v <= 1;
          }) &&
          vals.some(function (v) {
            return !Number.isInteger(v);
          });
        scale = fraction ? 'fraction' : 'hundred';
      }
      r._pct[name] = scale;
      return scale;
    }
    // A date column of month buckets: every value the first of a month at midnight,
    // and more than one month (or a column called a month). Read once per column.
    var MONTH_NAME = /month|(^|_)mon$/i;
    function monthly(name) {
      if (!r) return false;
      r._month = r._month || {};
      if (name in r._month) return r._month[name];
      var i = r.columns.indexOf(name);
      var vals = (r.rows || [])
        .map(function (row) {
          return row[i];
        })
        .filter(function (v) {
          return v !== null && v !== undefined;
        });
      var all =
        vals.length > 0 &&
        vals.every(function (v) {
          return typeof v === 'string' && MONTH_START.test(v);
        });
      var distinct = {};
      vals.forEach(function (v) {
        distinct[String(v).slice(0, 7)] = 1;
      });
      r._month[name] =
        all &&
        (Object.keys(distinct).length > 1 || MONTH_NAME.test(String(name)));
      return r._month[name];
    }
    function cell(v, kind, name) {
      var fmt = spec(name).format;
      if ((!fmt || fmt === 'date') && v != null && monthly(name)) {
        var asMonth = formatted(v, 'month');
        if (asMonth) return asMonth;
      }
      var own = formatted(v, fmt);
      if (own) return own;
      var scale = !spec(name).format && percentScale(name);
      var n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
      if (scale && isNum(n))
        return scale === 'fraction'
          ? formatted(n, 'percent_1')
          : {
              text:
                n.toLocaleString(undefined, { maximumFractionDigits: 1 }) + '%',
              cls: 'num',
            };
      return display(v, kind);
    }

    // What to chart. The agent's `display.chart` when it gave one; otherwise the
    // measures are the numeric columns that are not ids or years, all on ONE
    // scale (a rate is never drawn beside a count), at most five, against the
    // first column that is a category or a date.
    var YEAR_NAME = /(^|_)(year|yr)s?$/i;
    var RATE_NAME = /(rate|pct|percent|ratio|share|proportion)/i;
    function scaleOf(r, i) {
      var f = spec(r.columns[i]).format || '';
      if (/^percent_/.test(f)) return 'percent';
      if (f) return 'count';
      if (RATE_NAME.test(r.columns[i])) return 'percent';
      var vals = r.rows
        .map(function (row) {
          return row[i];
        })
        .filter(isNum);
      var fraction =
        vals.length &&
        vals.every(function (v) {
          return v >= 0 && v <= 1;
        }) &&
        vals.some(function (v) {
          return !Number.isInteger(v);
        });
      return fraction ? 'percent' : 'count';
    }
    function measureColumns(r, kinds) {
      return r.columns
        .map(function (c, i) {
          return i;
        })
        .filter(function (i) {
          if (!isNumeric(kinds[i])) return false;
          if (YEAR_NAME.test(r.columns[i])) return false;
          var vals = r.rows
            .map(function (row) {
              return row[i];
            })
            .filter(isNum);
          // A column of whole numbers 1900-2100 is a year, whatever it is called.
          var years =
            vals.length &&
            vals.every(function (v) {
              return Number.isInteger(v) && v >= 1900 && v <= 2100;
            });
          return !years;
        });
    }
    function chooseAxes() {
      state.x = null;
      state.ys = [];
      state.chartType = '';
      if (!hasRows(r) || !r.rows.length) return;
      var kinds = columnKinds(r);
      var hint = shownAs() && shownAs().chart;
      if (hint && typeof hint === 'object') {
        var xi = r.columns.indexOf(hint.x);
        var yi = (Array.isArray(hint.y) ? hint.y : [])
          .map(function (c) {
            return r.columns.indexOf(c);
          })
          .filter(function (i) {
            return i >= 0;
          });
        if (hint.type === 'stat') {
          state.chartType = 'stat';
          state.ys = yi.slice(0, 1);
          state.x = null;
          if (state.ys.length) return;
        } else if (xi >= 0) {
          state.chartType = hint.type === 'line' ? 'line' : 'bar';
          state.x = xi;
          state.ys = (yi.length ? yi : autoMeasures(r, kinds, xi)).slice(
            0,
            SERIES.length,
          );
          if (state.ys.length) return;
        }
      }
      var measures = measureColumns(r, kinds);
      var x = -1;
      for (var i = 0; i < r.columns.length; i++) {
        if (measures.indexOf(i) >= 0 || kinds[i] === 'json') continue;
        if (kinds[i] === 'id' && x >= 0) continue;
        x = i;
        if (kinds[i] !== 'id') break;
      }
      state.x = x >= 0 ? x : 0;
      state.ys = autoMeasures(r, kinds, state.x);
      state.chartType =
        kinds[state.x] === 'date' || kinds[state.x] === 'timestamp'
          ? 'line'
          : 'bar';
    }
    function autoMeasures(r, kinds, x) {
      var measures = measureColumns(r, kinds).filter(function (i) {
        return i !== x;
      });
      if (!measures.length) return [];
      var scale = scaleOf(r, measures[0]);
      return measures
        .filter(function (i) {
          return scaleOf(r, i) === scale;
        })
        .slice(0, SERIES.length);
    }

    // ---------------------------------------------------------------- chart (SVG)
    // A row's x value as its cell reads (a month bucket as "Jan 2026").
    function xText(row) {
      return cell(row[state.x], columnKinds(r)[state.x], r.columns[state.x])
        .text;
    }
    // The x position of the bucket that is this calendar month (UTC), when x is a
    // column of month buckets: its figure is the month so far, not a whole month.
    function partialMonthIndex(rows) {
      if (!monthly(r.columns[state.x])) return -1;
      var now = new Date();
      var current =
        now.getUTCFullYear() +
        '-' +
        String(now.getUTCMonth() + 1).padStart(2, '0');
      for (var i = 0; i < rows.length; i++) {
        if (String(rows[i][state.x]).slice(0, 7) === current) return i;
      }
      return -1;
    }
    // srcRows: the rows in the order to draw them; width: the space there is, in px.
    function chart(srcRows, width) {
      if (state.chartType === 'stat' && hasRows(r) && state.ys.length)
        return stat();
      if (!hasRows(r) || state.x === null || !state.ys.length)
        return '<div class="sub">Nothing to chart: the result needs a numeric column.</div>';
      var rows = srcRows.slice(0, 500);
      var line =
        rows.length > 1 &&
        (state.chartType === 'line' ||
          (state.chartType !== 'bar' &&
            rows.every(function (row) {
              return looksLikeDate(row[state.x]);
            })));
      if (!line) {
        // Bars largest first, by the first series: the comparison reads at a glance.
        var by = state.ys[0];
        rows = rows.slice().sort(function (a, b) {
          return (
            (isNum(b[by]) ? b[by] : -Infinity) -
            (isNum(a[by]) ? a[by] : -Infinity)
          );
        });
        if (rows.length > MAX_BARS) rows = rows.slice(0, MAX_BARS);
      }
      var tickFormat = spec(r.columns[state.ys[0]]).format;
      var hundred =
        !tickFormat && percentScale(r.columns[state.ys[0]]) === 'hundred';
      if (!tickFormat && !hundred && scaleOf(r, state.ys[0]) === 'percent')
        tickFormat = 'percent_0';
      var w = Math.max(320, Math.min(width, 1100));
      var h = 240;
      var pad = { l: 48, r: 10, t: 10, b: line ? 30 : 70 };
      var iw = w - pad.l - pad.r;
      var ih = h - pad.t - pad.b;
      var max = 0;
      rows.forEach(function (row) {
        state.ys.forEach(function (i) {
          if (isNum(row[i]) && row[i] > max) max = row[i];
        });
      });
      if (max <= 0) max = 1;
      // Round gridlines: a step of 1, 2 or 5 times a power of ten, and an axis that
      // ends on one.
      var raw = max / 4;
      var mag = Math.pow(10, Math.floor(Math.log10(raw)));
      var step = [1, 2, 5, 10]
        .map(function (k) {
          return k * mag;
        })
        .filter(function (v) {
          return v >= raw;
        })[0];
      var ticks = Math.ceil(max / step);
      max = ticks * step;
      function yPos(v) {
        return pad.t + ih - (ih * (isNum(v) ? v : 0)) / max;
      }
      var out = [
        '<svg width="' +
          w +
          '" height="' +
          h +
          '" viewBox="0 0 ' +
          w +
          ' ' +
          h +
          '" role="img" aria-label="Chart of the result">',
      ];
      for (var g = 0; g <= ticks; g++) {
        var gv = step * g;
        var gy = yPos(gv);
        out.push(
          '<line x1="' +
            pad.l +
            '" x2="' +
            (w - pad.r) +
            '" y1="' +
            gy +
            '" y2="' +
            gy +
            '" stroke="var(--color-border-secondary)"/>',
          '<text x="' +
            (pad.l - 6) +
            '" y="' +
            (gy + 4) +
            '" text-anchor="end">' +
            esc(
              (formatted(gv, tickFormat) || {}).text ||
                (hundred ? +gv.toFixed(1) + '%' : '') ||
                (gv >= 1000 ? Math.round(gv).toLocaleString() : +gv.toFixed(2)),
            ) +
            '</text>',
        );
      }
      var n = rows.length;
      var notes = [];
      if (line) {
        var partial = partialMonthIndex(rows);
        state.ys.forEach(function (col, s) {
          var pts = rows.map(function (row, i) {
            var px = pad.l + (n === 1 ? iw / 2 : (iw * i) / (n - 1));
            return px.toFixed(1) + ',' + yPos(row[col]).toFixed(1);
          });
          // This month so far: its segment dashed and its point hollow, so a fall at
          // the end of the line is not read as a fall in the work.
          var solid = pts;
          var dashed = null;
          if (partial === n - 1 && n > 1) {
            solid = pts.slice(0, n - 1);
            dashed = pts.slice(n - 2);
          } else if (partial === 0 && n > 1) {
            solid = pts.slice(1);
            dashed = pts.slice(0, 2);
          }
          out.push(
            '<polyline fill="none" stroke-width="2" stroke="var(' +
              SERIES[s] +
              ')" points="' +
              solid.join(' ') +
              '"/>',
          );
          if (dashed)
            out.push(
              '<polyline class="partial" fill="none" stroke-width="2" stroke-dasharray="4 3" stroke="var(' +
                SERIES[s] +
                ')" points="' +
                dashed.join(' ') +
                '"/>',
            );
          if (partial >= 0) {
            var xy = pts[partial].split(',');
            out.push(
              '<circle class="partial" cx="' +
                xy[0] +
                '" cy="' +
                xy[1] +
                '" r="3.5" fill="var(--color-background-primary, #fff)" stroke-width="2" stroke="var(' +
                SERIES[s] +
                ')"><title>' +
                esc(xText(rows[partial]) + ': this month so far') +
                '</title></circle>',
            );
          }
        });
        if (partial >= 0)
          notes.push(
            esc(xText(rows[partial])) +
              ' is this month: its figure is the month so far (dashed), not a whole month.',
          );
        [0, Math.floor((n - 1) / 2), n - 1].forEach(function (i, k, all) {
          if (k && i === all[k - 1]) return;
          var px = pad.l + (n === 1 ? iw / 2 : (iw * i) / (n - 1));
          out.push(
            '<text x="' +
              px +
              '" y="' +
              (h - 10) +
              '" text-anchor="middle">' +
              esc(middleCut(xText(rows[i]), AXIS_LABEL_MAX)) +
              '</text>',
          );
        });
      } else {
        var band = iw / n;
        var bw = Math.max(2, (band * 0.8) / state.ys.length);
        var fullLabels = rows.map(xText);
        var prefix = sharedPrefix(fullLabels);
        if (prefix)
          notes.push(
            'Every label starts \u201c' +
              esc(prefix.trim()) +
              '\u201d, left off the axis.',
          );
        rows.forEach(function (row, i) {
          state.ys.forEach(function (col, s) {
            var bx = pad.l + band * i + band * 0.1 + bw * s;
            var by = yPos(row[col]);
            out.push(
              '<rect x="' +
                bx.toFixed(1) +
                '" y="' +
                by.toFixed(1) +
                '" width="' +
                bw.toFixed(1) +
                '" height="' +
                Math.max(0, pad.t + ih - by).toFixed(1) +
                '" fill="var(' +
                SERIES[s] +
                ')"><title>' +
                esc(
                  xText(row) +
                    ' · ' +
                    heading(r.columns[col]) +
                    ': ' +
                    cell(row[col], 'numeric', r.columns[col]).text,
                ) +
                '</title></rect>',
            );
          });
          var lx = pad.l + band * i + band / 2;
          out.push(
            '<g><title>' +
              esc(fullLabels[i]) +
              '</title><text transform="translate(' +
              lx.toFixed(1) +
              ',' +
              (pad.t + ih + 10) +
              ') rotate(40)" text-anchor="start">' +
              esc(
                middleCut(fullLabels[i].slice(prefix.length), AXIS_LABEL_MAX),
              ) +
              '</text></g>',
          );
        });
      }
      out.push('</svg>');
      var legend = state.ys
        .map(function (col, s) {
          return (
            '<span><i style="background:var(' +
            SERIES[s] +
            ')"></i>' +
            esc(heading(r.columns[col])) +
            '</span>'
          );
        })
        .join('');
      var cut =
        r.rows.length > rows.length
          ? '<div class="sub">Charting the first ' +
            rows.length +
            ' rows.</div>'
          : '';
      cut += notes
        .map(function (t) {
          return '<div class="sub chart-note">' + t + '</div>';
        })
        .join('');
      return out.join('') + '<div class="legend">' + legend + '</div>' + cut;
    }

    // One headline number (display.chart.type stat): the first row's value, large.
    function stat() {
      var col = state.ys[0];
      var name = r.columns[col];
      var v = r.rows.length ? r.rows[0][col] : null;
      return (
        '<div class="stat"><div class="stat-value">' +
        esc(cell(v, columnKinds(r)[col], name).text) +
        '</div><div class="sub">' +
        esc(heading(name)) +
        '</div></div>'
      );
    }

    // What to chart, as {x, ys, type}: the display's chart when it gave one that fits the
    // result, else picked from the rows. `fromDisplay` says the display asked for it.
    function axes() {
      chooseAxes();
      var hint = shown && shown.chart;
      return {
        x: state.x,
        ys: state.ys.slice(),
        type: state.chartType,
        fromDisplay: !!(hint && typeof hint === 'object' && state.ys.length),
      };
    }
    // The chart as HTML: the SVG, its legend and notes (or the stat, or a line saying
    // there is nothing to chart). opts: {x, ys, type} (default: axes()), rows (default:
    // the result's, in order), width in px (default 640).
    function draw(opts) {
      opts = opts || {};
      if (opts.ys) {
        state.x = opts.x === undefined ? null : opts.x;
        state.ys = opts.ys.slice();
        state.chartType = opts.type || '';
      } else chooseAxes();
      return chart(opts.rows || (hasRows(r) ? r.rows : []), opts.width || 640);
    }

    return {
      result: r,
      display: shown,
      spec: spec,
      heading: heading,
      formatted: formatted,
      percentScale: percentScale,
      monthly: monthly,
      cell: cell,
      scaleOf: scaleOf,
      axes: axes,
      draw: draw,
      stat: stat,
    };
  }

  return {
    SERIES: SERIES,
    MAX_BARS: MAX_BARS,
    esc: esc,
    hasRows: hasRows,
    cellText: cellText,
    isNum: isNum,
    looksLikeDate: looksLikeDate,
    columnKinds: columnKinds,
    isNumeric: isNumeric,
    label: label,
    middleCut: middleCut,
    sharedPrefix: sharedPrefix,
    view: view,
  };
})();
if (typeof window !== 'undefined') window.LabsResultChart = LabsResultChart;
