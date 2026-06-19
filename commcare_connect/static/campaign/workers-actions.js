// workers-actions.js — fetch wrappers for worker mutations (CSRF-aware).
(function () {
  function csrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf(),
        Accept: 'application/json',
      },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (!r.ok)
        return r.json().then(function (e) {
          throw new Error(e.error || r.status);
        });
      return r.json();
    });
  }
  window.campaignActions = {
    setPayStatus: function (workerIds, status) {
      return post('/campaign/api/payments/set-status/', {
        worker_ids: workerIds,
        status: status,
      });
    },
    queuePay: function (workerId, approvedCount) {
      return post('/campaign/api/payments/' + workerId + '/queue/', {
        approved_count: approvedCount,
      });
    },
    setKyc: function (workerId, status) {
      return post('/campaign/api/kyc/' + workerId + '/status/', {
        status: status,
      });
    },
    resolveDuplicate: function (workerId, keep) {
      return post('/campaign/api/kyc/' + workerId + '/resolve-duplicate/', {
        keep: keep,
      });
    },
    saveInvestigation: function (workerId, inv) {
      return post('/campaign/api/kyc/' + workerId + '/investigation/', inv);
    },
  };
})();
