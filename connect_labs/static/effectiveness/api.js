/* Everything a scenario page does to saved scenarios and groups, in one place: the buttons and the canopy agent's page
 * actions both call these, so a delete asks the same question and a move sends the same request whoever started it.
 *
 * The addresses and the CSRF token come from the page's own JSON (`#scenario-data` on Market size and the workspace,
 * `#group-data` on a group page); the rules are the server's (`effectiveness.saved`, `effectiveness.scenario_groups`), this
 * only carries them. Every call resolves to the server's JSON and rejects with an Error whose message is the server's. */
(function () {
  'use strict';

  var cfg = null;
  function config() {
    if (cfg) return cfg;
    var node =
      document.getElementById('scenario-data') ||
      document.getElementById('group-data');
    var d = node ? JSON.parse(node.textContent) : {};
    cfg = {
      scenarios: d.scenarios_url,
      groups: d.groups_url,
      csrf:
        d.csrf || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '',
    };
    return cfg;
  }

  function request(method, url, body) {
    return fetch(url, {
      method: method,
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': config().csrf,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(
      function (r) {
        return r.json().then(function (j) {
          if (r.ok) return j;
          var err = new Error(j.error || 'That did not work.');
          err.status = r.status;
          err.body = j;
          throw err;
        });
      },
      function () {
        throw new Error('The server did not respond. Try again.');
      },
    );
  }

  function scenarioUrl(id) {
    return config().scenarios + id + '/';
  }
  function groupUrl(id) {
    return config().groups + id + '/';
  }

  // The order after moving one scenario: `to` is a 1-based place, or 'earlier' / 'later'. Nothing moves past an end.
  function reordered(order, id, to) {
    var at = order.indexOf(id);
    if (at < 0) throw new Error('That scenario is not in this group.');
    var place =
      to === 'earlier' ? at : to === 'later' ? at + 2 : Math.round(Number(to));
    if (isNaN(place))
      throw new Error('Give a place from 1 to ' + order.length + '.');
    place = Math.max(1, Math.min(order.length, place));
    var out = order.slice();
    out.splice(at, 1);
    out.splice(place - 1, 0, id);
    return out;
  }

  // Deleting cannot be undone, so it asks, and answers null when the person says no.
  function confirmed(question) {
    return window.confirm(question);
  }

  // Leave or reload a beat later, so the canopy agent's acknowledgement is posted before the page goes.
  function later(fn) {
    setTimeout(fn, 300);
  }

  window.EffApi = {
    reordered: reordered,
    config: config,
    later: later,
    // ---- scenarios
    list: function () {
      return request('GET', config().scenarios);
    },
    save: function (body) {
      return request('POST', config().scenarios, body);
    },
    update: function (id, patch) {
      return request('PATCH', scenarioUrl(id), patch);
    },
    // Into one of the person's groups (last in its order), or out of its group (null).
    moveToGroup: function (id, group) {
      return request('PATCH', scenarioUrl(id), { group: group });
    },
    removeScenario: function (id, name) {
      if (!confirmed('Delete "' + name + '"? This cannot be undone.'))
        return Promise.resolve(null);
      return request('DELETE', scenarioUrl(id));
    },
    // ---- groups
    createGroup: function (body) {
      return request('POST', config().groups, body);
    },
    updateGroup: function (id, patch) {
      return request('PATCH', groupUrl(id), patch);
    },
    arrange: function (id, order) {
      return request('PATCH', groupUrl(id), { scenario_ids: order });
    },
    removeGroup: function (id, name) {
      if (
        !confirmed(
          'Delete the group "' +
            name +
            '"? Its scenarios are kept, just no longer grouped.',
        )
      )
        return Promise.resolve(null);
      return request('DELETE', groupUrl(id));
    },
    rebaseline: function (id) {
      return request('POST', groupUrl(id) + 'baseline/', {});
    },
  };
})();
