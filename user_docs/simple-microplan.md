# Simple Microplan

Simple Microplan is a quick way to make a microplan: add one or more boundaries (for example one per ward), split each into work areas, group them, and download the two files Connect imports: the work-area CSV and the Implementation Area CSV. Everything you make is saved, so you and your colleagues can open it again later.

Open it from the Labs overview: **Microplans → Simple Microplan**.

!!! note "Finding Microplans"
    The Labs overview has a single **Microplans** tile (visible to everyone who can see Workflows). It opens a Microplans page with three options: **CHC Microplan** (the existing program workspace), **CHC WA Revisit** (the existing WA Revisit workflow), and **Simple Microplan**.

---

## Getting started

1. Pick the organization, program or opportunity the microplan is for in the context picker at the top right (see **Who can see and change a microplan** below). Then, on the **Simple Microplans** page, type a name in **New microplan** and click **Create**. The name, your name (as the creator) and what is picked in the context picker are saved straight away.
2. The microplan opens on a map, with the steps on the right: **Find a place**, then **1 Boundary**, **2 Work areas**, **3 Grouping** and **4 Export**.
3. Work through the steps in order. With several boundaries, do steps 2 and 3 for each one (click it in the **Boundaries** list first), then export once in step 4. Your work saves automatically; you can also click **Save** (or press Ctrl/⌘ + S) at any time.

The list on the Simple Microplans page shows the microplans of what is picked in the context picker, with the organization, program and opportunity each belongs to, who created it, when it was created and last saved, and how many work areas it has. Click a name to open it.

## Find a place

Use the **Find a place** box to move the map before you start.

- Type a town, ward, LGA or state and press **Enter** (or click **Search**). If more than one place matches, pick the right one from the list. The map zooms there and drops a red pin.
- You can also type a coordinate as **lat, long** (for example `11.108, 7.733`) to jump straight to it.

Search runs only when you press Enter or Search, not while you type.

## Step 1: Boundary

The boundary is the area your microplan covers. You can add several (for example one per ward), each with its own settings. Choose a **Boundary source**, then click **Add boundary**:

| Source | How to use it |
| --- | --- |
| **Draw boundary on map** | Click **Start drawing**, click the map to place each corner, then click **Finish** (or double-click, or press Esc). The boundary is added as soon as you finish. |
| **Upload boundary (GeoJSON)** | Choose a GeoJSON file with your polygon(s), then click **Add boundary**. |
| **Bounding box** | Type `minLon, minLat, maxLon, maxLat`, then click **Add boundary**. |
| **Ward lookup (admin GeoJSON)** | Load an admin-boundaries GeoJSON (for example all wards in Nigeria), search for wards and add them, or load a batch CSV of state / LGA / ward names. Click **Add boundary**. Each ward keeps its own name, LGA and state. |
| **Base point grid** | Type or **Pick** a centre point and set the number of rows and columns. The boundary is a rectangle of that many cells (cell size from step 2), and the grid in step 2 lines up with the same point. |

Each boundary shows as a dashed line; the one you are working on is drawn in blue. **Add boundary** adds to the boundaries you already have (adding the same one twice does nothing). **Clear all** removes every boundary; the work areas stay.

### Boundaries list

Once a boundary is set, the **Boundaries** list shows each one with its name, size, number of work areas and cell size. A boundary that comes in several pieces (a ward in two parts, or an uploaded file with several polygons) is added as one boundary per piece.

- **Click a boundary** to work on it. Steps 2 and 3 then use that boundary and its own settings (you can also pick it in the **Boundary** box at the top of Step 2).
- **Name:** each boundary is an **Implementation Area** on Connect. The name starts as the ward name (numbered, like **Doka 1** and **Doka 2**, for a ward in several pieces). A boundary with no ward of its own takes the ward of the work areas inside it once you have made them, or else **Implementation Area 1**, **2** …. Type in the box to change it. Connect links work areas to their Implementation Area by exact name, so give each its own; a line under the list warns you if two share a name.
- **×** removes a boundary and the work areas inside it (you are asked first).

## Step 2: Work areas

