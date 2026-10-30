/*
 * The field page (connect_labs/supply_chain/update_links/field.py).
 *
 * Plain ES2015, no framework and no build step, for old Android phones on poor connections.
 * What it does: record a stock count (place, product, quantity, batch, date, optional photo),
 * save it on the phone FIRST, and send it when there is signal -- each entry with an id the phone
 * made up, so sending it twice records it once. A barcode / QR scan fills the product in.
 *
 * Nothing here talks to any other site. Everything it needs is in the page's own JSON block.
 */
(function () {
  'use strict';

  var cfg = JSON.parse(document.getElementById('cfg').textContent);
  var $ = function (id) {
    return document.getElementById(id);
  };

  // ---- small helpers -------------------------------------------------------

  function uid() {
    var bytes = new Uint8Array(12);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i++) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    var out = 'e';
    for (var j = 0; j < bytes.length; j++) {
      out += ('0' + bytes[j].toString(16)).slice(-2);
    }
    return out + Date.now().toString(36);
  }

  function today() {
    var d = new Date();
    var m = ('0' + (d.getMonth() + 1)).slice(-2);
    var day = ('0' + d.getDate()).slice(-2);
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function text(el, value) {
    el.textContent = value;
    return el;
  }

  // ---- the phone's own store (IndexedDB) -----------------------------------

  var dbPromise = null;
  function db() {
    if (!dbPromise) {
      dbPromise = new Promise(function (resolve, reject) {
        var open = indexedDB.open(cfg.db, 1);
        open.onupgradeneeded = function () {
          var d = open.result;
          d.createObjectStore('outbox', { keyPath: 'id' });
          d.createObjectStore('done', { keyPath: 'id' });
          d.createObjectStore('meta');
        };
        open.onsuccess = function () {
          resolve(open.result);
        };
        open.onerror = function () {
          reject(open.error);
        };
      });
    }
    return dbPromise;
  }

  function tx(store, mode, work) {
    return db().then(function (d) {
      return new Promise(function (resolve, reject) {
        var t = d.transaction(store, mode);
        var result = work(t.objectStore(store));
        t.oncomplete = function () {
          resolve(
            result && result.result !== undefined ? result.result : undefined,
          );
        };
        t.onerror = function () {
          reject(t.error);
        };
        t.onabort = function () {
          reject(t.error);
        };
      });
    });
  }

  var store = {
    all: function (name) {
      return tx(name, 'readonly', function (s) {
        return s.getAll();
      });
    },
    put: function (name, value, key) {
      return tx(name, 'readwrite', function (s) {
        return key === undefined ? s.put(value) : s.put(value, key);
      });
    },
    del: function (name, key) {
      return tx(name, 'readwrite', function (s) {
        return s['delete'](key);
      });
    },
    get: function (name, key) {
      return tx(name, 'readonly', function (s) {
        return s.get(key);
      });
    },
  };

  // ---- reference data ------------------------------------------------------

  var data = null; // {points, items, org, recipient, csrf, ...}

  function loadData(fresh) {
    var url = cfg.urls.data + (fresh ? '?t=' + Date.now() : '');
    return fetch(url, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) {
        if (r.status === 403) {
          return r.json().then(function (b) {
            throw { verify: true, body: b };
          });
        }
        if (!r.ok) {
          throw { status: r.status };
        }
        return r.json();
      })
      .then(function (d) {
        data = d;
        return store.put('meta', d, 'data').then(function () {
          return d;
        });
      })
      .catch(function (err) {
        if (err && (err.verify || err.status === 404)) {
          throw err;
        }
        return store.get('meta', 'data').then(function (kept) {
          if (!kept) {
            throw err || { offline: true };
          }
          data = kept;
          return kept;
        });
      });
  }

  function fill(select, rows, label) {
    select.innerHTML = '';
    rows.forEach(function (row) {
      var o = document.createElement('option');
      o.value = row.id;
      o.textContent = label(row);
      select.appendChild(o);
    });
  }

  function currentItem() {
    var id = Number($('item').value);
    return (
      (data &&
        data.items.filter(function (i) {
          return i.id === id;
        })[0]) ||
      null
    );
  }

  function setBasisOptions() {
    var item = currentItem();
    var basis = $('basis');
    basis.innerHTML = '';
    var pack = document.createElement('option');
    pack.value = 'pack';
    pack.textContent = (item && item.pack_unit) || 'packs';
    var base = document.createElement('option');
    base.value = 'base';
    base.textContent = (item && item.base_unit) || 'single units';
    basis.appendChild(pack);
    basis.appendChild(base);
  }

  function showData() {
    fill($('point'), data.points, function (p) {
      return p.name;
    });
    fill($('item'), data.items, function (i) {
      return i.name;
    });
    var last = null;
    try {
      last = localStorage.getItem(cfg.db + ':point');
    } catch (e) {
      /* private mode */
    }
    if (last) {
      $('point').value = last;
    }
    setBasisOptions();
    if (!data.points.length || !data.items.length) {
      banner(
        'Nothing is set up on this link to count yet. Ask the programme team.',
        false,
      );
    }
  }

  // ---- messages ------------------------------------------------------------

  function banner(message, good) {
    var b = $('banner');
    if (!message) {
      b.hidden = true;
      return;
    }
    b.textContent = message;
    b.className = 'msg' + (good ? ' good' : '');
    b.hidden = false;
  }

  function needVerify(err) {
    var b = $('banner');
    b.className = 'msg';
    b.textContent = '';
    b.appendChild(document.createTextNode('Check it is you first: '));
    var a = document.createElement('a');
    a.href = cfg.urls.verify;
    a.textContent = 'open this link’s main page';
    b.appendChild(a);
    b.appendChild(
      document.createTextNode(' and enter the code, then come back.'),
    );
    b.hidden = false;
  }

  // ---- photo: shrink it on the phone ---------------------------------------

  var photoBlob = null;

  function shrink(file) {
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        try {
          var max = 1280;
          var scale = Math.min(
            1,
            max / Math.max(img.naturalWidth, img.naturalHeight),
          );
          var canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
          canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
          canvas
            .getContext('2d')
            .drawImage(img, 0, 0, canvas.width, canvas.height);
          canvas.toBlob(
            function (blob) {
              URL.revokeObjectURL(url);
              resolve(blob || file);
            },
            'image/jpeg',
            0.7,
          );
        } catch (e) {
          URL.revokeObjectURL(url);
          resolve(file);
        }
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        resolve(file);
      };
      img.src = url;
    });
  }

  function onPhoto() {
    var file = $('photo').files && $('photo').files[0];
    var note = $('photo-note');
    if (!file) {
      photoBlob = null;
      note.hidden = true;
      return;
    }
    shrink(file).then(function (blob) {
      if (blob.size > 3.5 * 1024 * 1024) {
        photoBlob = null;
        text(note, 'That photo is too large to send. Try again.');
      } else {
        photoBlob = blob;
        text(
          note,
          'Photo ready (' + Math.max(1, Math.round(blob.size / 1024)) + ' KB).',
        );
      }
      note.hidden = false;
    });
  }

  // ---- saving an entry -----------------------------------------------------

  function onSave(event) {
    event.preventDefault();
    if (!data) {
      return;
    }
    var qty = $('qty').value.trim();
    // A number input keeps "5E21" when letters land in it; the server refuses that, so stop it
    // here, before it is saved on the phone and can only fail later.
    if (!/^\d+(\.\d+)?$/.test(qty)) {
      banner('Enter how many, as a number like 12 or 2.5.', false);
      return;
    }
    if (Number(qty) > 1000000000) {
      banner(
        'That is too many for one count: the most is 1,000,000,000.',
        false,
      );
      return;
    }
    var entry = {
      id: uid(),
      supply_point: Number($('point').value),
      item: Number($('item').value),
      quantity: qty,
      unit_basis: $('basis').value,
      counted_on: $('day').value || today(),
      batch: $('batch').value,
      device_time: new Date().toISOString(),
      photo: photoBlob,
      state: 'queued',
      countSent: false,
      error: '',
      saved: Date.now(),
      label: describe(),
    };
    try {
      localStorage.setItem(cfg.db + ':point', String(entry.supply_point));
    } catch (e) {
      /* private mode */
    }
    store
      .put('outbox', entry)
      .then(function () {
        $('qty').value = '';
        $('batch').value = '';
        $('photo').value = '';
        photoBlob = null;
        $('photo-note').hidden = true;
        banner('Saved on this phone.', true);
        return render().then(sync);
      })
      .catch(function () {
        banner(
          'This phone could not save the entry. Free some space and try again.',
          false,
        );
      });
  }

  function describe() {
    var item = currentItem();
    var point = $('point').options[$('point').selectedIndex];
    return (
      $('qty').value +
      ' ' +
      ($('basis').options[$('basis').selectedIndex]
        ? $('basis').options[$('basis').selectedIndex].text
        : '') +
      ' · ' +
      (item ? item.name : '') +
      ' · ' +
      (point ? point.text : '')
    );
  }

  // ---- sending -------------------------------------------------------------

  var syncing = false;
  var retryTimer = null;
  var retryDelay = 15000;

  function csrf() {
    return (data && data.csrf) || '';
  }

  function post(url, body, isForm) {
    var headers = { 'X-CSRFToken': csrf() };
    if (!isForm) {
      headers['Content-Type'] = 'application/json';
    }
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: isForm ? body : JSON.stringify(body),
    });
  }

  function outcome(entry, response) {
    // -> 'sent' | 'rejected' | 'stop' (try again later) | 'verify' | 'gone'
    if (response.status === 200 || response.ok === true) {
      return 'sent';
    }
    if (response.status === 400 || response.status === 413) {
      return 'rejected';
    }
    if (response.status === 403) {
      return 'refresh';
    }
    if (response.status === 404) {
      return 'gone';
    }
    return 'stop';
  }

  function sendEntry(entry) {
    var counted = entry.countSent
      ? Promise.resolve({ status: 200 })
      : post(cfg.urls.count, {
          client_id: entry.id,
          supply_point: entry.supply_point,
          item: entry.item,
          quantity: entry.quantity,
          unit_basis: entry.unit_basis,
          counted_on: entry.counted_on,
          batch: entry.batch,
          device_time: entry.device_time,
        });
    return counted.then(function (response) {
      var result = outcome(entry, response);
      if (result === 'rejected') {
        return response.json().then(
          function (b) {
            entry.state = 'rejected';
            entry.error =
              (b && b.error) || 'The programme could not take this entry.';
            return store.put('outbox', entry).then(function () {
              return 'next';
            });
          },
          function () {
            entry.state = 'rejected';
            entry.error = 'The programme could not take this entry.';
            return store.put('outbox', entry).then(function () {
              return 'next';
            });
          },
        );
      }
      if (result === 'refresh') {
        return response.json().then(
          function (b) {
            return b && b.error === 'verify' ? 'verify' : 'refresh';
          },
          function () {
            return 'refresh';
          },
        );
      }
      if (result !== 'sent') {
        return result;
      }
      entry.countSent = true;
      var photoStep = Promise.resolve('done');
      if (entry.photo) {
        var form = new FormData();
        form.append('client_id', entry.id);
        form.append('photo', entry.photo, 'photo.jpg');
        photoStep = post(cfg.urls.photo, form, true).then(function (r) {
          if (r.status === 200) {
            return 'done';
          }
          if (r.status === 400 || r.status === 413) {
            return 'done'; // a photo that cannot be taken must not hold the count back
          }
          return 'stop';
        });
      }
      return photoStep.then(function (p) {
        if (p !== 'done') {
          return store.put('outbox', entry).then(function () {
            return 'stop';
          });
        }
        entry.state = 'sent';
        var summary = {
          id: entry.id,
          label: entry.label,
          sent: Date.now(),
          counted_on: entry.counted_on,
        };
        return store
          .put('done', summary)
          .then(function () {
            return store.del('outbox', entry.id);
          })
          .then(function () {
            return 'next';
          });
      });
    });
  }

  function sync() {
    if (syncing) {
      return Promise.resolve();
    }
    syncing = true;
    return store
      .all('outbox')
      .then(function (entries) {
        var waiting = entries
          .filter(function (e) {
            return e.state !== 'rejected';
          })
          .sort(function (a, b) {
            return a.saved - b.saved;
          });
        if (!waiting.length) {
          return 'idle';
        }
        // A fresh token and fresh data, then each entry in order.
        return loadData(true).then(function () {
          var index = 0;
          function next() {
            if (index >= waiting.length) {
              return 'idle';
            }
            return sendEntry(waiting[index++]).then(function (r) {
              return r === 'next' ? next() : r;
            });
          }
          return next();
        });
      })
      .then(function (result) {
        syncing = false;
        if (result === 'verify') {
          needVerify();
        } else if (result === 'gone') {
          banner(
            'This link has stopped working. Ask the programme team for a new one.',
            false,
          );
        } else if (result === 'refresh') {
          banner(
            'Could not send yet. Reload this page, then it will try again.',
            false,
          );
        } else if (result === 'stop') {
          retryLater();
        } else {
          retryDelay = 15000;
        }
        return render();
      })
      .catch(function (err) {
        syncing = false;
        if (err && err.verify) {
          needVerify();
        } else if (err && err.status === 404) {
          banner(
            'This link has stopped working. Ask the programme team for a new one.',
            false,
          );
        } else {
          retryLater();
        }
        return render();
      });
  }

  function retryLater() {
    clearTimeout(retryTimer);
    retryTimer = setTimeout(sync, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 5 * 60 * 1000);
  }

  // ---- what is on screen ---------------------------------------------------

  function render() {
    return Promise.all([store.all('outbox'), store.all('done')]).then(
      function (both) {
        var outbox = both[0].sort(function (a, b) {
          return a.saved - b.saved;
        });
        var done = both[1]
          .sort(function (a, b) {
            return b.sent - a.sent;
          })
          .slice(0, 10);
        var queue = $('queue');
        queue.innerHTML = '';
        outbox.forEach(function (e) {
          var li = document.createElement('li');
          li.setAttribute('data-state', e.state);
          li.appendChild(
            document.createTextNode(e.label + ' · ' + e.counted_on),
          );
          var status = document.createElement('div');
          status.className = 'note' + (e.state === 'rejected' ? ' bad' : '');
          status.textContent =
            e.state === 'rejected'
              ? 'Not accepted: ' + e.error
              : e.countSent
                ? 'Counted; photo still to send'
                : 'Waiting for signal';
          li.appendChild(status);
          if (e.state === 'rejected') {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'link';
            b.textContent = 'Dismiss';
            b.onclick = function () {
              store.del('outbox', e.id).then(render);
            };
            li.appendChild(b);
          }
          queue.appendChild(li);
        });
        $('queue-box').hidden = !outbox.length;
        var pending = outbox.filter(function (e) {
          return e.state !== 'rejected';
        }).length;
        var pill = $('pending');
        pill.hidden = !pending;
        pill.textContent = pending + ' to send';
        $('send-now').hidden = !pending;

        var list = $('done');
        list.innerHTML = '';
        done.forEach(function (d) {
          var li = document.createElement('li');
          li.textContent = d.label + ' · ' + d.counted_on;
          list.appendChild(li);
        });
        $('done-box').hidden = !done.length;
      },
    );
  }

  function showNet() {
    var net = $('net');
    var online = navigator.onLine !== false;
    net.textContent = online ? 'online' : 'offline';
    net.className = 'pill ' + (online ? 'ok' : 'off');
  }

  // ---- scanning a barcode / QR ---------------------------------------------

  /** Pull a product code, batch and expiry out of whatever a code holds. */
  function parseCode(raw) {
    var textValue = String(raw || '').trim();
    var out = { raw: textValue, gtin: '', batch: '', expiry: '' };
    var link = textValue.match(/\/01\/(\d{8,14})(?:\/|\?|$|#)/);
    if (link) {
      out.gtin = link[1];
      var lot = textValue.match(/\/10\/([^/?#]+)/);
      var exp = textValue.match(/\/17\/(\d{6})/);
      if (lot) {
        out.batch = decodeURIComponent(lot[1]);
      }
      if (exp) {
        out.expiry = exp[1];
      }
      return out;
    }
    var gs1 = textValue.match(/\(01\)(\d{8,14})/);
    if (gs1) {
      out.gtin = gs1[1];
      var lot2 = textValue.match(/\(10\)([^(]+)/);
      var exp2 = textValue.match(/\(17\)(\d{6})/);
      if (lot2) {
        out.batch = lot2[1];
      }
      if (exp2) {
        out.expiry = exp2[1];
      }
      return out;
    }
    if (/^\d{8,14}$/.test(textValue)) {
      out.gtin = textValue;
    }
    return out;
  }

  function pad14(code) {
    return ('00000000000000' + code).slice(-14);
  }

  function applyCode(raw) {
    var parsed = parseCode(raw);
    var note = $('scan-note');
    if (!parsed.gtin) {
      text(note, 'That code does not carry a product number.');
      return false;
    }
    var wanted = pad14(parsed.gtin);
    var match = (data ? data.items : []).filter(function (i) {
      return i.gtins.some(function (g) {
        return pad14(g) === wanted;
      });
    })[0];
    if (!match) {
      text(
        note,
        'No product on this link matches that code (' + parsed.gtin + ').',
      );
      return false;
    }
    $('item').value = match.id;
    setBasisOptions();
    if (parsed.batch) {
      $('batch').value = parsed.batch;
    }
    closeScanner();
    banner(
      'Product set to ' +
        match.name +
        (parsed.batch ? ', batch ' + parsed.batch : '') +
        '.',
      true,
    );
    return true;
  }

  var stream = null;
  var scanTimer = null;
  var decoderPromise = null;

  function loadDecoder() {
    if (window.jsQR) {
      return Promise.resolve(window.jsQR);
    }
    if (!decoderPromise) {
      decoderPromise = new Promise(function (resolve, reject) {
        var s = document.createElement('script');
        s.src = cfg.decoder;
        s.onload = function () {
          resolve(window.jsQR);
        };
        s.onerror = function () {
          reject(new Error('decoder'));
        };
        document.head.appendChild(s);
      });
    }
    return decoderPromise;
  }

  function decodeCanvas(canvas) {
    var ctx = canvas.getContext('2d');
    var image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    return loadDecoder().then(function (jsQR) {
      var found = jsQR(image.data, image.width, image.height, {
        inversionAttempts: 'attemptBoth',
      });
      return found ? found.data : null;
    });
  }

  function decodeFile(file) {
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var scale = Math.min(
          1,
          900 / Math.max(img.naturalWidth, img.naturalHeight),
        );
        var canvas = document.createElement('canvas');
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        canvas
          .getContext('2d')
          .drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        decodeCanvas(canvas).then(resolve, function () {
          resolve(null);
        });
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        resolve(null);
      };
      img.src = url;
    });
  }

  function openScanner() {
    $('scanner').style.display = 'flex';
    text($('scan-note'), '');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      text(
        $('scan-note'),
        'The camera is not available here. Photograph the code, or type it.',
      );
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .then(function (s) {
        stream = s;
        var video = $('video');
        video.srcObject = s;
        video.play();
        var detector = null;
        if (window.BarcodeDetector) {
          try {
            detector = new window.BarcodeDetector();
          } catch (e) {
            detector = null;
          }
        }
        var canvas = document.createElement('canvas');
        scanTimer = setInterval(function () {
          if (!video.videoWidth) {
            return;
          }
          if (detector) {
            detector
              .detect(video)
              .then(function (codes) {
                if (codes.length && applyCode(codes[0].rawValue)) {
                  return;
                }
              })
              .catch(function () {});
            return;
          }
          var scale = Math.min(1, 640 / video.videoWidth);
          canvas.width = Math.round(video.videoWidth * scale);
          canvas.height = Math.round(video.videoHeight * scale);
          canvas
            .getContext('2d')
            .drawImage(video, 0, 0, canvas.width, canvas.height);
          decodeCanvas(canvas)
            .then(function (value) {
              if (value) {
                applyCode(value);
              }
            })
            .catch(function () {});
        }, 300);
      })
      .catch(function () {
        text(
          $('scan-note'),
          'The camera could not be opened. Photograph the code, or type it.',
        );
      });
  }

  function closeScanner() {
    clearInterval(scanTimer);
    if (stream) {
      stream.getTracks().forEach(function (t) {
        t.stop();
      });
      stream = null;
    }
    $('scanner').style.display = 'none';
  }

  // ---- start ---------------------------------------------------------------

  function start() {
    $('day').value = today();
    $('entry').addEventListener('submit', onSave);
    $('item').addEventListener('change', setBasisOptions);
    $('photo').addEventListener('change', onPhoto);
    $('send-now').addEventListener('click', function () {
      retryDelay = 15000;
      sync();
    });
    $('scan').addEventListener('click', openScanner);
    $('scan-close').addEventListener('click', closeScanner);
    $('typed-go').addEventListener('click', function () {
      applyCode($('typed').value);
    });
    $('shot').addEventListener('change', function () {
      var file = $('shot').files && $('shot').files[0];
      if (!file) {
        return;
      }
      text($('scan-note'), 'Reading the photo…');
      decodeFile(file).then(function (value) {
        if (value) {
          applyCode(value);
        } else {
          text(
            $('scan-note'),
            'No code found in that photo. Try again, closer and steady, or type it.',
          );
        }
      });
    });
    window.addEventListener('online', function () {
      showNet();
      sync();
    });
    window.addEventListener('offline', showNet);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) {
        sync();
      }
    });
    setInterval(function () {
      if (navigator.onLine !== false) {
        sync();
      }
    }, 60000);
    showNet();

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register(cfg.urls.sw).catch(function () {});
    }

    loadData(false)
      .then(function () {
        showData();
        return render();
      })
      .then(sync)
      .catch(function (err) {
        if (err && err.verify) {
          needVerify();
        } else if (err && err.status === 404) {
          banner(
            'This link has stopped working. Ask the programme team for a new one.',
            false,
          );
        } else {
          banner(
            'Open this page once with signal to load the products. Then it works without.',
            false,
          );
        }
        return render();
      });
  }

  window.SupplyField = { parseCode: parseCode, pad14: pad14 };
  start();
})();
