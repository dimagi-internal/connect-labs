// Phase 1's program home: list of past mop-up runs, plus multi-select
// delete. Deletion is a real, unrecoverable hard delete server-side (see
// MopupRunDataAccess.delete_run's docstring) -- everything about a run
// (thresholds, locked candidates, uploaded building data, planning-gap
// features) lives in that one record, so removing it is a complete cleanup.
window.MopupProgramHome = (function () {
  function $(id) {
    return document.getElementById(id);
  }

  let CFG = {};

  function checkboxes() {
    return Array.from(document.querySelectorAll('.run-checkbox'));
  }

  function selectedIds() {
    return checkboxes()
      .filter((cb) => cb.checked)
      .map((cb) => cb.value);
  }

  function updateDeleteButtonState() {
    $('delete-selected-runs').disabled = selectedIds().length === 0;
  }

  async function deleteSelected() {
    const ids = selectedIds();
    if (!ids.length) return;
    const confirmed = confirm(
      `Delete ${ids.length} WA Revisit run(s)? This permanently erases all data for ` +
        `${
          ids.length === 1 ? 'it' : 'them'
        } (thresholds, locked candidates, uploaded ` +
        `building data, planning-gap results) and cannot be undone.`,
    );
    if (!confirmed) return;

    const button = $('delete-selected-runs');
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = 'Deleting…';
    try {
      const resp = await fetch(CFG.deleteRunsUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({ run_ids: ids }),
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        alert(data.detail || 'Failed to delete run(s).');
        button.disabled = false;
        button.textContent = originalText;
        return;
      }
      data.deleted.forEach((id) => {
        const row = document.querySelector(`tr[data-run-id="${id}"]`);
        if (row) row.remove();
      });
      if (data.not_found.length) {
        alert(
          `${data.not_found.length} run(s) could not be deleted (already removed, or not in this program).`,
        );
      }
    } catch (e) {
      alert('Failed to delete run(s).');
    } finally {
      button.textContent = originalText;
      updateDeleteButtonState();
    }
  }

  function init(cfg) {
    CFG = cfg;
    checkboxes().forEach((cb) =>
      cb.addEventListener('change', () => {
        updateDeleteButtonState();
        const all = checkboxes();
        $('select-all-runs').checked =
          all.length > 0 && all.every((c) => c.checked);
      }),
    );
    const selectAll = $('select-all-runs');
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        checkboxes().forEach((cb) => (cb.checked = selectAll.checked));
        updateDeleteButtonState();
      });
    }
    $('delete-selected-runs').addEventListener('click', deleteSelected);
  }

  return { init };
})();
