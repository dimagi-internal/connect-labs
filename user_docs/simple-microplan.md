# Simple Microplan

Simple Microplan is a quick way to make a test microplan: pick an area, split it into work areas, group them, and download the work-area CSV. Everything you make is saved, so you and your colleagues can open it again later.

Open it from the Labs overview: **Microplans → Simple Microplan**.

!!! note "Finding Microplans"
    The Labs overview has a single **Microplans** tile (visible to everyone who can see Workflows). It opens a Microplans page with three options: **CHC Microplan** (the existing program workspace), **CHC WA Revisit** (the existing WA Revisit workflow), and **Simple Microplan**.

---

## Getting started

1. On the **Simple Microplans** page, type a name in **New microplan** and click **Create**. The name and your name (as the creator) are saved straight away.
2. The microplan opens on a map, with the steps on the right: **Find a place**, then **1 Boundary**, **2 Work areas**, **3 Grouping** and **4 Export**.
3. Work through the steps in order. Your work saves automatically; you can also click **Save** (or press Ctrl/⌘ + S) at any time.

The list on the Simple Microplans page shows every saved microplan, who created it, when it was created and last saved, and how many work areas it has. Click a name to open it.

## Find a place

Use the **Find a place** box to move the map before you start.

- Type a town, ward, LGA or state and press **Enter** (or click **Search**). If more than one place matches, pick the right one from the list. The map zooms there and drops a red pin.
- You can also type a coordinate as **lat, long** (for example `11.108, 7.733`) to jump straight to it.

Search runs only when you press Enter or Search, not while you type.

## Step 1: Boundary

The boundary is the area your microplan covers. Choose a **Boundary source**, then set it:

| Source | How to use it |
| --- | --- |
| **Draw boundary on map** | Click **Start drawing**, click the map to place each corner, then click **Finish** (or double-click, or press Esc). The boundary is set as soon as you finish. |
| **Upload boundary (GeoJSON)** | Choose a GeoJSON file with your polygon(s), then click **Use as boundary**. |
| **Bounding box** | Type `minLon, minLat, maxLon, maxLat`, then click **Use as boundary**. |
| **Ward lookup (admin GeoJSON)** | Load an admin-boundaries GeoJSON (for example all wards in Nigeria), search for wards and add them, or load a batch CSV of state / LGA / ward names. Click **Use as boundary**. Each ward keeps its own name, LGA and state. |
| **Base point grid** | Type or **Pick** a centre point and set the number of rows and columns. The boundary is a rectangle of that many cells (cell size from step 2), and the grid in step 2 lines up with the same point. |

The boundary shows as a dashed black line. **Clear** removes it; any work areas you already made stay.

## Step 2: Work areas

Work areas are the small areas a field worker visits. Choose how to make them with the three buttons at the top of the step.

**Slug prefix** (two letters at the start of each work-area ID) and **Country ISO** apply to every method. Leave them blank to fill them automatically from the ward name and the map location.

### Auto-grid

Splits the boundary into square cells and counts the buildings in each one (from Google Open Buildings).

- **Cell size (m)**: the side of each square.
- **Visit ratio**: Expected Visit Count = buildings × ratio.
- **Grid base point** (optional): makes the cell corners line up with a GPS point. Type it or click **Pick** and click the map.
- **Drop empty cells**: leaves out cells with no buildings.
- **Add singleton columns**: adds the keepSingleton columns for cells with only one building.

Click **Generate grid**. Downloading buildings can take a little while for a big area; the progress bar and log show what is happening. Generating again adds more cells; it does not replace the ones you have.

### Draw

Draw each work area yourself. Click **Draw work areas**, click the map to place corners, then click **Finish area** (or double-click). You can keep drawing the next one straight away. Click **Stop drawing** or press Esc when you are done.

### Smart draw

Like Draw, but it tidies the shape for you:

- A corner placed within the **Snap distance** of the boundary or another work area lands exactly on it (an orange dot shows where it will snap).
- An edge between two snapped corners follows that line, curves and all.
- The new area is cut to the boundary and never overlaps an existing work area.

So you can sketch roughly up to an edge and the gap is filled exactly.

### Building counts for drawn areas

**Autofill from building footprints** is off by default.

- **Off**: each drawn work area gets the **Minimum fill** number (default 1) as its Building Count and Expected Visit Count.
- **On**: the buildings inside each drawn area are counted, and Expected Visit Count equals that count. The first count in a session downloads the buildings for your boundary, so it may take a moment.

**Remove all work areas** deletes every work area (the boundary stays).

