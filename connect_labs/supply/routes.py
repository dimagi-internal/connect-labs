"""Corridor geometry for the flow map.

Straight lines between nodes read as "abstract relationship"; goods moving
along a real corridor read as logistics. These are hand-digitised waypoint
chains for the corridors the demo actually uses — the Kano–Maiduguri road, the
Djibouti–Addis corridor, the Lomé–Ouagadougou corridor, and the Port Sudan
approaches — plus a small sea-lane graph for the maritime legs.

Kept as data rather than computed live: routes are baked onto shipments at
creation time so the map never waits on a routing service.
"""

# Interior waypoints keyed by (origin node name, destination node name).
# Coordinates are (lon, lat) and follow the actual road alignment closely
# enough to read correctly at country zoom.
ROAD_CORRIDORS = {
    # Nigeria — the A3/A4 trunk east from Kano through Damaturu to Maiduguri
    ("Kano RUTF Plant", "Kano Central Warehouse"): [],
    ("Kano Central Warehouse", "Maiduguri Distribution Hub"): [
        (8.9500, 11.9800),  # Wudil
        (9.7500, 11.8300),  # Azare
        (10.6000, 11.7500),  # Potiskum
        (11.9660, 11.7480),  # Damaturu
        (12.6000, 11.7800),  # Benisheikh
    ],
    ("Kano Central Warehouse", "Damaturu Distribution Hub"): [
        (8.9500, 11.9800),
        (9.7500, 11.8300),
        (10.6000, 11.7500),
    ],
    ("Maiduguri Distribution Hub", "Bama Health Post"): [(13.4200, 11.6800)],
    ("Maiduguri Distribution Hub", "Monguno Health Post"): [(13.3600, 12.2000)],
    # Ethiopia — the Djibouti corridor and the road south-east to the Somali region
    ("Port of Djibouti", "Addis Central Depot"): [
        (42.8900, 11.5000),  # Dikhil
        (41.8661, 9.5931),  # Dire Dawa
        (40.2000, 9.4000),  # Awash
        (39.2700, 9.0500),  # Nazret / Adama
    ],
    ("Addis Ababa RUTF Plant", "Addis Central Depot"): [],
    ("Addis Central Depot", "Dire Dawa Transit Store"): [
        (39.2700, 9.0500),
        (40.2000, 9.4000),
    ],
    ("Addis Central Depot", "Gode Distribution Hub"): [
        (39.2700, 9.0500),  # Adama
        (40.7700, 8.5400),  # Asela road junction
        (41.9000, 7.0000),  # Imi approach
        (43.1000, 6.2000),
    ],
    ("Dire Dawa Transit Store", "Gode Distribution Hub"): [
        (42.1000, 8.6000),
        (43.0000, 7.0000),
    ],
    ("Addis Central Depot", "Jijiga Distribution Hub"): [
        (39.2700, 9.0500),
        (41.8661, 9.5931),  # Dire Dawa
        (42.1400, 9.3600),  # Harar
    ],
    # Burkina Faso — the Lomé corridor north, then out to the Sahel
    ("Port of Lomé", "Ouagadougou Central Warehouse"): [
        (1.1300, 8.0000),  # Atakpamé
        (0.8300, 9.5500),  # Kara
        (0.3600, 10.8800),  # Dapaong
        (-0.3600, 11.7800),  # Tenkodogo approach
    ],
    ("Ouagadougou RUTF Plant", "Ouagadougou Central Warehouse"): [],
    ("Ouagadougou Central Warehouse", "Djibo Distribution Hub"): [
        (-1.6100, 12.8600),  # Kongoussi road
        (-1.6900, 13.5800),
    ],
    ("Ouagadougou Central Warehouse", "Dori Distribution Hub"): [
        (-0.8700, 12.7000),  # Ziniaré / Kaya
        (-0.3600, 13.3000),
    ],
    ("Djibo Distribution Hub", "Sebba Nutrition Site"): [(-0.6000, 13.8000)],
    # Sudan — Port Sudan inland, and the long haul west into Darfur
    ("Port Sudan", "Khartoum Central Warehouse"): [
        (36.4000, 18.7000),  # Haiya
        (34.5000, 17.4000),  # Atbara approach
        (33.9000, 16.6000),  # Shendi
    ],
    ("Port Sudan", "Kassala Forward Store"): [(36.9000, 17.5000)],
    ("Khartoum Central Warehouse", "Kassala Forward Store"): [(34.3000, 15.4000)],
    ("Khartoum Central Warehouse", "El Fasher Distribution Hub"): [
        (31.6000, 15.1000),  # Omdurman west
        (30.2000, 14.4000),  # El Obeid road
        (28.4000, 13.9000),  # En Nahud
        (26.5000, 13.6000),  # Umm Keddada approach
    ],
    ("Khartoum Central Warehouse", "Nyala Distribution Hub"): [
        (31.6000, 15.1000),
        (30.2000, 14.4000),
        (27.5000, 13.0000),
        (25.7000, 12.3000),
    ],
    ("Kassala Forward Store", "El Fasher Distribution Hub"): [
        (33.5000, 15.2000),
        (30.2000, 14.4000),
        (27.0000, 13.8000),
    ],
    ("El Fasher Distribution Hub", "Tawila Nutrition Site"): [(25.2000, 13.7500)],
    ("El Fasher Distribution Hub", "Kebkabiya Nutrition Site"): [(24.7000, 13.6000)],
    ("Gode Distribution Hub", "Kelafo Nutrition Site"): [(43.9000, 5.8000)],
    ("Nyala Distribution Hub", "Kebkabiya Nutrition Site"): [(24.4000, 12.8000)],
}

