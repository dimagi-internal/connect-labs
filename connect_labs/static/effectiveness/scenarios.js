/* Saving and opening scenarios: the panel at the top of the controls on Market size and the workspace.
 *
 * What is saved is the scenario's link (`scn.params()`); the server checks it and keeps it under a name. Opening one is
 * a page load of `?scenario=<id>`, so the server reads it with today's models and the page starts from the result: this
 * file never rebuilds a scenario itself. */
(function () {
  'use strict';
  var U = window.EffUI;
  var h = U.h,
    put = U.put;

  // Two links are the same scenario when every key has the same value, numbers compared as numbers ("1e-7" is "1e-07").
  function entries(p) {
    var o = p instanceof URLSearchParams ? Object.fromEntries(p.entries()) : p;
    return Object.keys(o)
      .sort()
      .map(function (k) {
        var v = String(o[k]);
        return [
          k,
          v.trim() !== '' && !isNaN(Number(v)) ? String(Number(v)) : v,
        ];
      });
  }
  function same(a, b) {
    return JSON.stringify(entries(a)) === JSON.stringify(entries(b));
  }

  function mount(root, scn, data) {
    var loaded = data.loaded;
    var lists = { mine: [], shared: [] };
    var status = h('div', { class: 'scn-status' });
    var msg = h('p', {
      class: 'muted',
      css: { margin: '6px 0 0', minHeight: '1em' },
    });
    var name = h('input', {
      type: 'text',
      placeholder: 'Name this scenario',
      value: loaded ? loaded.name + (loaded.mine ? '' : ' (copy)') : '',
    });
    var note = h('input', {
      type: 'text',
      placeholder: 'A note, if you want one',
      value: loaded && loaded.mine ? loaded.note : '',
    });
    var shared = h('input', {
      type: 'checkbox',
      checked: loaded && loaded.mine && loaded.shared ? true : null,
    });
    var listBox = h('div');

    function say(text, bad) {
      msg.textContent = text || '';
      msg.className = bad ? 'err' : 'muted';
      msg.style.margin = '6px 0 0';
    }

    function call(method, url, body) {
      return fetch(url, {
        method: method,
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': data.csrf,
        },
        body: body ? JSON.stringify(body) : undefined,
      }).then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, status: r.status, j: j };
        });
      });
    }

    function drawStatus() {
      var kids = [];
      // The scenario's name and note are in the header above the numbers; this says only whether it matches what is saved.
      if (!loaded) kids.push('Not saved');
      else {
        kids.push(
          same(scn.params(), loaded.params)
            ? 'Saved, and unchanged'
            : 'Saved, but changed since: Save to keep the changes',
        );
        if (loaded.models_changed.length)
          kids.push(
            h('div', {
              class: 'lo',
              text: 'The models have changed since this was saved, so its numbers may not be the ones it showed.',
            }),
          );
      }
      put(status, kids);
    }

    // Rename, or share or unshare, a scenario of yours: the list is redrawn before the message, as for a save.
    function change(s, patch, said) {
      call('PATCH', data.scenarios_url + s.id + '/', patch).then(
        function (res) {
          if (!res.ok) return say(res.j.error, true);
          if (loaded && loaded.id === s.id) {
            loaded = Object.assign({}, loaded, {
              name: res.j.name,
              shared: res.j.shared,
            });
            drawStatus();
          }
          refresh().then(function () {
            say(said);
          });
        },
      );
    }

    function row(s) {
      var open = h('button', { type: 'button', class: 'btn', text: 'Open' });
      open.addEventListener('click', function () {
        location.href = location.pathname + '?scenario=' + s.id;
      });
      var kids = [open];
      if (s.mine) {
        // Share it with everyone on Labs, or make it private again.
        var share = h('button', {
          type: 'button',
          class: 'btn',
          text: s.shared ? 'Make private' : 'Share',
        });
        share.addEventListener('click', function () {
          change(
            s,
            { shared: !s.shared },
            (s.shared ? 'Made "' : 'Shared "') +
              s.name +
              '"' +
              (s.shared ? ' private.' : ' with everyone on Labs.'),
          );
        });
        var rename = h('button', {
          type: 'button',
          class: 'btn',
          text: 'Rename',
        });
        rename.addEventListener('click', function () {
          var to = prompt('Rename "' + s.name + '" to:', s.name);
          if (to == null || to.trim() === '' || to.trim() === s.name) return;
          change(s, { name: to.trim() }, 'Renamed to "' + to.trim() + '".');
        });
        var del = h('button', { type: 'button', class: 'btn', text: 'Delete' });
        del.addEventListener('click', function () {
          if (!confirm('Delete "' + s.name + '"?')) return;
          call('DELETE', data.scenarios_url + s.id + '/').then(function (res) {
            if (!res.ok) return say(res.j.error, true);
            if (loaded && loaded.id === s.id) {
              loaded = null;
              drawStatus();
            }
            refresh().then(function () {
              say('Deleted "' + s.name + '".');
            });
          });
        });
        kids.push(share, rename, del);
      }
      var meta =
        (s.mine ? '' : s.owner + ' · ') +
        new Date(s.updated_at).toLocaleDateString() +
        (s.mine && s.shared ? ' · shared' : '') +
        (s.models_changed.length ? ' · models changed' : '');
      // A long note is a paragraph of results; the list shows the sentence that says what the scenario is.
      var gist = s.note ? s.note.split('. ')[0].replace(/\.$/, '') + '.' : '';
      return h(
        'div',
        { class: 'srow' + (loaded && loaded.id === s.id ? ' cur' : '') },
        [
          h('div', { class: 'sn' }, [
            h('b', { text: s.name }),
            s.group
              ? h('small', {
                  text:
                    s.group.name +
                    ' · ' +
                    s.group.position +
                    ' of ' +
                    s.group.count,
                })
              : null,
            h('small', { text: meta }),
            gist ? h('small', { text: gist }) : null,
          ]),
          h('div', { class: 'sb' }, kids),
        ],
      );
    }

    function drawLists() {
      function block(title, items, empty) {
        return [h('div', { class: 'gt', text: title })].concat(
          items.length
            ? items.map(row)
            : [h('p', { class: 'muted', text: empty })],
        );
      }
      var total = lists.mine.length + lists.shared.length;
      put(listBox, [
        h('details', { class: 'saved', open: total <= 4 ? true : null }, [
          h('summary', { text: 'Saved scenarios (' + total + ')' }),
          h(
            'div',
            {},
            block(
              'Yours',
              lists.mine,
              data.signed_in
                ? 'Nothing saved yet.'
                : 'Sign in to save scenarios.',
            ).concat(
              block(
                'Shared with everyone on Labs',
                lists.shared,
                'Nothing shared yet.',
              ),
            ),
          ),
        ]),
      ]);
    }

    function refresh() {
      return call('GET', data.scenarios_url)
        .then(function (res) {
          if (res.ok) lists = res.j;
        })
        .catch(function () {})
        .then(drawLists);
    }

    function save(overwrite) {
      var body = {
        name: name.value,
        note: note.value,
        shared: shared.checked,
        overwrite: !!overwrite,
        params: Object.fromEntries(scn.params().entries()),
      };
      return call('POST', data.scenarios_url, body)
        .then(function (res) {
          if (res.status === 409 && !overwrite) {
            if (
              confirm(
                'You already have a scenario called "' +
                  name.value.trim() +
                  '". Replace it?',
              )
            )
              return save(true);
            return say('Not saved.');
          }
          if (!res.ok) return say(res.j.error, true);
          loaded = res.j;
          drawStatus();
          drawGroup();
          // The list first, so the message is never beside a stale one.
          return refresh().then(function () {
            say('Saved "' + res.j.name + '".');
          });
        })
        .catch(function () {
          say('The server did not respond. Try again.', true);
        });
    }

    var saveBtn = h('button', {
      type: 'button',
      class: 'btn on',
      text: 'Save',
    });
    saveBtn.addEventListener('click', function () {
      save(false);
    });
    var copyBtn = h('button', {
      type: 'button',
      class: 'btn',
      text: 'Copy link',
    });
    copyBtn.addEventListener('click', function () {
      var url =
        location.origin + location.pathname + '?' + scn.link().toString();
      if (navigator.clipboard && navigator.clipboard.writeText)
        navigator.clipboard.writeText(url).then(
          function () {
            say('Link copied.');
          },
          function () {
            say('Copy the address bar: it holds this scenario.');
          },
        );
      else say('Copy the address bar: it holds this scenario.');
    });
    if (!data.signed_in) {
      saveBtn.disabled = true;
      name.disabled = true;
      note.disabled = true;
      shared.disabled = true;
    }

    // Which group the saved scenario is in, and delete: only for one of yours.
    var mineLoaded = function () {
      return loaded && loaded.mine;
    };
    var groupBox = h('div');
    function drawGroup() {
      if (!mineLoaded()) return put(groupBox, []);
      var pick = h('select', { 'aria-label': 'Group' });
      var current = loaded.group ? loaded.group.id : '';
      [{ id: '', name: 'No group' }]
        .concat(data.my_groups || [])
        .concat([{ id: 'new', name: 'A new group…' }])
        .forEach(function (g) {
          pick.appendChild(
            h('option', {
              value: String(g.id),
              text: g.name,
              selected: String(g.id) === String(current) ? true : null,
            }),
          );
        });
      pick.addEventListener('change', function () {
        function move(gid) {
          call('PATCH', data.scenarios_url + loaded.id + '/', {
            group: gid === '' ? null : Number(gid),
          }).then(function (res) {
            if (!res.ok) return say(res.j.error, true);
            location.href = location.pathname + '?scenario=' + loaded.id;
          });
        }
        if (pick.value !== 'new') return move(pick.value);
        var gname = prompt(
          'Name the new group (the question it answers works well):',
        );
        if (gname == null || gname.trim() === '') return drawGroup();
        call('POST', data.groups_url, { name: gname.trim() }).then(
          function (res) {
            if (!res.ok) {
              say(res.j.error, true);
              return drawGroup();
            }
            move(res.j.id);
          },
        );
      });
      var del = h('button', {
        type: 'button',
        class: 'btn danger',
        text: 'Delete this scenario',
      });
      del.addEventListener('click', function () {
        if (!confirm('Delete "' + loaded.name + '"? This cannot be undone.'))
          return;
        var back = loaded.group ? loaded.group.path : location.pathname;
        call('DELETE', data.scenarios_url + loaded.id + '/').then(
          function (res) {
            if (!res.ok) return say(res.j.error, true);
            location.href = back;
          },
        );
      });
      put(groupBox, [
        h('label', { class: 'f' }, [h('span', { text: 'Group' }), pick]),
        h('div', { css: { marginTop: '8px' } }, [del]),
      ]);
    }

    put(root, [
      h('h2', { text: 'Scenario' }),
      status,
      h('label', { class: 'f' }, [
        h('span', { text: loaded && loaded.mine ? 'Save as' : 'Name' }),
        name,
      ]),
      h('label', { class: 'f' }, [h('span', { text: 'Note' }), note]),
      h('label', { class: 'f chk' }, [shared, ' Share with everyone on Labs']),
      h(
        'div',
        {
          class: 'row',
          css: { marginTop: '10px', display: 'flex', gap: '8px' },
        },
        [saveBtn, copyBtn],
      ),
      groupBox,
      msg,
      listBox,
    ]);
    drawGroup();
    drawStatus();
    drawLists();
    refresh();
    scn.onChange(drawStatus);
  }

  window.EffScenarios = { mount: mount, same: same };
})();