## Step 3: Grouping

Groups (work area groups, WAGs) bundle work areas together, for example one group per field worker.

### Auto-group

Choose how to size each group:

- **By work areas**: set **Work areas per WAG**. Each group takes a work area and its nearest neighbours.
- **By buildings**: set **Buildings per WAG** and a **Tolerance** (±10% by default). For a target of 100, every group aims for 90–110 buildings in total. Every work area in a group shares a boundary with another work area in the same group, so groups are never scattered or broken into pieces.

Then choose:

- **Group**: **All work areas**, or **Selected only** to regroup just the work areas you have selected (other groups stay as they are, and new group numbers carry on after them).
- **Start from**: the corner the grouping starts from. A different corner gives a different layout. **Best of 4 corners** (the default) tries all four and keeps the layout with the most groups in range; when grouping by work areas it starts top-left.

**Lone work areas join a group within (m)** (200 by default): if grouping leaves a WAG with only one work area, that work area joins the group of the nearest work area within this distance, measured edge to edge. When grouping by buildings it prefers a nearby group that stays within the range, and a single work area that already has enough buildings to be a WAG by itself (in range, or above it) stays on its own. Set it to 0 to keep lone work areas as their own groups. A work area placed this way is the one exception to the "shares a boundary" rule.

Click **Auto-group**. A progress bar shows while it works, and a summary says how many groups were made. Groups are named like `WA-G-001`, `WA-G-002`.

You can regroup as often as you like: change the settings and click **Regroup**, or click **Try another layout** to start from the next corner. **Undo** puts the previous groups back (several steps are remembered).

When grouping by buildings, some groups may not fit the range:

- **Below the range**: a patch of work areas that cannot grow without breaking the group, for example an isolated patch, or one hemmed in by full groups.
- **Above the range**: a single work area that already has more buildings than the limit.
- **Above the range after a lone work area joined**: no group within the distance had room, so the lone work area joined the nearest group anyway. Set the lone-work-area distance to 0 if you would rather keep it apart.

These are marked ⚠ in the group list. The list shows each group's total buildings and number of work areas (WA).

### Manual grouping

1. Click **Select work areas**, then click work areas on the map to select them. You can also Ctrl/⌘/Shift-click work areas at any time, or click a group in the list to select all of it.
2. Type a group name (or pick an existing one) and click **Assign**.
3. The work areas move to that group and the selection is cleared, ready for the next group.

With work areas selected you can also **Delete selected work areas**. Click **Done selecting** to go back to normal clicking.

The list at the bottom of the step shows every group, its colour on the map and how many work areas it has.

## Step 4: Export

Click **Download CSV** to save the work areas. The file has the same columns and format as the grid generator: Area Slug, Ward, Centroid, Boundary, Building Count, Expected Visit Count, LGA, State, Target Population, Work Area Group Name (plus the singleton columns if you added them). Change the **File name** if you like.

## Saving and renaming

- **Auto-save** is on by default: your changes save by themselves a few seconds after you stop editing. Untick **Auto-save** (next to the Save button) to save only when you choose; your choice is remembered in this browser.
- **Save** saves straight away and keeps everything: the boundary, the work areas, the groups and your settings. The status beside it shows **Unsaved changes**, **Saving…** or when it last saved.
- **Download CSV** also saves, so what you download is what is saved.
- You are warned if you try to leave with unsaved changes.
- **Rename** (or click the name) to change the microplan's name. Press Enter to keep it, Esc to cancel. Renaming saves straight away.
- Building footprints are not saved with the microplan. They download again the first time they are needed after you reopen it.

## Who can see and change a microplan

- Everyone who can open Simple Microplan sees every saved microplan and who created it.
- Only the person who created a microplan (and Dimagi staff) can change, rename or delete it.
- Anyone else opens it **view only**: they can look around the map, see the groups and download the CSV, but cannot change it.

## Getting help in the app

Each page has a **?** button that opens a searchable, chat-style guide covering every part of the microplan tool. Each step also has its own **?** link that jumps straight to the relevant section of that guide.

## Tips and troubleshooting

- **The map is empty or grey**: check your internet connection and reload. Switch between **Streets** and **Satellite** at the top right of the map.
- **"No coverage for country"**: fill in **Country ISO** with the two-letter code (for example NG).
- **Smart draw added nothing**: the shape was outside the boundary or already covered by other work areas.
- **Building counts are 0**: the area may not be covered by Google Open Buildings, or the country could not be worked out; set **Country ISO**.
- Use the **⇲** button on the map to zoom back to the whole microplan.
