/* The owner's controls on a scenario group's page: save the question and summary, re-baseline, reorder, remove or
 * delete a scenario, and delete the group. The addresses come in as
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
        'Computing today\u2019s numbers: about 30 seconds…';
      send(data.baseline, 'POST', {}, 'b-state', function () {
        location.reload();
      });
    });

  // The owner's controls on each card: move it earlier or later, take it out of the group, or delete it. Order is the
  // group's own list of scenario ids, so a move is that list with two neighbours swapped.
  function request(url, method, body, then) {
    return fetch(url, {
      method: method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: body ? JSON.stringify(body) : undefined,
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (j) {
        if (j.error) return window.alert(j.error);
        then();
      });
  }
  function reload() {
    window.location.reload();
  }
  Array.prototype.forEach.call(
    document.querySelectorAll('[data-testid="card-manage"] button'),
    function (btn) {
      btn.addEventListener('click', function () {
        var id = Number(btn.dataset.id);
        var name = btn.dataset.name;
        var order = (data.order || []).slice();
        var at = order.indexOf(id);
        if (btn.dataset.act === 'earlier' || btn.dataset.act === 'later') {
          var to = at + (btn.dataset.act === 'earlier' ? -1 : 1);
          if (at < 0 || to < 0 || to >= order.length) return;
          order.splice(to, 0, order.splice(at, 1)[0]);
          request(data.group, 'PATCH', { scenario_ids: order }, reload);
        } else if (btn.dataset.act === 'out') {
          request(data.scenarios + id + '/', 'PATCH', { group: null }, reload);
        } else if (btn.dataset.act === 'delete') {
          if (!window.confirm('Delete "' + name + '"? This cannot be undone.'))
            return;
          request(data.scenarios + id + '/', 'DELETE', null, reload);
        }
      });
    },
  );

  var del = document.getElementById('g-delete');
  if (del)
    del.addEventListener('click', function () {
      if (
        !window.confirm(
          'Delete this group? Its scenarios are kept, just no longer grouped.',
        )
      )
        return;
      request(data.group, 'DELETE', null, function () {
        window.location.assign(data.groups);
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
