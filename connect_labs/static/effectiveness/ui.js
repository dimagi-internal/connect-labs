/* Shared by the effectiveness pages (CEA models, Market size): the DOM builder and the number formats.
 *
 * Everything is built as DOM nodes with text set through textContent, so nothing from the model data is ever parsed
 * as HTML and there is nothing to escape. */
(function () {
  'use strict';

  function h(tag, props, kids) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'css')
        Object.keys(v).forEach(function (c) {
          e.style[c] = v[c];
        });
      else if (k === 'data')
        Object.keys(v).forEach(function (d) {
          e.dataset[d] = v[d];
        });
      else if (v === true) e.setAttribute(k, '');
      else e.setAttribute(k, v);
    });
    [].concat(kids == null ? [] : kids).forEach(function (c) {
      if (c != null)
        e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }

  function put(el, nodes) {
    while (el.firstChild) el.removeChild(el.firstChild);
    nodes.forEach(function (n) {
      el.appendChild(typeof n === 'string' ? document.createTextNode(n) : n);
    });
  }

  function safeUrl(u) {
    return /^https:\/\//.test(u || '') ? u : '#';
  }

  function dec(f) {
    return (String(f || '').split('.')[1] || '').replace(/[^0#]/g, '').length;
  }

  function fmt(v, f) {
    if (v == null || isNaN(v)) return '–';
    f = f || '';
    var d = dec(f);
    if (f.indexOf('%') >= 0) return (v * 100).toFixed(d) + '%';
    if (f.indexOf('$') >= 0)
      return (
        '$' +
        Number(v).toLocaleString(undefined, {
          minimumFractionDigits: d,
          maximumFractionDigits: d,
        })
      );
    if (!f || f === 'General')
      return Number(v).toLocaleString(undefined, {
        maximumSignificantDigits: 4,
      });
    return Number(v).toLocaleString(undefined, {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    });
  }

  function mult(v) {
    return v == null ? '–' : v >= 10 ? v.toFixed(0) : v.toFixed(1);
  }

  function usd(v) {
    if (v == null) return '–';
    var a = Math.abs(v);
    if (a >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return '$' + (v / 1e6).toFixed(a >= 1e8 ? 0 : 1) + 'M';
    if (a >= 1e3) return '$' + (v / 1e3).toFixed(0) + 'K';
    return '$' + v.toFixed(2);
  }

  function num(v) {
    return v == null
      ? '–'
      : v >= 1e6
        ? (v / 1e6).toFixed(1) + 'M'
        : v >= 1e3
          ? (v / 1e3).toFixed(0) + 'K'
          : String(Math.round(v));
  }

  window.EffUI = {
    h: h,
    put: put,
    safeUrl: safeUrl,
    dec: dec,
    fmt: fmt,
    mult: mult,
    usd: usd,
    num: num,
  };
})();
