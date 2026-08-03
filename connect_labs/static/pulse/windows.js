/**
 * Pulse drill-down windows — a partner, and a worker inside it.
 *
 * These are overlays on the running display, not pages. The map keeps
 * animating underneath, the ticker keeps arriving, and closing a window returns
 * you to exactly the frame you left. That is the whole reason they are not
 * routes: the value of this screen is that it is live, and navigating away from
 * it to read a detail throws that away.
 *
 * Both windows re-fetch on a timer while open, so a window left up during a
 * demo keeps telling the truth rather than freezing at the moment it opened.
 *
 * **Workers are opaque.** Connect publishes `username` already hashed, so a
 * worker here is an anonymous identifier with a delivery record attached. There
 * is no name or phone in Pulse to show, by construction — which is what makes a
 * per-worker view safe on a funder-facing screen.
 *
 * Depends on window.PulseCards.helpers for formatting so the windows and the
 * cards can never disagree about how a figure is written.
 */
(function (global) {
  'use strict';

  const { nf, usd, usdCompact, nearestTown } = global.PulseCards.helpers;

  const REFRESH_MS = 12000;
  const esc = (s) =>
    String(s == null ? '' : s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[c],
    );
  const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
  const ago = (ts) => {
    if (!ts) return 'never';
    const m = (Date.now() / 1000 - ts) / 60;
    if (m < 60) return Math.max(1, Math.round(m)) + 'm ago';
    if (m < 1440) return Math.round(m / 60) + 'h ago';
    return Math.round(m / 1440) + 'd ago';
  };

  /* A stack, so a worker window opens ON a partner window and closing it
     returns to the partner rather than to the map. */
  const stack = [];

  function chart(series, valueOf) {
    if (!series || !series.length)
      return '<div class="pulse-win-note">No history in range.</div>';
    const max = Math.max(...series.map(valueOf), 1);
    const last = series.length - 1;
    return (
      '<div class="pulse-win-chart">' +
      series
        .map(
          (d, i) =>
            `<i style="height:${Math.max(
              (valueOf(d) / max) * 100,
              valueOf(d) ? 2 : 0,
            ).toFixed(1)}%"${
              // Trust the server's flag when the series carries one. OR-ing
              // `i === last` over it hatches a COMPLETED week as incomplete
              // every Monday morning, before any work has landed in the new
              // week. Flagless series (the per-partner and per-opportunity
              // sparks are plain counts) are padded to end at the current week,
              // so there the last bucket genuinely is the partial one.
              ('partial' in d ? d.partial : i === last)
                ? ' data-partial="1"'
                : ''
            } title="${nf.format(valueOf(d))}"></i>`,
        )
        .join('') +
      '</div>'
    );
  }

  function kpis(cells) {
    return (
      '<div class="pulse-win-kpis">' +
      cells
        .map(
          (c) =>
            `<div><span class="wk-l">${esc(c[0])}</span><div class="wk-v${
              c[2] ? ' gold' : ''
            }">${c[1]}</div></div>`,
        )
        .join('') +
      '</div>'
    );
  }

  function close(depth) {
    while (stack.length > depth) {
      const win = stack.pop();
      clearInterval(win.timer);
      win.el.remove();
      win.scrim.remove();
    }
    if (!stack.length) document.body.style.overflow = '';
  }

  function frame(depth, title, subtitle, isSlug) {
    close(depth);
    const scrim = document.createElement('div');
    scrim.className = 'pulse-scrim';
    scrim.dataset.depth = String(depth + 1);
    const el = document.createElement('div');
    el.className = 'pulse-win';
    el.dataset.depth = String(depth + 1);
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.innerHTML = `<div class="pulse-win-head">
         <div class="pulse-win-title">
           <h2${isSlug ? ' data-slug="1"' : ''}>${esc(title)}</h2>
           <div class="pulse-win-sub">${subtitle}</div>
         </div>
         <button class="pulse-win-close" type="button">Close · Esc</button>
       </div>
       <div class="pulse-win-body"></div>`;
    // Clicking the backdrop closes only the layer that owns it.
    scrim.addEventListener('click', () => close(depth));
    el.querySelector('.pulse-win-close').addEventListener('click', () =>
      close(depth),
    );
    document.body.appendChild(scrim);
    document.body.appendChild(el);
    document.body.style.overflow = 'hidden';
    el.querySelector('.pulse-win-close').focus();
    return {
      el,
      scrim,
      body: el.querySelector('.pulse-win-body'),
      timer: null,
    };
  }

  async function fetchJSON(store, path, params) {
    const res = await fetch(store._url(path, params));
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  /* One engagement reads as a statement; several read as a grid you can pick
     from. Rendering a single opportunity as a one-row list is chrome around a
     fact, and rendering ninety-one as a paragraph is unreadable — the partner
     window has to do both, because real partners span that whole range. */
  function opportunities(store, d, selected) {
    const rows = d.opportunities || [];
    if (!rows.length) return '';

    const spark = (o) => {
      const v = o.spark || [];
      if (!v.length) return '';
      const max = Math.max(...v, 1);
      const last = v.length - 1;
      return (
        '<div class="pulse-opp-spark">' +
        v
          .map(
            (n, i) =>
              '<i style="height:' +
              (n ? Math.max((n / max) * 100, 6).toFixed(1) : 0) +
              '%"' +
              (i === last ? ' data-partial="1"' : '') +
              '></i>',
          )
          .join('') +
        '</div>'
      );
    };

    const where = (o) =>
      [
        o.service_name,
        (store.summary?.labels?.countries || {})[o.country] || o.country,
      ]
        .filter(Boolean)
        .join(' \u00b7 ');

    if (rows.length === 1) {
      const o = rows[0];
      return (
        '<div class="pulse-win-sect"><span class="pulse-lbl">Its opportunity</span>' +
        '<div class="pulse-opp-solo">' +
        '<span class="os-name">' +
        esc(o.name) +
        '</span>' +
        '<span class="os-f">' +
        esc(where(o)) +
        '</span>' +
        '<span class="os-f">services <b>' +
        nf.format(o.visits) +
        '</b></span>' +
        '<span class="os-f">paid <b>' +
        usdCompact(o.usd_total) +
        '</b></span>' +
        '<span class="os-f">approved <b>' +
        pct(o.approval_rate) +
        '</b></span>' +
        '<span class="os-f">workers <b>' +
        nf.format(o.workers) +
        '</b></span>' +
        '<span class="os-f">' +
        (o.active
          ? 'active'
          : 'ended' + (o.end_date ? ' ' + esc(o.end_date) : '')) +
        ' \u00b7 last delivery ' +
        esc(ago(o.last_ts)) +
        '</span>' +
        '</div></div>'
      );
    }

    return (
      '<div class="pulse-win-sect"><span class="pulse-lbl">' +
      nf.format(rows.length) +
      ' opportunities \u00b7 click one to narrow this window' +
      (selected ? ' \u00b7 showing one' : '') +
      '</span>' +
      '<div class="pulse-opp-grid">' +
      rows
        .map(
          (o) =>
            '<div class="pulse-opp" role="button" tabindex="0" data-opp="' +
            o.id +
            '" aria-pressed="' +
            (selected === o.id ? 'true' : 'false') +
            '" title="' +
            esc(o.name) +
            '">' +
            '<div class="pulse-opp-name">' +
            esc(o.name) +
            '</div>' +
            '<div class="pulse-opp-meta">' +
            '<i class="pulse-opp-dot" data-live="' +
            (o.last_ts ? '1' : '0') +
            '"></i>' +
            esc(where(o)) +
            '</div>' +
            '<div class="pulse-opp-figs">' +
            '<div><span class="of-l">Services</span><span class="of-v">' +
            nf.format(o.visits) +
            '</span></div>' +
            '<div><span class="of-l">Paid</span><span class="of-v gold">' +
            usdCompact(o.usd_total) +
            '</span></div>' +
            '<div><span class="of-l">Appr</span><span class="of-v">' +
            pct(o.approval_rate) +
            '</span></div>' +
            '</div>' +
            spark(o) +
            '</div>',
        )
        .join('') +
      '</div></div>'
    );
  }

  /* ── partner window ──────────────────────────────────────────────── */
  function openPartner(store, slug) {
    const depth = 0;
    const win = frame(depth, 'Loading…', '', false);
    stack.push(win);

    let sort = { key: 'last_ts', dir: -1 };
    // Which engagement the window is narrowed to, if any.
    let selectedOpp = null;

    const paint = (d) => {
      const p = d.partner || {};
      const m = d.money || {};
      const sc = d.scope || {};
      win.el.querySelector('h2').textContent = p.name || p.slug;
      win.el.querySelector('h2').toggleAttribute('data-slug', !p.named);
      win.el.querySelector('.pulse-win-sub').innerHTML =
        (p.workspace && p.workspace !== p.name
          ? `workspace <b>${esc(p.workspace)}</b> · `
          : '') +
        (p.funder ? `funded by <b>${esc(p.funder)}</b> · ` : '') +
        `${nf.format(sc.opportunities || 0)} opportunities · ` +
        `${nf.format(sc.programs || 0)} programmes`;

      const rows = (d.workers || []).slice().sort((a, b) => {
        const x = a[sort.key],
          y = b[sort.key];
        if (x == null) return 1;
        if (y == null) return -1;
        return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
      });

      const th = (key, label, cls) =>
        `<th data-k="${key}" class="${cls || ''}" aria-sort="${
          sort.key === key
            ? sort.dir === 1
              ? 'ascending'
              : 'descending'
            : 'none'
        }">${label}</th>`;

      win.body.innerHTML =
        kpis([
          ['Paid out', usdCompact(m.total_paid || 0), true],
          ['Per service', m.rate == null ? '—' : usd(m.rate)],
          ['Services', nf.format(sc.lifetime_visits || 0)],
          ['Units of work', nf.format(m.works || 0)],
          ['Workers', nf.format(d.worker_count || 0)],
        ]) +
        opportunities(store, d, selectedOpp) +
        `<div class="pulse-win-sect">
           <span class="pulse-lbl">Delivery, last 26 weeks${
             selectedOpp ? ' · this opportunity' : ''
           }</span>
           ${chart(d.weekly || [], (x) => x.works)}
         </div>` +
        `<div class="pulse-win-sect">
           <span class="pulse-lbl">Workers · click one to open their record</span>
           <table class="pulse-roster">
             <thead><tr>
               ${th('worker', 'Worker')}
               ${th('works', 'Work', 'rw-num')}
               ${th('approval_rate', 'Approved', 'rw-num')}
               ${th('flag_rate', 'Flagged', 'rw-num')}
               ${th('usd', 'Earned', 'rw-num')}
               ${th('last_ts', 'Last seen', 'rw-num')}
             </tr></thead>
             <tbody>${
               rows.length
                 ? rows
                     .map((w) => {
                       const lvl =
                         w.approval_rate == null
                           ? 0
                           : w.approval_rate < 0.5
                           ? 2
                           : w.approval_rate < 0.8
                           ? 1
                           : 0;
                       return `<tr tabindex="0" data-w="${esc(w.worker)}">
                         <td class="rw-id">${esc(w.worker)}</td>
                         <td class="rw-num">${nf.format(w.works)}</td>
                         <td class="rw-num">${pct(
                           w.approval_rate,
                         )}<span class="pulse-meter"><i data-low="${lvl}" style="width:${(
                           (w.approval_rate || 0) * 100
                         ).toFixed(0)}%"></i></span></td>
                         <td class="rw-num">${pct(w.flag_rate)}</td>
                         <td class="rw-num">${usdCompact(w.usd)}</td>
                         <td class="rw-num">${ago(w.last_ts)}</td>
                       </tr>`;
                     })
                     .join('')
                 : '<tr><td colspan="6">No workers have delivered for this partner in range.</td></tr>'
             }</tbody>
           </table>
           ${
             d.workers_truncated
               ? `<div class="pulse-win-note">Showing the ${nf.format(
                   d.worker_count,
                 )} most recently active workers — there are more.</div>`
               : ''
           }
         </div>` +
        `<div class="pulse-win-note">
           <span class="pulse-win-live"><i></i>Updating live</span> ·
           Workers are identified by Connect's own opaque ID. No names or phone
           numbers exist in Pulse. Money is accrued against approved work, and
           counts both the worker's payout and the organisation's share.
         </div>`;

      win.body.querySelectorAll('.pulse-roster th').forEach((h) =>
        h.addEventListener('click', () => {
          const k = h.dataset.k;
          sort = { key: k, dir: sort.key === k ? -sort.dir : -1 };
          paint(win.last);
        }),
      );
      /* Selecting an engagement re-fetches rather than filtering what is
         held: the roster, the chart and the KPIs are all server-side
         aggregates, so a client-side filter would leave the partner's totals
         above one opportunity's workers. */
      const pick = (el2) => {
        const id = Number(el2.dataset.opp);
        selectedOpp = selectedOpp === id ? null : id;
        load();
      };
      win.body.querySelectorAll('.pulse-opp[data-opp]').forEach((el2) => {
        el2.addEventListener('click', () => pick(el2));
        el2.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            pick(el2);
          }
        });
      });

      const open = (tr) =>
        openWorker(store, tr.dataset.w, p.slug, p.name || p.slug);
      win.body
        .querySelectorAll('.pulse-roster tbody tr[data-w]')
        .forEach((tr) => {
          tr.addEventListener('click', () => open(tr));
          tr.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              open(tr);
            }
          });
        });
    };

    const load = async () => {
      try {
        const params = { org: slug };
        if (selectedOpp) params.opportunity = selectedOpp;
        const d = await fetchJSON(store, '/api/partner/', params);
        win.last = d;
        paint(d);
      } catch (err) {
        win.body.innerHTML = `<div class="pulse-win-note">Could not load this partner (${esc(
          err.message,
        )}).</div>`;
      }
    };
    load();
    win.timer = setInterval(load, REFRESH_MS);
  }

  /* ── worker window ───────────────────────────────────────────────── */
  function openWorker(store, worker, orgSlug, orgName) {
    const depth = stack.length;
    const win = frame(depth, worker, '', true);
    stack.push(win);

    const paint = (d) => {
      const t = d.totals || {};
      win.el.querySelector('.pulse-win-sub').innerHTML = `worker at <b>${esc(
        orgName,
      )}</b> · last delivered ${esc(ago(t.last_ts))}`;

      const towns = {};
      for (const r of d.recent || []) {
        if (r.lat == null) continue;
        const town = nearestTown(r.lat, r.lon);
        const key = town.t + (town.c ? ' ' + town.c : '');
        towns[key] = (towns[key] || 0) + 1;
      }
      const topTowns = Object.entries(towns)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6);
      const flags = Object.entries(d.by_flag || {}).sort((a, b) => b[1] - a[1]);
      const labels =
        (store.summary && store.summary.labels && store.summary.labels.flags) ||
        {};

      win.body.innerHTML =
        kpis([
          ['Earned', usdCompact(t.usd || 0), true],
          ['Units of work', nf.format(t.works || 0)],
          ['Approved', pct(t.approval_rate)],
          ['Flagged', pct(t.flag_rate)],
          ['Visits in window', nf.format(t.events || 0)],
        ]) +
        `<div class="pulse-win-sect">
           <span class="pulse-lbl">Their delivery, last 26 weeks</span>
           ${chart(d.weekly || [], (x) => x.works)}
         </div>` +
        `<div class="pulse-win-sect">
           <span class="pulse-lbl">What they deliver</span>
           <div class="rank">${
             (d.by_service || [])
               .map((s) => {
                 const mx = d.by_service[0].works || 1;
                 return `<div class="rrow"><span class="rn">${esc(
                   s.name,
                 )}</span>
                   <span class="rv">${nf.format(s.works)}</span>
                   <span class="rb"><i style="width:${(
                     (s.works / mx) *
                     100
                   ).toFixed(
                     1,
                   )}%;background:var(--light-dim)"></i></span></div>`;
               })
               .join('') ||
             '<div class="pulse-win-note">No work in range.</div>'
           }</div>
         </div>` +
        `<div class="pulse-win-sect">
           <span class="pulse-lbl">Where they work</span>
           <div class="rank">${
             topTowns
               .map(
                 ([t2, n]) =>
                   `<div class="rrow"><span class="rn">${esc(t2)}</span>
                     <span class="rv">${nf.format(n)}</span>
                     <span class="rb"><i style="width:${(
                       (n / topTowns[0][1]) *
                       100
                     ).toFixed(1)}%;background:var(--c-1)"></i></span></div>`,
               )
               .join('') ||
             '<div class="pulse-win-note">No located visits in range.</div>'
           }</div>
         </div>` +
        (flags.length
          ? `<div class="pulse-win-sect">
               <span class="pulse-lbl">Why their work gets flagged</span>
               <div class="rank">${flags
                 .map(
                   ([k, n]) =>
                     `<div class="rrow"><span class="rn">${esc(
                       labels[k] || k,
                     )}</span>
                       <span class="rv">${nf.format(n)}</span>
                       <span class="rb"><i style="width:${(
                         (n / flags[0][1]) *
                         100
                       ).toFixed(
                         1,
                       )}%;background:var(--warn)"></i></span></div>`,
                 )
                 .join('')}</div>
             </div>`
          : '') +
        `<div class="pulse-win-note">
           <span class="pulse-win-live"><i></i>Updating live</span> ·
           This is an opaque worker identifier from Connect, not a name. Visit
           locations are shown as the nearest town, never as household
           coordinates.
         </div>`;
    };

    const load = async () => {
      try {
        const d = await fetchJSON(store, '/api/worker/', {
          w: worker,
          org: orgSlug,
        });
        paint(d);
      } catch (err) {
        win.body.innerHTML = `<div class="pulse-win-note">Could not load this worker (${esc(
          err.message,
        )}).</div>`;
      }
    };
    load();
    win.timer = setInterval(load, REFRESH_MS);
  }

  // Esc closes the topmost layer only, so a worker window returns you to the
  // partner rather than dropping you all the way back to the map.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && stack.length) {
      e.stopPropagation();
      close(stack.length - 1);
    }
  });

  global.PulseWindows = {
    openPartner,
    openWorker,
    close: () => close(0),
    isOpen: () => stack.length > 0,
  };
})(window);
