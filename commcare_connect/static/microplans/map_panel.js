/*
 * Microplans map Layers/Inspector panel.
 *
 * A self-contained card docked on the map with two tabs:
 *   - Layers:  a registry of toggleable map overlays (footprints, admin
 *              boundaries, service delivery). Each layer owns a row + an optional
 *              body (revealed when the layer is on, e.g. the SD opp picker or an
 *              admin level switch).
 *   - Inspect: a single slot showing whatever the user last clicked on the map
 *              (a work area + its group, an admin boundary, …). Replaces the old
 *              cursor-following Mapbox popup so the map stays readable.
 *
 * Vanilla (no Alpine), uses window.Microplans helpers, injects its own styles
 * once. Built to be dropped into any microplans map page (review.html today).
 *
 *   const panel = MicroplansMapPanel.create({ map, mount });
 *   const fp = panel.registerLayer({ id:'footprints', label:'Building footprints',
 *       color:'#f59e0b', onToggle:(on)=>{...} });
 *   fp.setMeta('1,024 buildings'); fp.setEnabled(true);
 *   panel.setInspect(htmlString); panel.showTab('inspect');
 */
(function () {
  'use strict';

  const STYLE_ID = 'mp-map-panel-styles';
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = `
      .mp-panel{position:absolute;top:.5rem;right:.5rem;z-index:6;width:248px;background:rgba(255,255,255,.97);
        border:1px solid #e5e7eb;border-radius:.6rem;box-shadow:0 10px 30px -12px rgba(16,18,34,.45);
        font-size:.72rem;color:#111827;overflow:hidden}
      .mp-panel-tabs{display:flex;border-bottom:1px solid #eceef1}
      .mp-panel-tabs button{flex:1;border:0;background:transparent;cursor:pointer;padding:.5rem .25rem;
        font:700 .66rem/1 inherit;letter-spacing:.04em;color:#8a90a0;text-transform:uppercase}
      .mp-panel-tabs button.on{color:#2d36b3;box-shadow:inset 0 -2px 0 #3843d0}
      .mp-panel-sec{padding:.55rem .6rem;max-height:340px;overflow:auto}
      .mp-panel-sec[hidden]{display:none}
      .mp-lyr{display:flex;align-items:center;gap:.5rem;padding:.45rem 0;border-bottom:1px solid #f1f2f5}
      .mp-lyr:last-child{border-bottom:0}
      .mp-lyr-sw{width:10px;height:10px;border-radius:50%;flex:0 0 auto}
      .mp-lyr-nm{font-weight:600}
      .mp-lyr-badge{font:700 .56rem/1 inherit;letter-spacing:.04em;text-transform:uppercase;padding:1px 5px;
        border-radius:4px;background:#eef4ff;color:#2563eb;border:1px solid #dbe6ff;margin-left:.3rem}
      .mp-lyr-meta{font-size:.62rem;color:#9ca3af;margin-top:1px}
      .mp-lyr-sw-toggle{margin-left:auto;width:32px;height:19px;border-radius:999px;background:#d6d9e0;
        position:relative;flex:0 0 auto;cursor:pointer;transition:background .15s}
      .mp-lyr-sw-toggle i{position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;
        background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:left .15s}
      .mp-lyr-sw-toggle.on{background:#2563eb}.mp-lyr-sw-toggle.on i{left:15px}
      .mp-lyr-body{padding:.4rem 0 .15rem 1.4rem}
      .mp-lyr-body[hidden]{display:none}
      .mp-inspect-empty{color:#9ca3af;text-align:center;padding:1rem .4rem;line-height:1.5}
      .mp-panel a{color:#3843d0}
    `;
    document.head.appendChild(s);
  }

  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function setContent(node, htmlOrNode) {
    node.innerHTML = '';
    if (htmlOrNode == null) return;
    if (typeof htmlOrNode === 'string') node.innerHTML = htmlOrNode;
    else node.appendChild(htmlOrNode);
  }

  function create(opts) {
    injectStyles();
    const map = opts.map;
    const mount = opts.mount;
    const esc =
      (window.Microplans && window.Microplans.esc) ||
      ((s) => String(s == null ? '' : s));

    const root = el('div', 'mp-panel');
    root.innerHTML = `
      <div class="mp-panel-tabs">
        <button type="button" data-tab="layers" class="on">Layers</button>
        <button type="button" data-tab="inspect">Inspect</button>
      </div>
      <div class="mp-panel-sec" data-sec="layers"></div>
      <div class="mp-panel-sec" data-sec="inspect" hidden>
        <div class="mp-inspect-empty">Click a feature on the map to inspect it.</div>
      </div>`;
    mount.appendChild(root);

    const layersSec = root.querySelector('[data-sec="layers"]');
    const inspectSec = root.querySelector('[data-sec="inspect"]');
    const tabBtns = root.querySelectorAll('.mp-panel-tabs button');

    function showTab(name) {
      tabBtns.forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
      layersSec.hidden = name !== 'layers';
      inspectSec.hidden = name !== 'inspect';
    }
    tabBtns.forEach((b) =>
      b.addEventListener('click', () => showTab(b.dataset.tab)),
    );

    // ---- layer registry ----
    function registerLayer(cfg) {
      const row = el('div', 'mp-lyr');
      const badge = cfg.badge
        ? `<span class="mp-lyr-badge">${esc(cfg.badge)}</span>`
        : '';
      row.innerHTML = `
        <span class="mp-lyr-sw" style="background:${esc(
          cfg.color || '#9ca3af',
        )}"></span>
        <div style="min-width:0">
          <div class="mp-lyr-nm">${esc(cfg.label)}${badge}</div>
          <div class="mp-lyr-meta"></div>
        </div>
        <button type="button" class="mp-lyr-sw-toggle" data-testid="layer-toggle-${esc(
          cfg.id || '',
        )}" aria-label="toggle ${esc(cfg.label)}"><i></i></button>`;
      const metaEl = row.querySelector('.mp-lyr-meta');
      const toggleEl = row.querySelector('.mp-lyr-sw-toggle');
      const bodyEl = el('div', 'mp-lyr-body');
      bodyEl.hidden = true;
      layersSec.appendChild(row);
      layersSec.appendChild(bodyEl);

      let on = false;
      function render() {
        toggleEl.classList.toggle('on', on);
        bodyEl.hidden = !on || !bodyEl.childNodes.length;
      }
      function setEnabled(v, fireCallback) {
        v = !!v;
        if (v === on) {
          render();
          return;
        }
        on = v;
        render();
        if (fireCallback !== false && typeof cfg.onToggle === 'function')
          cfg.onToggle(on, handle);
      }
      toggleEl.addEventListener('click', () => setEnabled(!on));

      if (cfg.meta) metaEl.innerHTML = esc(cfg.meta);
      if (cfg.body) setContent(bodyEl, cfg.body);

      const handle = {
        id: cfg.id,
        get on() {
          return on;
        },
        setEnabled, // setEnabled(true) fires onToggle; setEnabled(true,false) suppresses it
        setMeta(text) {
          metaEl.innerHTML = esc(text || '');
        },
        setMetaHTML(html) {
          metaEl.innerHTML = html || '';
        },
        setBody(htmlOrNode) {
          setContent(bodyEl, htmlOrNode);
          render();
        },
        bodyEl,
        row,
      };
      render();
      return handle;
    }

    // ---- inspector ----
    function setInspect(htmlOrNode, autoshow) {
      setContent(inspectSec, htmlOrNode);
      if (autoshow !== false) showTab('inspect');
    }
    function clearInspect() {
      inspectSec.innerHTML =
        '<div class="mp-inspect-empty">Click a feature on the map to inspect it.</div>';
    }

    return {
      el: root,
      registerLayer,
      setInspect,
      clearInspect,
      showTab,
      destroy() {
        root.remove();
      },
    };
  }

  window.MicroplansMapPanel = { create };
})();
