/* The network page: filtering in place, and a map you can resize.
 *
 * Filtering. Every filter on the page is an ordinary link or GET form, and
 * stays one: this only intercepts them. A click fetches the filtered page,
 * swaps each `data-mk-swap` region for its new copy, hands the globe its new
 * points and pushes the URL, so the address bar, Back and a shared link all
 * mean what they did. What it saves is everything a full reload threw away:
 * the globe rebuilt from nothing, the page scrolled to the top, and a blank
 * screen while it happened. Anything unexpected -- a failed fetch, a page that
 * is not this one -- falls back to plain navigation rather than a half-swapped
 * page.
 *
 * Resizing. The map's height follows the bar along its bottom edge, and is
 * remembered in this browser only. Double-click the bar to put it back.
 */
(function () {
  'use strict';

  var CHECKED = ['bg-brand-indigo', 'border-brand-indigo', 'text-white'];
  var UNCHECKED = ['bg-white', 'border-brand-border-light', 'text-transparent'];

  function setUpFiltering(root) {
    if (
      !window.fetch ||
      !window.DOMParser ||
      !window.history ||
      !history.pushState
    )
      return;

    var inflight = null;

    function busy(on) {
      root.setAttribute('aria-busy', on ? 'true' : 'false');
      var list = root.querySelector('[data-mk-swap="list"]');
      if (list) list.style.opacity = on ? '0.55' : '';
    }

    // The new copy of each region replaces the old one whole. The regions sit
    // inside, never on, the elements Alpine binds, so the rail's open/closed
    // state on a phone survives the swap.
    function swap(doc) {
      var fresh = doc.getElementById('mk-network');
      if (!fresh) return false;
      var regions = root.querySelectorAll('[data-mk-swap]');
      for (var i = 0; i < regions.length; i++) {
        var name = regions[i].getAttribute('data-mk-swap');
        var next = fresh.querySelector('[data-mk-swap="' + name + '"]');
        if (!next) return false;
      }
      var focused = document.activeElement;
      var refocus =
        focused && focused.type === 'search' && root.contains(focused);
      for (var j = 0; j < regions.length; j++) {
        var key = regions[j].getAttribute('data-mk-swap');
        regions[j].replaceWith(
          document.importNode(
            fresh.querySelector('[data-mk-swap="' + key + '"]'),
            true,
          ),
        );
      }
      if (refocus) {
        var input = root.querySelector('input[type="search"]');
        if (input) {
          input.focus();
          input.setSelectionRange(input.value.length, input.value.length);
        }
      }
      return true;
    }

    function readJson(doc, id) {
      var el = doc.getElementById(id);
      if (!el) return null;
      try {
        return JSON.parse(el.textContent);
      } catch (e) {
        return null;
      }
    }

    function navigate(url, push) {
      if (inflight) inflight.abort();
      var ctrl = window.AbortController ? new AbortController() : null;
      inflight = ctrl;
      busy(true);

      fetch(url, {
        credentials: 'same-origin',
        signal: ctrl ? ctrl.signal : undefined,
      })
        .then(function (r) {
          // A login redirect, an error page: let the browser show it properly.
          if (
            !r.ok ||
            new URL(r.url, window.location.href).pathname !==
              window.location.pathname
          ) {
            throw new Error('not the network page');
          }
          return r.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, 'text/html');
          if (!swap(doc)) throw new Error('page shape changed');

          var points = readJson(doc, 'mk-points');
          if (points && window.MarketplaceGlobe)
            window.MarketplaceGlobe.setPoints(points);

          // Tell the agent panel what is on screen now, as a reload would have.
          var state = readJson(doc, 'canopy-page-state');
          if (state && window.canopyHost)
            window.canopyHost.updatePageState(state);

          if (doc.title) document.title = doc.title;
          if (push) history.pushState({ mkNetwork: true }, '', url);
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          window.location.href = url;
        })
        .then(function () {
          if (inflight === ctrl) {
            inflight = null;
            busy(false);
          }
        });
    }

    // Tick the box the moment it is clicked; the swap that follows confirms it.
    function tickNow(link) {
      var box = link.querySelector('[data-mk-box]');
      if (!box) return;
      var on = box.classList.contains(CHECKED[0]);
      (on ? CHECKED : UNCHECKED).forEach(function (c) {
        box.classList.remove(c);
      });
      (on ? UNCHECKED : CHECKED).forEach(function (c) {
        box.classList.add(c);
      });
    }

    root.addEventListener('click', function (e) {
      if (
        e.defaultPrevented ||
        e.button !== 0 ||
        e.metaKey ||
        e.ctrlKey ||
        e.shiftKey ||
        e.altKey
      )
        return;
      var link = e.target.closest('a[data-mk-filter]');
      if (!link || !root.contains(link)) return;
      e.preventDefault();
      if (link.hasAttribute('data-mk-row')) tickNow(link);
      navigate(link.href, true);
    });

    root.addEventListener('submit', function (e) {
      var form = e.target.closest('form[data-mk-filter]');
      if (!form) return;
      e.preventDefault();
      var data = new FormData(form);
      // The segment buttons carry their value on the button that was pressed.
      if (e.submitter && e.submitter.name)
        data.append(e.submitter.name, e.submitter.value);
      var query = new URLSearchParams(data).toString();
      navigate(window.location.pathname + (query ? '?' + query : ''), true);
    });

    window.addEventListener('popstate', function () {
      navigate(window.location.href, false);
    });
  }

  function setUpResize() {
    var box = document.getElementById('mk-globe-box');
    var bar = document.getElementById('mk-globe-resize');
    if (!box || !bar) return;

    var KEY = 'marketplace.network.globeHeight';
    var MIN = 160;

    function max() {
      return Math.max(MIN, Math.round(window.innerHeight * 0.85));
    }
    function clamp(h) {
      return Math.min(max(), Math.max(MIN, Math.round(h)));
    }
    function remember(h) {
      try {
        if (h == null) window.localStorage.removeItem(KEY);
        else window.localStorage.setItem(KEY, String(h));
      } catch (e) {
        /* private window or storage blocked: the size just is not kept */
      }
    }
    function setHeight(h) {
      box.style.height = clamp(h) + 'px';
    }

    try {
      var saved = parseInt(window.localStorage.getItem(KEY), 10);
      if (saved > 0) setHeight(saved);
    } catch (e) {
      /* as above */
    }

    var startY = 0;
    var startH = 0;

    bar.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      startY = e.clientY;
      startH = box.getBoundingClientRect().height;
      bar.setPointerCapture(e.pointerId);
      document.body.style.userSelect = 'none';
    });
    bar.addEventListener('pointermove', function (e) {
      if (!bar.hasPointerCapture(e.pointerId)) return;
      setHeight(startH + (e.clientY - startY));
    });
    function finish(e) {
      if (!bar.hasPointerCapture(e.pointerId)) return;
      bar.releasePointerCapture(e.pointerId);
      document.body.style.userSelect = '';
      remember(Math.round(box.getBoundingClientRect().height));
    }
    bar.addEventListener('pointerup', finish);
    bar.addEventListener('pointercancel', finish);

    bar.addEventListener('dblclick', function () {
      box.style.height = '';
      remember(null);
    });

    // Arrow keys, so the bar is not mouse-only.
    bar.addEventListener('keydown', function (e) {
      var step = e.shiftKey ? 80 : 20;
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault();
      setHeight(
        box.getBoundingClientRect().height +
          (e.key === 'ArrowDown' ? step : -step),
      );
      remember(Math.round(box.getBoundingClientRect().height));
    });
  }

  function start() {
    var root = document.getElementById('mk-network');
    if (!root) return;
    setUpFiltering(root);
    setUpResize();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
