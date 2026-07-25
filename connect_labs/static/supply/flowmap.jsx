/* The flow map: shipments moving along real corridors over the IPC
   famine-phase choropleth.

   Deliberate choices, from the visualisation research:
   - Mapbox owns the camera; deck.gl rides along via MapboxOverlay in overlay
     mode (see the note at the overlay construction for why not interleaved).
   - The IPC layer is a Mapbox fill layer, not a deck GeoJsonLayer, so the
     interleaved flows naturally sit above it. Its colours are the official
     IPC phase palette and are used for nothing else on the page.
   - TripsLayer animates by advancing `currentTime` only. `data` and the
     accessors stay identical between frames — rebuilding them per frame is
     the classic way to make this janky.
*/

const IPC_COLOURS = {
  1: '#CDFACD',
  2: '#FAE61E',
  3: '#E67800',
  4: '#C80000',
  5: '#640000',
};

const STATUS_COLOUR = {
  planned: [148, 163, 184],
  in_transit: [13, 122, 95],
  delivered: [37, 99, 235],
  confirmed: [100, 116, 139],
};

const NODE_COLOUR = {
  factory: [13, 122, 95],
  port: [30, 64, 175],
  warehouse: [120, 113, 108],
  distribution_hub: [180, 83, 9],
  delivery_point: [136, 19, 55],
};

/* One shared deck overlay per page. Layers are toggled rather than recreated:
   layer construction is cheap, GPU resource churn is not. */
function useFlowMap({ containerRef, nodes, shipments, showIpc, focusCountry }) {
  const mapRef = useRef(null);
  const overlayRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [time, setTime] = useState(0);

  // Trips are precomputed once per shipment set: each route coordinate gets a
  // timestamp so the comet trail advances along the real corridor.
  const trips = useMemo(() => buildTrips(shipments), [shipments]);
  const loopLength = 1800;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return undefined;
    // Without the libraries or a token there is nothing to initialise. Bailing
    // out here keeps a missing map from taking the whole page down with it.
    if (!window.mapboxgl || !window.deck || !mapAvailable()) return undefined;

    window.mapboxgl.accessToken = window.SUPPLY_MAPBOX_TOKEN;
    const map = new window.mapboxgl.Map({
      container: containerRef.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: focusCountry ? COUNTRY_CENTRES[focusCountry] : [24, 12],
      zoom: focusCountry ? 4.6 : 3.1,
      attributionControl: true,
    });
    mapRef.current = map;

    map.on('load', () => {
      // IPC choropleth underneath everything, as a Mapbox fill layer.
      map.addSource('ipc', {
        type: 'geojson',
        data: '/static/supply/geo/admin1_ipc.geojson',
      });
      map.addLayer({
        id: 'ipc-fill',
        type: 'fill',
        source: 'ipc',
        paint: {
          'fill-color': [
            'match',
            ['get', 'ipc_phase'],
            1,
            IPC_COLOURS[1],
            2,
            IPC_COLOURS[2],
            3,
            IPC_COLOURS[3],
            4,
            IPC_COLOURS[4],
            5,
            IPC_COLOURS[5],
            '#cccccc',
          ],
          'fill-opacity': 0.45,
        },
      });
      map.addLayer({
        id: 'ipc-outline',
        type: 'line',
        source: 'ipc',
        paint: {
          'line-color': '#ffffff',
          'line-width': 0.4,
          'line-opacity': 0.35,
        },
      });

      // Overlay mode (deck draws into its own canvas above the map) rather
      // than interleaved. Interleaved would let Mapbox labels sit above the
      // flows, but it renders nothing under software WebGL, which is what
      // headless verification runs on — a prettier map that cannot be checked
      // is the worse trade.
      const overlay = new window.deck.MapboxOverlay({
        interleaved: false,
        layers: [],
      });
      map.addControl(overlay);
      overlayRef.current = overlay;
      // The card may still have been laying out when the map initialised, in
      // which case the canvas is stuck at Mapbox's default size.
      map.resize();
      setReady(true);
    });

    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, [containerRef, focusCountry]);

  // Toggle the IPC layers without tearing the map down.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const visibility = showIpc ? 'visible' : 'none';
    ['ipc-fill', 'ipc-outline'].forEach((id) => {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility);
    });
  }, [showIpc, ready]);

  // Animate: only `currentTime` changes between frames.
  useEffect(() => {
    if (!ready) return undefined;
    let frame;
    const tick = () => {
      setTime((t) => (t + 4) % loopLength);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [ready]);

  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay || !ready) return;
    overlay.setProps({
      layers: [
        // Every corridor, drawn faintly and always present: without this the
        // network only exists wherever a comet happens to be at that instant,
        // and the map reads as scattered dots rather than a supply chain.
        new window.deck.PathLayer({
          id: 'corridors',
          data: trips,
          getPath: (d) => d.path,
          getColor: [255, 255, 255, 55],
          getWidth: 2,
          widthUnits: 'pixels',
          widthMinPixels: 1.5,
          capRounded: true,
          jointRounded: true,
        }),
        new window.deck.ScatterplotLayer({
          id: 'nodes',
          data: nodes,
          getPosition: (n) => [n.lon, n.lat],
          getRadius: (n) => (n.kind === 'factory' || n.kind === 'port' ? 9 : 6),
          radiusUnits: 'pixels',
          getFillColor: (n) => NODE_COLOUR[n.kind] || [120, 120, 120],
          stroked: true,
          getLineColor: [255, 255, 255, 200],
          lineWidthMinPixels: 1,
          pickable: true,
        }),
        new window.deck.TripsLayer({
          id: 'trips',
          data: trips,
          getPath: (d) => d.path,
          getTimestamps: (d) => d.timestamps,
          getColor: (d) => STATUS_COLOUR[d.status] || [200, 200, 200],
          widthMinPixels: 5,
          capRounded: true,
          jointRounded: true,
          // Long enough that a corridor reads as a moving line rather than a dot.
          trailLength: 600,
          currentTime: time,
        }),
      ],
    });
  }, [ready, nodes, trips, time]);

  return { ready };
}

