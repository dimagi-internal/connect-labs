/* The Pulse header widget.
 *
 * A labs page opts in with `@page_chrome(pulse_widget=True)` (see
 * connect_labs/labs/chrome.py); the markup is templates/pulse/widget.html and
 * the data is /labs/pulse/api/widget/. Clicking it opens the night map.
 *
 * The pure pieces -- the count, the "2 min ago", the sparkline geometry -- live
 * on `PulseWidget` so they can be tested without a DOM (widget.test.js). The
 * DOM code below them only reads and writes text and attributes.
 *
 * Honesty rule, borrowed from the wall display: the dot only pulses when
 * ingest is healthy. Stale data is shown as stored data, never as live.
 */
(function (root) {
  'use strict';

  var PulseWidget = {
    /** "4,120" -- the count as people read it. */
    count: function (n) {
      return Number(n || 0).toLocaleString('en-US');
    },

    /** "just now", "4 min ago", "3 h ago", "2 d ago". */
    ago: function (seconds) {
      var s = Math.max(0, Math.floor(seconds || 0));
      if (s < 60) return 'just now';
      if (s < 3600) return Math.floor(s / 60) + ' min ago';
      if (s < 86400) return Math.floor(s / 3600) + ' h ago';
      return Math.floor(s / 86400) + ' d ago';
    },

    /** "Kangaroo Mother Care · Nigeria · 4 min ago", skipping what is missing. */
    latest: function (item) {
      if (!item) return '';
      return [item.service, item.country, PulseWidget.ago(item.seconds_ago)]
        .filter(Boolean)
        .join(' · ');
    },

    /**
     * Bars for the 24-hour sparkline, in a width x height box. Every hour
     * gets a slot; an empty hour draws a 1px stub so the axis stays legible
     * and a quiet night reads as quiet rather than as missing.
     */
    bars: function (hourly, width, height) {
      var n = hourly.length || 1;
      var max = Math.max.apply(null, hourly.concat([1]));
      var slot = width / n;
      var w = Math.max(slot - 1, 1);
      return hourly.map(function (v, i) {
        var h = v ? Math.max((v / max) * height, 2) : 1;
        return {
          x: +(i * slot).toFixed(2),
          y: +(height - h).toFixed(2),
          w: +w.toFixed(2),
          h: +h.toFixed(2),
        };
      });
    },
  };

  root.PulseWidget = PulseWidget;
  if (typeof module !== 'undefined' && module.exports)
    module.exports = PulseWidget;
  if (typeof document === 'undefined') return;

  var SVG = 'http://www.w3.org/2000/svg';

  function paint(el, data) {
    var q = function (name) {
      return el.querySelector('[data-pw-' + name + ']');
    };
    el.dataset.state = data.live ? 'live' : 'stale';
    q('count').textContent = PulseWidget.count(data.services_24h);

    var where = data.countries_24h
      ? ' across ' +
        data.countries_24h +
        ' countr' +
        (data.countries_24h === 1 ? 'y' : 'ies')
      : '';
    el.title =
      (data.live ? 'Live' : 'Showing stored data, not live') +
      ' — ' +
      PulseWidget.count(data.services_24h) +
      ' services in the last 24 hours' +
      where +
      '. Open the night map.';

    var latest = q('latest');
    if (latest) latest.textContent = PulseWidget.latest(data.latest);

    var svg = q('spark');
    if (svg) {
      var box = svg.viewBox.baseVal;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      PulseWidget.bars(data.hourly || [], box.width, box.height).forEach(
        function (b) {
          var r = document.createElementNS(SVG, 'rect');
          r.setAttribute('x', b.x);
          r.setAttribute('y', b.y);
          r.setAttribute('width', b.w);
          r.setAttribute('height', b.h);
          r.setAttribute('rx', 0.5);
          svg.appendChild(r);
        },
      );
    }
  }

  function boot(el) {
    var url = el.dataset.url;
    var timer = null;
    var every = 60;
    var painted = false;

    function tick() {
      if (document.hidden) return; // resumed on visibilitychange
      fetch(url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function (data) {
          paint(el, data);
          painted = true;
          every = Math.max(Number(data.poll_seconds) || 60, 30);
        })
        .catch(function (err) {
          // Keep whatever is on screen and say so on hover, not in the header.
          // Before a first answer there is no number to call stale, so it stays
          // "Pulse" rather than turning into a label with nothing in it.
          if (painted) el.dataset.state = 'stale';
          el.title =
            'Pulse could not be reached (' +
            err.message +
            '). Open the night map.';
        })
        .then(function () {
          clearTimeout(timer);
          timer = setTimeout(tick, every * 1000);
        });
    }

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) {
        clearTimeout(timer);
        tick();
      }
    });
    tick();
  }

  function start() {
    var el = document.getElementById('pulse-widget');
    if (el && !el.dataset.booted) {
      el.dataset.booted = '1';
      boot(el);
    }
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', start);
  else start();
})(typeof window !== 'undefined' ? window : globalThis);