Work areas are the small areas a field worker visits. With several boundaries, pick the one to work on in the **Boundary** box at the top of the step. The settings in this step (cell size, visit ratio, minimum buildings, base point, slug prefix, drop empty cells, singleton columns) are kept separately for each boundary, so one ward can use 50 m cells and another 20 m. A new boundary starts with the settings currently showing.

Choose how to make them with the three buttons at the top of the step.

**Slug prefix** (two letters at the start of each work-area ID) and **Country ISO** apply to every method. Leave them blank to fill them automatically from the ward name and the map location.

**Ward, LGA and State** for each new work area come from the ward you picked in **Ward lookup**, or else from the map location (OpenStreetMap). OpenStreetMap often has no ward (in Nigeria it usually has none), so any that are still blank are filled from Labs' own ward boundaries (the **Enriched Boundaries** the CHC microplan uses): the ward that contains the centre of the work area, with its LGA and state. A value that is already there is never changed. Where Labs has no ward boundaries, they stay blank (see Step 4).

!!! note "Work areas made before this filling was introduced"
    Work areas created before this change was made are not updated automatically. To get a Ward, LGA and State filled in for one, remove it and make it again.

### Auto-grid

Splits the boundary into square cells and counts the buildings in each one (from Google Open Buildings).

- **Cell size (m)**: the side of each square.
- **Visit ratio**: Expected Visit Count = buildings × ratio.
- **Minimum buildings per work area** (0 = off): leaves out cells with fewer buildings than this, so every work area that is made has at least this many. Work areas stay one square cell of the chosen size; nothing is merged. The log says how many cells were left out.
- **Grid base point** (optional): makes the cell corners line up with a GPS point. Type it or click **Pick** and click the map.
- **Drop empty cells**: leaves out cells with no buildings.
- **Add singleton columns**: adds the keepSingleton columns for cells with only one building.

Click **Generate grid** to make the grid for the boundary you are working on. Downloading buildings can take a little while for a big area; the progress bar and log show what is happening. If that boundary already has work areas, you are asked before they are replaced (their groups go with them); work areas in other boundaries are not touched.

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

Groups (work area groups, WAGs) bundle work areas together, for example one group per field worker. With several boundaries, group each one in turn with **Group** set to **This boundary**: the grouping settings (work areas or buildings per WAG, tolerance, start corner, lone-work-area distance) are kept separately for each boundary, like the Step 2 settings.

### Auto-group

Choose how to size each group:

- **By work areas**: set **Work areas per WAG**. Each group takes a work area and its nearest neighbours.
- **By buildings**: set **Buildings per WAG** and a **Tolerance** (±10% by default). For a target of 100, every group aims for 90–110 buildings in total. Every work area in a group shares a boundary with another work area in the same group, so groups are never scattered or broken into pieces.

Then choose:

- **Group**: **This boundary** (the boundary you are working on, with its own grouping settings), **All work areas**, or **Selected only** to regroup just the work areas you have selected (other groups stay as they are, and new group numbers carry on after them).
- **Start from**: the corner the grouping starts from. A different corner gives a different layout. **Best of 4 corners** (the default) tries all four and keeps the layout with the most groups in range; when grouping by work areas it starts top-left.

**Lone work areas join a group within (m)** (200 by default): if grouping leaves a WAG with only one work area, that work area joins the group of the nearest work area within this distance, measured edge to edge. When grouping by buildings it prefers a nearby group that stays within the range, and a single work area that already has enough buildings to be a WAG by itself (in range, or above it) stays on its own. Set it to 0 to keep lone work areas as their own groups. A work area placed this way is the one exception to the "shares a boundary" rule.

Click **Auto-group**. A progress bar shows while it works, and a summary says how many groups were made. Groups are named after their ward: `Doka-G-001`, `Doka-G-002`, and so on, numbered separately for each ward. Spaces in a ward name become `_` (`Kauran_Mata-G-001`), because group names must not contain spaces. A group that crosses a ward line takes the ward most of its work areas are in. Work areas with no ward fall back to their slug prefix (`WA-G-001`).

You can regroup as often as you like: change the settings and click **Regroup**, or click **Try another layout** to start from the next corner. **Undo** puts the previous groups back (several steps are remembered).

When grouping by buildings, some groups may not fit the range:

