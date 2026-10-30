/* Keeps the field page working with no signal. Generated per link; see update_links/field.py. */
var CACHE = "{{ cache_name }}";
var SHELL = {{ shell|safe }};
var LATER = {{ later|safe }};
var SLOW_MS = 4000;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      /* One file failing (a decoder not yet deployed) must not stop the rest being kept. */
      return Promise.all(
        SHELL.map(function (url) {
          return cache.add(new Request(url, { credentials: "same-origin" })).catch(function () {});
        })
      );
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.filter(function (n) { return n !== CACHE && n.indexOf("supply-field-") === 0; })
        .map(function (n) { return caches.delete(n); }));
    }).then(function () { return self.clients.claim(); })
  );
  /* Best effort, after we are running: a failure just means it is fetched when first needed. */
  caches.open(CACHE).then(function (cache) {
    LATER.forEach(function (url) { cache.add(new Request(url, { credentials: "same-origin" })).catch(function () {}); });
  });
});

function fromNetworkThenCache(request) {
  /* On a slow connection the kept copy is better than a spinner: wait SLOW_MS, then use it,
     while the network request carries on and refreshes the copy. */
  var network = fetch(request).then(function (response) {
    if (response && response.ok) {
      var copy = response.clone();
      caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
    }
    return response;
  });
  var kept = caches.match(request, { ignoreSearch: true });
  return kept.then(function (hit) {
    if (!hit) { return network; }
    var slow = new Promise(function (resolve) { setTimeout(function () { resolve(hit); }, SLOW_MS); });
    return Promise.race([network.catch(function () { return hit; }), slow]);
  });
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") { return; }
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) { return; }
  if (url.pathname.indexOf("/static/") === 0) {
    event.respondWith(
      caches.match(request).then(function (hit) {
        return hit || fetch(request).then(function (response) {
          if (response && response.ok) {
            var copy = response.clone();
            caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
          }
          return response;
        });
      })
    );
  } else if (SHELL.indexOf(url.pathname) !== -1) {
    event.respondWith(fromNetworkThenCache(request));
  }
});
