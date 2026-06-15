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

    // Comparability — is the control a fair counterfactual? When this plan carries
    // two arms, POST their per-arm sampling stats to the shared ArmComparabilityView,
    // which runs the same PSU/SMD engine the study-group page uses and returns the
    // rendered _arm_comparability.html partial. We just inject it — ONE comparison
    // engine + ONE panel markup across both surfaces (DRY).
    async function updateComparability(stats) {
      const wrap = $('sampling-comparability'),
        body = $('comparability-body');
      if (!wrap || !body) return;
      const arms = new Set((stats || []).map((s) => s.arm));
      if (arms.size < 2 || !COMPARABILITY_URL) {
        wrap.classList.add('hidden');
        return;
      }
      try {
        const r = await Microplans.post(
          COMPARABILITY_URL,
          { stats },
          { csrf: CSRF },
        );
        if (!r || r.status !== 'ok' || !r.html) {
          wrap.classList.add('hidden');
          return;
        }
        body.innerHTML = r.html;
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