# Sea lanes, as waypoint chains that respect the actual chokepoints. A great
# circle from Europe to Port Sudan would cut across Egypt; real vessels transit
# Suez and the Red Sea, and East African traffic rounds Bab-el-Mandeb.
SEA_LANES = {
    ("Port of Lagos (Apapa)", "Port Sudan"): [
        (3.0000, 4.5000),  # Gulf of Guinea
        (8.5000, 3.0000),
        (14.0000, -2.0000),
        (30.0000, -32.0000),  # around the Cape
        (40.0000, -25.0000),
        (45.0000, -5.0000),
        (51.0000, 12.0000),  # Gulf of Aden
        (43.4000, 12.6000),  # Bab-el-Mandeb
        (39.5000, 17.0000),  # Red Sea
    ],
    ("Port of Djibouti", "Port Sudan"): [
        (43.3000, 12.4000),  # Bab-el-Mandeb
        (41.5000, 15.0000),
        (39.5000, 17.5000),
    ],
    ("Port of Lagos (Apapa)", "Port of Lomé"): [(2.5000, 5.9000)],
}


def waypoints_for(origin_name, destination_name):
    """Interior waypoints for a leg, or None when we have no corridor for it.

    Direction-agnostic: a leg digitised one way is reused reversed.
    """
    key = (origin_name, destination_name)
    for table in (ROAD_CORRIDORS, SEA_LANES):
        if key in table:
            return list(table[key])
        reverse = (destination_name, origin_name)
        if reverse in table:
            return list(reversed(table[reverse]))
    return None


def build_route(origin, destination, via_nodes=()):
    """A coordinate chain from origin to destination through any hub stops.

    Each hop is expanded with its digitised corridor where we have one, so the
    rendered line follows roads and sea lanes rather than cutting straight
    across terrain.
    """
    stops = [origin] + list(via_nodes) + [destination]
    coords = []
    for start, end in zip(stops, stops[1:]):
        if start.location is None or end.location is None:
            continue
        if not coords:
            coords.append((start.location.x, start.location.y))
        interior = waypoints_for(start.name, end.name) or []
        coords.extend(interior)
        coords.append((end.location.x, end.location.y))
    # Drop consecutive duplicates — a hub appears as both an arrival and a
    # departure point.
    deduped = [coords[0]] if coords else []
    for point in coords[1:]:
        if point != deduped[-1]:
            deduped.append(point)
    return deduped if len(deduped) >= 2 else None


def coverage_report(node_names):
    """Which legs between known nodes still lack digitised geometry."""
    known = set()
    for table in (ROAD_CORRIDORS, SEA_LANES):
        known.update(table.keys())
    return sorted(pair for pair in known if pair[0] in node_names and pair[1] in node_names)
