// workers-actions.js — fetch wrappers for worker mutations (CSRF-aware).
(function () {
  function csrf() {
    // This project uses CSRF_USE_SESSIONS (+ HttpOnly cookie), so there is no
    // readable csrftoken cookie — the token is rendered into a <meta> tag by
    // app.html ({{ csrf_token }}). Fall back to the cookie just in case.
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
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
    createActivity: function (data) {
      return post('/campaign/api/activities/', data);
    },
    syncActivity: function (activityId) {
      return post('/campaign/api/activities/' + activityId + '/sync/', {});
    },
    createMicroplan: function (data) {
      return post('/campaign/api/microplans/', data);
    },
    updateMicroplan: function (microplanId, data) {
      return post('/campaign/api/microplans/' + microplanId + '/', data);
    },
    setMicroplanTarget: function (microplanId, target, goalPct) {
      return post('/campaign/api/microplans/' + microplanId + '/target/', {
        target: target,
        goalPct: goalPct,
      });
    },
    setMicroplanBudget: function (microplanId, budget) {
      return post('/campaign/api/microplans/' + microplanId + '/budget/', {
        budget: budget,
      });
    },
    inviteUser: function (data) {
      return post('/campaign/api/users/invite/', data);
    },
    setUserRole: function (username, role) {
      return post(
        '/campaign/api/users/' + encodeURIComponent(username) + '/role/',
        { role: role },
      );
    },
    setUserStatus: function (username, status) {
      return post(
        '/campaign/api/users/' + encodeURIComponent(username) + '/status/',
        { status: status },
      );
    },
  };
})();
