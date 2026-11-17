/* The owner's controls on a scenario group's page: save the question and summary, and re-baseline. The addresses come in as
 * typed JSON (`#group-data`); nothing is built from model text as HTML. */
(function () {
  'use strict';
  var node = document.getElementById('group-data');
  var data = node ? JSON.parse(node.textContent) : {};
  var csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';

  function send(url, method, body, stateId, done) {
    var el = document.getElementById(stateId);
    el.textContent = 'Saving…';
    fetch(url, {
      method: method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify(body || {}),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (j) {
        el.textContent = j.error ? j.error : 'Saved.';
        if (!j.error && done) done();
      });
  }

  var save = document.getElementById('g-save');
  if (save)
    save.addEventListener('click', function () {
      send(
        data.group,
        'PATCH',
        {
          question: document.getElementById('g-question').value,
          summary: document.getElementById('g-summary').value,
        },
        'g-state',
        function () {
          location.reload();
        },
      );
    });

  var baseline = document.getElementById('g-baseline');
  if (baseline)
    baseline.addEventListener('click', function () {
      document.getElementById('b-state').textContent =
        'Computing today\u2019s numbers: about a minute…';
      send(data.baseline, 'POST', {}, 'b-state', function () {
        location.reload();
      });
    });

  // "Compare against": choosing a scenario reloads the page with it as the reference.
  var pick = document.getElementById('ref-pick');
  if (pick)
    pick.addEventListener('change', function () {
      var u = new URL(window.location.href);
      u.searchParams.set('ref', pick.value);
      window.location.assign(u.toString());
    });
})();