/* Give every shipment a synthetic timeline along its own route so the loop
   reads as continuous movement. Shipments that have not departed are omitted
   rather than drawn frozen at the origin. */
function buildTrips(shipments) {
  return (shipments || [])
    .filter((s) => s.route && s.route.length > 1 && s.status !== 'planned')
    .map((s) => {
      const n = s.route.length;
      const span = 1200;
      const offset = (s.id * 137) % 400; // stagger departures so they don't pulse in unison
      return {
        id: s.id,
        status: s.status,
        path: s.route,
        timestamps: s.route.map((_p, i) => offset + (i / (n - 1)) * span),
      };
    });
}

const COUNTRY_CENTRES = {
  NG: [8.5, 9.5],
  SD: [30.0, 15.5],
  ET: [39.5, 8.5],
  BF: [-1.7, 12.5],
};

function IpcLegend() {
  return (
    <div className="ipc-legend">
      <div className="legend-title">Food insecurity (IPC phase)</div>
      {[1, 2, 3, 4, 5].map((phase) => (
        <div className="legend-row" key={phase}>
          <span
            className="legend-swatch"
            style={{ background: IPC_COLOURS[phase] }}
          />
          <span>
            {phase} — {IPC_PHASE_LABELS[phase]}
          </span>
        </div>
      ))}
      <div className="legend-note">Phase classifications are synthetic.</div>
    </div>
  );
}

const IPC_PHASE_LABELS = {
  1: 'Minimal',
  2: 'Stressed',
  3: 'Crisis',
  4: 'Emergency',
  5: 'Catastrophe / Famine',
};

/* The map needs a Mapbox token, which only a configured environment has.
   Without one we render an explanation — a missing map must not take the
   whole page down with it. */
function mapAvailable() {
  return (
    Boolean(window.SUPPLY_MAPBOX_TOKEN) &&
    Boolean(window.mapboxgl) &&
    Boolean(window.deck)
  );
}

function MapUnavailable({ shipments, height }) {
  const routed = (shipments || []).filter((s) => s.route && s.route.length > 1);
  return (
    <div className="map-unavailable" style={{ height: height || 520 }}>
      <div className="map-unavailable-title">Map unavailable</div>
      <div className="map-unavailable-body">
        This environment has no Mapbox access token configured, so the network
        map cannot be drawn. Everything it visualises is still in the tables
        below — {routed.length} consignment
        {routed.length === 1 ? '' : 's'} with routed corridors.
      </div>
    </div>
  );
}

function FlowMap({ nodes, shipments, focusCountry, height }) {
  const containerRef = useRef(null);
  const [showIpc, setShowIpc] = useState(true);
  const { ready } = useFlowMap({
    containerRef,
    nodes,
    shipments,
    showIpc,
    focusCountry,
  });

  if (!mapAvailable()) {
    return <MapUnavailable shipments={shipments} height={height} />;
  }

  return (
    <div className="flowmap-wrap" style={{ height: height || 520 }}>
      <div ref={containerRef} className="flowmap" />
      {!ready ? <div className="flowmap-loading">Loading map…</div> : null}
      <div className="flowmap-controls">
        <label className="check-row">
          <input
            type="checkbox"
            checked={showIpc}
            onChange={() => setShowIpc((v) => !v)}
          />
          <span>Food insecurity</span>
        </label>
      </div>
      {showIpc ? <IpcLegend /> : null}
    </div>
  );
}
