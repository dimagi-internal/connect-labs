/* The owner's controls on a scenario group's page: save the question and summary, re-baseline, reorder, remove or delete a
 * scenario, and delete the group. Every one is a call to `EffApi` (shared with the scenario panel), and every one is on
 * `window.EffGroup` so the canopy agent does exactly what the buttons do. The page's facts come in as typed JSON
 * (`#group-data`); nothing is built from model text as HTML. */
(function () {
  'use strict';
  var A = window.EffApi;
  var node = document.getElementById('group-data');
  var data = node ? JSON.parse(node.textContent) : {};

  function reload() {
    A.later(function () {
      window.location.reload();
    });
  }

  var ops = {
    group: function () {
      return { id: data.group_id, name: data.group_name, order: data.order };
    },
    // Move a scenario to a 1-based place, or 'earlier' / 'later'.
    move: function (id, to) {
      return A.arrange(data.group_id, A.reordered(data.order, id, to)).then(
        reload,
      );
    },
    takeOut: function (id) {
      return A.moveToGroup(id, null).then(reload);
    },
    remove: function (id, name) {
      return A.removeScenario(id, name).then(function (gone) {
        if (gone) reload();
        return gone;
      });
    },
    saveText: function (fields) {
      return A.updateGroup(data.group_id, fields).then(reload);
    },
    rebaseline: function () {
      return A.rebaseline(data.group_id).then(reload);
    },
    removeGroup: function () {
      return A.removeGroup(data.group_id, data.group_name).then(
        function (gone) {
          if (gone)
            A.later(function () {
              window.location.assign('../'); // the groups list, beside this page
            });
          return gone;
        },
      );
    },
  };
  window.EffGroup = ops;

  // A button says what it is doing in the line beside it, and the server's refusal there.
  function says(id, text, run) {
    var el = document.getElementById(id);
    el.textContent = text;
    return run().then(null, function (err) {
      el.textContent = err.message;
    });
  }

  var save = document.getElementById('g-save');
  if (save)
    save.addEventListener('click', function () {
      says('g-state', 'Saving…', function () {
        return ops.saveText({
          question: document.getElementById('g-question').value,
          summary: document.getElementById('g-summary').value,
        });
      });
    });

  var baseline = document.getElementById('g-baseline');
  if (baseline)
    baseline.addEventListener('click', function () {
      says(
        'b-state',
        'Computing today’s numbers: about 30 seconds…',
        ops.rebaseline,
      );
    });

  Array.prototype.forEach.call(
    document.querySelectorAll('[data-testid="card-manage"] button'),
    function (btn) {
      btn.addEventListener('click', function () {
        var id = Number(btn.dataset.id);
        var act = btn.dataset.act;
        var run =
          act === 'earlier' || act === 'later'
            ? ops.move(id, act)
            : act === 'out'
              ? ops.takeOut(id)
              : ops.remove(id, btn.dataset.name);
        run.then(null, function (err) {
          window.alert(err.message);
        });
      });
    },
  );

  var del = document.getElementById('g-delete');
  if (del)
    del.addEventListener('click', function () {
      ops.removeGroup().then(null, function (err) {
        window.alert(err.message);
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
