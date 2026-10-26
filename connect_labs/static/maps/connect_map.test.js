// @vitest-environment happy-dom
// A map whose browser will not start WebGL draws a "map unavailable" panel in
// its own spot and hands back an inert map, instead of throwing and blanking
// every table and chart on the page (2026-10-26, a shared RUTF dashboard:
// "Error rendering workflow — Failed to initialize WebGL.").
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const CONNECT_MAP = fs.readFileSync(path.join(here, 'connect_map.js'), 'utf8');
const PLAN_LAYERS = fs.readFileSync(path.join(here, 'plan_layers.js'), 'utf8');

// The files attach to window, as the page loads them.
function load() {
  new Function('window', CONNECT_MAP)(window);
  new Function(PLAN_LAYERS)();
  return { ConnectMap: window.ConnectMap, PlanLayers: window.PlanLayers };
}

/** mapboxgl as v3.13 behaves when the browser refuses a WebGL2 context: the
 * constructor dresses the container, then throws. */
function failingMapbox() {
  return {
    accessToken: 'pk.test',
    LngLatBounds: class {
      extend() {}
    },
    Map: class {
      constructor(o) {
        o.container.classList.add('mapboxgl-map');
        const canvasBox = document.createElement('div');
        canvasBox.className = 'mapboxgl-canvas-container';
        o.container.appendChild(canvasBox);
        throw new Error('Failed to initialize WebGL.');
      }
    },
  };
}

/** A mapboxgl whose Map records what it was asked to do. */
function workingMapbox() {
  const made = [];
  return {
    made,
    accessToken: 'pk.test',
    LngLatBounds: class {
      extend() {}
    },
    Map: class {
      constructor(o) {
        this.options = o;
        this.calls = [];
        made.push(this);
      }
      on(...a) {
        this.calls.push(['on', ...a]);
        return this;
      }
      getSource() {
        return undefined;
      }
      getLayer() {
        return undefined;
      }
      addSource(...a) {
        this.calls.push(['addSource', ...a]);
      }
      addLayer(...a) {
        this.calls.push(['addLayer', a[0].id]);
      }
    },
  };
}

const FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { ward: 'Kaura', confirmed: true },
      geometry: { type: 'Point', coordinates: [8.5, 9.6] },
    },
  ],
};

let el;
beforeEach(() => {
  el = document.createElement('div');
  document.body.appendChild(el);
});
afterEach(() => {
  el.remove();
  delete window.mapboxgl;
  delete window.ConnectMap;
  delete window.PlanLayers;
  vi.restoreAllMocks();
});