- **Below the range**: a patch of work areas that cannot grow without breaking the group, for example an isolated patch, or one hemmed in by full groups.
- **Above the range**: a single work area that already has more buildings than the limit.
- **Above the range after a lone work area joined**: no group within the distance had room, so the lone work area joined the nearest group anyway. Set the lone-work-area distance to 0 if you would rather keep it apart.

These are marked ⚠ in the group list. The list shows each group's total buildings and number of work areas (WA).

### Manual grouping

1. Click **Select work areas**, then click work areas on the map to select them. You can also Ctrl/⌘/Shift-click work areas at any time, or click a group in the list to select all of it.
2. Type a group name (or pick an existing one) and click **Assign**. Any spaces you type become `_`.
3. The work areas move to that group and the selection is cleared, ready for the next group.

With work areas selected you can also **Delete selected work areas**. Click **Done selecting** to go back to normal clicking.

The list at the bottom of the step shows every group, its colour on the map and how many work areas it has.

## Step 4: Export

There are two files to download, one for each of Connect's imports. Each covers every boundary in the microplan, so with several wards you still download just two files.

**Work areas.** Click **Download CSV**. The columns, in order, are the same as the CHC Microplan export: Area Slug, Ward, Centroid, Boundary, Building Count, Expected Visit Count, Target Population, LGA, State, Implementation Area, Work Area Group Name (plus the singleton columns if you added them). Each work area's **Implementation Area** is the name of the boundary it sits in (see Step 1). Change the **File name** if you like.

**Implementation Area.** Click **Download Implementation Area CSV**. It has one row per boundary in the **Boundaries** list, each a single polygon: Implementation Area Name, Centroid and Boundary. Upload it to Connect as well, so each work area is linked to its Implementation Area. The file is named after your microplan unless you type a **File name**.

Connect will not import a work area with a blank Ward, LGA or State. Most work areas get these filled in automatically (see Step 2), but where Labs has no ward boundaries they may still be blank. If any work areas have a blank field, a line under **Download CSV** says how many (for example "3 work areas have no Ward/LGA/State — Connect will reject them"). You can still download the file: fill those cells in before you import it, or remove those work areas and make them again.

## Saving and renaming

- **Auto-save** is on by default: your changes save by themselves a few seconds after you stop editing. Untick **Auto-save** (next to the Save button) to save only when you choose; your choice is remembered in this browser.
- **Save** saves straight away and keeps everything: the boundaries and their names, the work areas, the groups, and the settings for each boundary. The status beside it shows **Unsaved changes**, **Saving…** or when it last saved.
- Both downloads also save, so what you download is what is saved.
- You are warned if you try to leave with unsaved changes.
- **Rename** (or click the name) to change the microplan's name. Press Enter to keep it, Esc to cancel. Renaming saves straight away.
- Building footprints are not saved with the microplan. They download again the first time they are needed after you reopen it.

## Who can see and change a microplan

Simple Microplan uses the **context picker** (the organization, program and opportunity selector at the top right of the page). You do not need anyone to give you a role: what you can see follows the organizations, programs and opportunities your Connect account gives you.

- **A new microplan belongs to what is picked** when you click **Create**: the organization, the program and the opportunity, whichever of them are picked. The list shows them in the **Context** column, and the microplan shows them under its name.
- **The list shows the microplans of what is picked now.** The most specific choice wins: with an opportunity picked, you see that opportunity's microplans; otherwise, with a program picked, that program's; otherwise, with an organization picked, that organization's. With nothing picked, you see only the microplans you created.
- **Who can open and change a microplan**: anyone who can pick its context in their own context picker (its opportunity; or, if it has none, its program; or, if it has neither, its organization), the person who created it, and Dimagi staff. They can open it, change it and rename it.
- **A microplan made with nothing picked** (including every microplan made before Simple Microplan used the context picker) has no context. Only the person who created it and Dimagi staff can open or change it, and it is listed for its creator when nothing is picked.
- **Only the person who created a microplan, or Dimagi staff, can delete it.** The **Delete** button is shown only to them.
- A microplan you cannot open does not appear in your list, and its link says the page is not found.

### In a program with workspace roles (for example Auto-Connect or CLP Stage 4)

Programs with workspace roles (such as Auto-Connect and CLP Stage 4) keep their microplans inside Simple Microplan, and only there. With such a program picked in the context
