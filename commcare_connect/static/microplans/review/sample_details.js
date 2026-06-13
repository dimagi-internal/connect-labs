(function () {
  'use strict';
  window.MPReview = window.MPReview || {};
  // "Sample details" rail rendering: per-arm stats, the "why this sample"
  // rationale, source counts, the arm-colour paint expression, and the
  // comparability panel. Pure presentation from engine stats (no map / draw /
  // plan state), so it takes only small helpers + URLs as deps.
  window.MPReview.sampleDetails = function (deps) {
    const $ = deps.$;
    const esc = deps.esc;
    const ARM_COLOR = deps.ARM_COLOR;
    const COMPARABILITY_URL = deps.COMPARABILITY_URL;
    const CSRF = deps.CSRF;

    function renderSourceCounts(stats) {
      const totals = {};
      (stats || []).forEach((s) => {
        Object.entries(s.source_counts || {}).forEach(([k, v]) => {
          totals[k] = (totals[k] || 0) + v;
        });
      });
      document.querySelectorAll('.src-count').forEach((el) => {
        const n = totals[el.dataset.src];
        el.textContent = n == null ? '' : n.toLocaleString();
      });
    }

    function armPaint(prop, intervention, comparison) {
      return ['match', ['get', 'arm'], 'comparison', comparison, intervention];
    }

    function renderArmStats(stats) {
      const el = $('sampling-arm-stats');
      if (!el) return;
      el.innerHTML = (stats || [])
        .map((s) => {
          const c = ARM_COLOR[s.arm] || ARM_COLOR.intervention;
          const prim = s.primaries || 0,
            alt = s.alternates || 0,
            psus = s.psus_selected || 0;
          return (
            `<div class="flex justify-between"><dt class="text-gray-500"><span style="color:${c}">●</span> ${esc(
              s.arm,
            )}</dt>` +
            `<dd class="text-gray-700 font-medium">${prim}+${alt} pins · ${psus} PSUs</dd></div>`
          );
        })
        .join('');
    }

    // "Why this sample" — turn the per-arm engine stats into a plain-language,
    // methodology-faithful explanation so a non-specialist can defend the sample.
    function renderRationale(stats) {
      const wrap = $('sampling-rationale'),
        body = $('sampling-rationale-body');
      if (!wrap || !body) return;
      if (!stats || !stats.length) {
        wrap.classList.add('hidden');
        return;
      }
      wrap.classList.remove('hidden');
      const step = (n, title, txt) =>
        `<li class="text-[10px] text-gray-600"><b>${n}. ${esc(
          title,
        )}</b> — ${txt}</li>`;
      body.innerHTML = stats
        .map((s) => {
          const c = ARM_COLOR[s.arm] || ARM_COLOR.intervention;
          const fetched = s.fetched || 0,
            kept = s.after_filters || 0;
          const tiny = s.removed_tiny_isolated || 0,
            large = s.removed_large || 0;
          const psusFormed = s.clusters_formed || 0,
            psusSel = s.psus_selected || 0;
          const prim = s.primaries || 0,
            alt = s.alternates || 0;
          const shortName = (k) =>
            k.replace(' Open Buildings', '').replace(' ML Buildings', '');
          const used =
            (s.sources_used || []).map(shortName).join(' + ') || 'all sources';
          const avail = Object.entries(s.source_counts || {})
            .map(([k, v]) => `${esc(shortName(k))} ${v.toLocaleString()}`)
            .join(', ');
          return `<div>
        <div class="text-[11px] font-medium text-gray-700"><span style="color:${c}">●</span> ${esc(
          s.arm,
        )} arm</div>
        <ol class="space-y-1 mt-1 ml-1 list-none">
          ${step(
            0,
            'Source',
            `<b>${esc(used)}</b>${
              avail ? ` &mdash; available in this area: ${avail}` : ''
            }.`,
          )}
          ${step(
            1,
            'Building frame',
            `${fetched} rooftops fetched; dropped ${tiny} tiny/isolated (&lt;9 m²) and ${large} oversized (&gt;330 m²) &rarr; <b>${kept}</b> in the frame.`,
          )}
          ${step(
            2,
            'PSUs formed',
            `grouped into <b>${psusFormed}</b> candidate clusters; under-16-building clusters merge into their nearest neighbour.`,
          )}
          ${step(
            3,
            'PPS selection',
            `<b>${psusSel}</b> PSUs selected at random, each cluster's chance proportional to its building count &mdash; denser clusters are likelier, so the sample mirrors where people actually live. These are the highlighted clusters on the map; re-running draws a fresh set.`,
          )}
          ${step(
            4,
            'Pins per PSU',
            `<b>${prim}</b> primary + <b>${alt}</b> alternate, spaced &ge;15 m apart; an alternate is the 15 m substitute when a primary roof can't be surveyed.`,
          )}
          ${step(
            5,
            'Design weight',
            `each primary carries weight = 1 &divide; its inclusion probability, so coverage estimates stay unbiased despite the unequal draw.`,
          )}
        </ol>
      </div>`;
        })
        .join('');
    }

    // Comparability — is the control a fair counterfactual? Server computes area +
    // density from the arm geometries + the sample's building counts; we show the
    // two arms side by side with a matched / not-matched flag.
    async function updateComparability(areas, stats) {
      const wrap = $('sampling-comparability'),
        body = $('comparability-body');
      if (!wrap || !body) return;
      const armSet = new Set(areas.map((a) => a.arm));
      if (armSet.size < 2 || !COMPARABILITY_URL) {
        wrap.classList.add('hidden');
        return;
      }
      const counts = {};
      (stats || []).forEach((s) => {
        counts[s.arm] = s.after_filters || 0;
      });
      try {
        const r = await Microplans.post(
          COMPARABILITY_URL,
          { areas, building_counts: counts },
          { csrf: CSRF },
        );
        if (!r || r.status !== 'ok' || !(r.arms || []).length) {
          wrap.classList.add('hidden');
          return;
        }
        const byArm = {};
        r.arms.forEach((a) => {
          byArm[a.arm] = a;
        });
        const iv = byArm.intervention || r.arms[0],
          cm = byArm.comparison || r.arms[1] || r.arms[0];
        const cell = (v) =>
          `<td class="text-right tabular-nums px-1">${esc(String(v))}</td>`;
        const badge = r.matched
          ? `<span class="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">matched</span>`
          : `<span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">not matched</span>`;
        const why =
          r.matched === false && (r.reasons || []).length
            ? ` <span class="text-gray-500">${esc(r.reasons.join('; '))}</span>`
            : '';
        body.innerHTML = `
        <table class="w-full"><thead><tr class="text-gray-500">
          <th class="text-left"></th>
          <th class="text-right px-1"><span style="color:${
            ARM_COLOR.intervention
          }">●</span> Interv.</th>
          <th class="text-right px-1"><span style="color:${
            ARM_COLOR.comparison
          }">●</span> Control</th></tr></thead>
          <tbody class="text-gray-700">
            <tr><td>Buildings</td>${cell(iv.building_count)}${cell(
              cm.building_count,
            )}</tr>
            <tr><td>Area (km²)</td>${cell(iv.area_km2)}${cell(cm.area_km2)}</tr>
            <tr><td>Density /km²</td>${cell(iv.density_per_km2)}${cell(
              cm.density_per_km2,
            )}</tr>
          </tbody></table>
        <div class="mt-1">${badge}${why}</div>`;
        wrap.classList.remove('hidden');
      } catch (e) {
        wrap.classList.add('hidden');
      }
    }

    return {
      renderSourceCounts,
      armPaint,
      renderArmStats,
      renderRationale,
      updateComparability,
    };
  };
})();