describe('createMap when WebGL will not start', () => {
  beforeEach(() => {
    window.mapboxgl = failingMapbox();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  it('does not throw, and returns a map the caller can tell is unavailable', () => {
    const { ConnectMap } = load();
    let map;
    expect(() => {
      map = ConnectMap.createMap(el, { center: [8.5, 9.6], zoom: 10 });
    }).not.toThrow();
    expect(map).toBeTruthy();
    expect(ConnectMap.isAvailable(map)).toBe(false);
  });

  it('puts the "map unavailable" panel where the map would be, and takes back what Mapbox added', () => {
    const { ConnectMap } = load();
    const kept = document.createElement('span');
    el.appendChild(kept);
    ConnectMap.createMap(el, {});
    expect(el.querySelector('.mapboxgl-canvas-container')).toBeNull();
    expect(el.classList.contains('mapboxgl-map')).toBe(false);
    expect(el.contains(kept)).toBe(true);
    const panel = el.querySelector('.connect-map-unavailable');
    expect(panel).not.toBeNull();
    expect(panel.getAttribute('role')).toBe('status');
    expect(panel.textContent).toBe(
      "Map unavailable — this browser couldn't start WebGL. Reload the page to try again.",
    );
    expect(ConnectMap.UNAVAILABLE_TEXT).toBe(panel.textContent);
  });

  it('runs the usual render code to the end: every map call is a no-op and load never fires', () => {
    const { ConnectMap, PlanLayers } = load();
    const onLoad = vi.fn();
    expect(() => {
      const map = ConnectMap.createMap(el, {});
      map.addControl({}, 'top-right');
      map.on('load', onLoad).on('click', 'x', () => {});
      map.once('load', onLoad);
      map.getCanvas().style.cursor = 'pointer';
      map.getSource('x').setData(FC);
      map.setPaintProperty('a', 'fill-color', '#fff');
      map.setFilter('a', ['==', 1, 1]);
      map.fitBounds([
        [0, 0],
        [1, 1],
      ]);
      map.resize();
      map.remove();
      expect(map.loaded()).toBe(false);
      expect(map.isStyleLoaded()).toBe(false);
      expect(map.getContainer()).toBe(el);
      expect(map.getStyle().layers).toEqual([]);
    }).not.toThrow();
    expect(onLoad).not.toHaveBeenCalled();
  });

  it('lets the ConnectMap and PlanLayers drawers run on it without throwing', () => {
    const { ConnectMap, PlanLayers } = load();
    const map = ConnectMap.createMap(el, {});
    expect(() => {
      ConnectMap.calmBasemap(map);
      ConnectMap.boundary(map, 'wards', FC, { activeWard: 'Kaura' });
      ConnectMap.points(map, 'sd', FC, {});
      ConnectMap.pins(map, 'pins', FC);
      ConnectMap.setSource(map, 'src', FC);
      ConnectMap.fit(map, FC, 40);
      ConnectMap.remove(map, ['wards', 'sd', 'pins']);
      PlanLayers.workAreas(map, { data: FC });
      PlanLayers.hulls(map, { data: FC });
      PlanLayers.pins(map, { data: FC });
      PlanLayers.footprints(map, { data: FC, splitByType: true });
      PlanLayers.setSource(map, 'src', FC);
      PlanLayers.remove(map, ['wa-fill']);
    }).not.toThrow();
  });

  it('is not a thenable, so awaiting it does not hang', async () => {
    const { ConnectMap } = load();
    const map = ConnectMap.createMap(el, {});
    await expect(Promise.resolve(map)).resolves.toBe(map);
  });

  it('says so in the console rather than failing silently', () => {
    const { ConnectMap } = load();
    ConnectMap.createMap(el, {});
    expect(console.warn).toHaveBeenCalledTimes(1);
    expect(String(console.warn.mock.calls[0][0])).toMatch(/unavailable/);
  });

  it('still throws a caller bug that is not a WebGL failure', () => {
    window.mapboxgl.Map = class {
      constructor() {
        throw new Error(
          "Invalid type: 'container' must be a String or HTMLElement.",
        );
      }
    };
    const { ConnectMap } = load();
    expect(() => ConnectMap.createMap(el, {})).toThrow(/Invalid type/);
    expect(el.querySelector('.connect-map-unavailable')).toBeNull();
  });
});

describe('createMap when WebGL starts', () => {
  it('returns the real Mapbox map with the same options as before', () => {
    const mbx = workingMapbox();
    window.mapboxgl = mbx;
    const { ConnectMap } = load();
    const map = ConnectMap.createMap(el, { center: [8.5, 9.6], zoom: 10 });
    expect(map).toBe(mbx.made[0]);
    expect(map).toBeInstanceOf(mbx.Map);
    expect(ConnectMap.isAvailable(map)).toBe(true);
    expect(map.options).toEqual({
      container: el,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [8.5, 9.6],
      zoom: 10,
      attributionControl: true,
      interactive: true,
      projection: undefined,
    });
    expect(el.querySelector('.connect-map-unavailable')).toBeNull();
  });

  it('still draws layers on a real map', () => {
    const mbx = workingMapbox();
    window.mapboxgl = mbx;
    const { ConnectMap } = load();
    const map = ConnectMap.createMap(el, {});
    ConnectMap.boundary(map, 'wards', FC, {});
    ConnectMap.points(map, 'sd', FC, {});
    expect(
      map.calls.filter((c) => c[0] === 'addLayer').map((c) => c[1]),
    ).toEqual(['wards-fill', 'wards-line', 'wards-label', 'sd']);
  });
});
