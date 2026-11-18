/* Saving and opening scenarios: the panel at the top of the controls on Market size and the workspace.
 *
 * What is saved is the scenario's link (`scn.params()`); the server checks it and keeps it under a name. Opening one is
 * a page load of `?scenario=<id>`, so the server reads it with today's models and the page starts from the result: this
 * file never rebuilds a scenario itself. */
(function () {
  'use strict';
  var U = window.EffUI;
  var h = U.h,
    put = U.put,
    A = window.EffApi;

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

    function fail(err) {
      say(err.message, true);
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
      return A.update(s.id, patch).then(function (j) {
        if (loaded && loaded.id === s.id) {
          loaded = Object.assign({}, loaded, {
            name: j.name,
            shared: j.shared,
          });
          drawStatus();
        }
        return refresh().then(function () {
          say(said);
          return j;
        });
      }, fail);
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
          removeScenario(s);
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
                  text: 'Scenario ' + s.group.position + ' of ' + s.group.count,
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
      // A list is read by group: each group's scenarios in its order, then the ones in no group.
      function rows(items) {
        var groups = [];
        var loose = [];
        items.forEach(function (s) {
          if (!s.group) return loose.push(s);
          var g = groups.filter(function (x) {
            return x.id === s.group.id;
          })[0];
          if (!g)
            groups.push(
              (g = { id: s.group.id, name: s.group.name, items: [] }),
            );
          g.items.push(s);
        });
        var out = [];
        groups.forEach(function (g) {
          g.items.sort(function (a, b) {
            return a.group.position - b.group.position;
          });
          out.push(h('div', { class: 'gs', text: g.name }));
          out = out.concat(g.items.map(row));
        });
        if (groups.length && loose.length)
          out.push(h('div', { class: 'gs', text: 'Not in a group' }));
        return out.concat(loose.map(row));
      }
      function block(title, items, empty) {
        return [h('div', { class: 'gt', text: title })].concat(
          items.length
            ? rows(items)
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
      return A.list()
        .then(
          function (j) {
            lists = j;
          },
          function () {},
        )
        .then(drawLists);
    }

    // Delete a saved scenario (asks first); the one that is open leaves its page for its group, or the bare workspace.
    function removeScenario(s) {
      return A.removeScenario(s.id, s.name).then(function (gone) {
        if (!gone) return null;
        if (loaded && loaded.id === s.id) {
          var back = loaded.group ? loaded.group.path : location.pathname;
          A.later(function () {
            location.href = back;
          });
          return gone;
        }
        return refresh().then(function () {
          say('Deleted "' + s.name + '".');
          return gone;
        });
      }, fail);
    }

    function save(overwrite) {
      return A.save({
        name: name.value,
        note: note.value,
        shared: shared.checked,
        overwrite: !!overwrite,
        params: Object.fromEntries(scn.params().entries()),
      }).then(
        function (j) {
          loaded = j;
          drawStatus();
          drawGroup();
          // The list first, so the message is never beside a stale one.
          return refresh().then(function () {
            say('Saved "' + j.name + '".');
            return j;
          });
        },
        function (err) {
          if (err.status === 409 && !overwrite) {
            if (
              confirm(
                'You already have a scenario called "' +
                  name.value.trim() +
                  '". Replace it?',
              )
            )
              return save(true);
            say('Not saved.');
            return null;
          }
          fail(err);
          return null;
        },
      );
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
    // Into one of the person's groups, or out of its group (null); the page then opens the scenario in its new place.
    function moveToGroup(gid) {
      return A.moveToGroup(loaded.id, gid).then(function (j) {
        var to = location.pathname + '?scenario=' + loaded.id;
        A.later(function () {
          location.href = to;
        });
        return j;
      }, fail);
    }
    function newGroup(gname) {
      return A.createGroup({ name: gname }).then(
        function (g) {
          return moveToGroup(g.id);
        },
        function (err) {
          fail(err);
          drawGroup();
        },
      );
    }
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
        if (pick.value === 'new') {
          var gname = prompt(
            'Name the new group (the question it answers works well):',
          );
          if (gname == null || gname.trim() === '') return drawGroup();
          return newGroup(gname.trim());
        }
        moveToGroup(pick.value === '' ? null : Number(pick.value));
      });
      var del = h('button', {
        type: 'button',
        class: 'btn danger',
        text: 'Delete this scenario',
      });
      del.addEventListener('click', function () {
        removeScenario(loaded);
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
    // What the buttons do, for the canopy agent to do too: the same functions, so there is one way to save, move or delete.
    window.EffScenarios.current = {
      loaded: function () {
        return loaded;
      },
      // Fill in the fields it is given, then press Save: a name already in use asks to replace, as for the person.
      save: function (fields) {
        fields = fields || {};
        if (fields.name != null) name.value = fields.name;
        if (fields.note != null) note.value = fields.note;
        if (fields.shared != null) shared.checked = !!fields.shared;
        return save(false).then(function (j) {
          if (j && fields.group !== undefined) return moveToGroup(fields.group);
          return j;
        });
      },
      update: function (patch) {
        if (!mineLoaded())
          throw new Error('Open one of your saved scenarios first.');
        return change(loaded, patch, 'Saved.');
      },
      moveToGroup: function (gid) {
        if (!mineLoaded())
          throw new Error('Open one of your saved scenarios first.');
        return moveToGroup(gid);
      },
      newGroup: function (gname) {
        if (!mineLoaded())
          throw new Error('Open one of your saved scenarios first.');
        return newGroup(gname);
      },
      remove: function (s) {
        return removeScenario(s || loaded);
      },
    };
    drawLists();
    refresh();
    scn.onChange(drawStatus);
  }

  window.EffScenarios = { mount: mount, same: same };
})();
