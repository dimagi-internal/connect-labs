/**
 * Tell the embedded agent what the page now shows.
 *
 * The canopy SDK's panel (`canopy_host/panel.html`, >= 0.5) declares the run's or
 * page's selection when the page renders and exposes `window.canopyHost`; a report
 * that drills (an organisation, an opportunity, a worker) narrows that selection
 * here so the agent's "these workers" means the ones on screen. The resource, its
 * scope and the tools on offer stay as the server declared them -- the SDK keeps
 * them -- and so do the server's filters, which the drill is layered over.
 * A no-op on a page with no panel (a workflow or page that does not share).
 *
 * The same for a render drawn unsandboxed (its `view.shareSelection` calls this
 * directly) and sandboxed (the frame's call is a bridge request the host
 * sanitises -- host.ts `sanitizeSelection` -- and hands here).
 */

export interface AgentSelection {
  visible_ids?: string[];
  drilled?: Record<string, unknown>;
}

interface CanopyHost {
  pageState(): { filters?: Record<string, unknown> } | null;
  updatePageState(patch: Record<string, unknown>): unknown;
}

export function createAgentSelection(
  win: Window,
  doc: Document,
): (selection: AgentSelection) => void {
  let baseFilters: Record<string, unknown> | null = null;
  let last: AgentSelection | null = null;
  function share(selection: AgentSelection): void {
    last = selection;
    const host = (win as unknown as { canopyHost?: CanopyHost }).canopyHost;
    if (!host || typeof host.updatePageState !== 'function') return;
    if (baseFilters === null) {
      baseFilters = { ...(host.pageState()?.filters || {}) };
    }
    // Nested, never spread: the server's filters name the run's own scope
    // (opportunity_id OR program_id), which every agent tool must be called with,
    // and a drill into an opportunity must not read as a change of that scope.
    const patch: Record<string, unknown> = {
      filters: { ...baseFilters, drilled: selection.drilled || {} },
    };
    if (selection.visible_ids) patch.visible_ids = selection.visible_ids;
    host.updatePageState(patch);
  }
  // A selection shared before the panel finished loading is replayed once it has.
  doc.addEventListener('canopy:ready', () => {
    if (last) share(last);
  });
  return share;
}
