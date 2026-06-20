// data-api.js — fetches the seeded campaign data and exposes window.CUT_DATA
// with the same shape + helper functions the prototype's data.js provided.
(function () {
  function money(n) {
    return '₦' + Math.round(n).toLocaleString('en-US');
  }
  function moneyK(n) {
    return n >= 1e6
      ? '₦' + (n / 1e6).toFixed(2) + 'M'
      : '₦' + Math.round(n / 1e3) + 'K';
  }
  function num(n) {
    return Math.round(n).toLocaleString('en-US');
  }
  function summarize(workers) {
    const s = {
      total: workers.length,
      kyc: { approved: 0, pending: 0, rejected: 0, review: 0 },
      pay: { paid: 0, approved: 0, pending: 0, rejected: 0, hold: 0 },
      amount: 0,
      paidAmount: 0,
      pendingAmount: 0,
      duplicates: 0,
      female: 0,
      flagged: 0,
    };
    workers.forEach(function (w) {
      if (s.kyc[w.kyc] !== undefined) s.kyc[w.kyc]++;
      if (s.pay[w.pay] !== undefined) s.pay[w.pay]++;
      s.amount += w.amount;
      if (w.pay === 'paid') s.paidAmount += w.amount;
      if (w.pay === 'pending' || w.pay === 'approved')
        s.pendingAmount += w.amount;
      if (w.duplicate) s.duplicates++;
      if (w.gender === 'F') s.female++;
      if (w.fraudRules && w.fraudRules.length) s.flagged++;
    });
    return s;
  }
  // small self-contained PRNG so the daily breakdown is stable per worker id
  function dailyForWorker(w) {
    let seed = 0;
    for (let i = 0; i < w.id.length; i++)
      seed = (seed * 31 + w.id.charCodeAt(i)) >>> 0;
    function rng() {
      seed = (seed + 0x6d2b79f5) >>> 0;
      let t = seed;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }
    const out = [];
    for (let i = 0; i < w.daysWorked; i++) {
      const dayNum = 18 + i;
      const date = dayNum > 31 ? 'Jun ' + (dayNum - 31) : 'May ' + dayNum;
      const approved = i < w.daysApproved;
      out.push({
        date: date,
        units: 40 + Math.floor(rng() * 81),
        rate: w.rate,
        amount: w.rate,
        status: approved
          ? 'approved'
          : w.pay === 'rejected'
          ? 'rejected'
          : 'pending',
        flag:
          rng() < 0.08
            ? rng() < 0.5
              ? 'Low units logged'
              : 'Overlapping GPS'
            : null,
      });
    }
    return out;
  }

  // Fetch a filtered/paginated page of full worker objects from the server,
  // instead of relying on the (now first-page-only) D.WORKERS bootstrap list.
  // params: { page, page_size, q, kyc, pay, role, region, fraud }. The current
  // page's ?campaign=<code> is appended so the same campaign the bootstrap
  // selected is read.
  function fetchWorkers(params) {
    params = params || {};
    const qs = new URLSearchParams();
    const keys = [
      'page',
      'page_size',
      'q',
      'kyc',
      'pay',
      'role',
      'region',
      'fraud',
    ];
    keys.forEach(function (k) {
      const v = params[k];
      if (v !== undefined && v !== null && v !== '' && v !== 'all')
        qs.set(k, v);
    });
    const code = new URLSearchParams(window.location.search).get('campaign');
    if (code) qs.set('campaign', code);
    const query = qs.toString();
    const url = '/campaign/api/workers/' + (query ? '?' + query : '');
    return fetch(url, { headers: { Accept: 'application/json' } }).then(
      function (r) {
        if (!r.ok) throw new Error('workers ' + r.status);
        return r.json();
      },
    );
  }

  window.campaignLoadData = function () {
    // Carry a ?campaign=<code> from the page URL through to the bootstrap, so a
    // specific campaign (e.g. the national one) can be selected for display.
    const code = new URLSearchParams(window.location.search).get('campaign');
    const url =
      '/campaign/api/bootstrap/' +
      (code ? '?campaign=' + encodeURIComponent(code) : '');
    return fetch(url, {
      headers: { Accept: 'application/json' },
    })
      .then(function (r) {
        if (!r.ok) throw new Error('bootstrap ' + r.status);
        return r.json();
      })
      .then(function (body) {
        const d = body.campaign;
        d.summarize = summarize;
        d.fetchWorkers = fetchWorkers;
        d.money = money;
        d.moneyK = moneyK;
        d.num = num;
        d.dailyForWorker = dailyForWorker;
        window.CUT_DATA = d;
        return { user: body.user };
      });
  };
})();
