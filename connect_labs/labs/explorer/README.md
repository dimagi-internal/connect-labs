# Labs Data Explorer

A table-based UI for exploring, filtering, editing, and managing LabsRecord data in CommCare Connect Labs.

## Features

- **Browse Records**: View all LabsRecord data in a paginated table with context filtering
- **Advanced Filtering**: Filter by experiment, type, username, and date ranges
- **Edit Records**: Dedicated edit page with JSON validation and formatting
- **Download Records**: Export selected or filtered records as JSON
- **Upload/Import**: Bulk import records from JSON files
- **Delete Records**: Bulk delete selected records with confirmation
- **Labs Context**: Automatically scopes data by selected opportunity/program

## Structure

```
explorer/
├── __init__.py
├── data_access.py      # API client wrapper with context filtering
├── forms.py            # Filter, edit, and upload forms (crispy forms)
├── tables.py           # Django Tables2 table definition
├── urls.py             # URL routing
├── utils.py            # JSON validation, export/import helpers
├── views.py            # List, edit, download, upload views
└── README.md

templates/labs/explorer/
├── list.html           # Main table view with filters
└── edit.html           # Dedicated edit page with JSON editor
```

## Usage

### Access

Navigate to `/labs/explorer/` after logging into Labs and selecting a context (opportunity/program).

### Filtering

Use the sidebar filters to narrow down records:

- **Experiment**: Filter by experiment name (audit, tasks, solicitations, etc.)
- **Type**: Filter by record type (AuditSession, Task, Solicitation, etc.)
- **Username**: Search by username
- **Created Date Range**: Filter by creation date

### Editing

1. Click the "Edit" button on any record row
2. Modify the JSON data in the editor
3. Use "Format JSON" to auto-format or "Validate JSON" to check syntax
4. Click "Save Changes" to update the record
5. Cancel returns to the list view

### Downloading

- **Download Selected**: Check records and click to download only those
- **Download All Filtered**: Downloads all records matching current filters
- Files are saved as `labs_records_{experiment}_{timestamp}.json`

### Uploading

1. Click "Upload/Import" button
2. Select a JSON file (must be array of record objects)
3. File is validated before import
4. Records are created via API with bulk_create_records

### Deleting

- **Delete Selected**: Check records and click to delete them
- Confirmation prompt before deletion
- Records are permanently deleted via API
- This action cannot be undone

## Implementation Notes

### Data Access

- Uses `RecordExplorerDataAccess` class that wraps `LabsRecordAPIClient`
- Automatically applies labs context (opportunity_id/program_id) from session
- **Optimized**: Makes a single API call per page load
  - Fetches all records once and caches them in the view
  - Filters are applied client-side (Python) from the cached data
  - Distinct values for filter dropdowns extracted from cached data
  - Previous implementation made 3 API calls (queryset + 2 for distinct values)
- Trade-off: Client-side filtering vs. fewer API calls (worthwhile for typical dataset sizes)

### Forms

- `RecordFilterForm`: Crispy forms with dynamic choices from API
- `RecordEditForm`: JSON textarea with validation
- `RecordUploadForm`: File upload with JSON validation

### Views

- `RecordListView`: SingleTableView with filtering and pagination
- `RecordEditView`: TemplateView with form handling
- `RecordDownloadView`: View that returns JSON file response
- `RecordUploadView`: View that handles bulk imports
- `DeleteRecordsView`: View that handles bulk deletions

### Templates

- Follow existing Labs patterns (base.html, context checking)
- Use Alpine.js for interactive elements (upload modal, selection)
- Include breadcrumb navigation and metadata display

## Future Enhancements

- Batch editing of multiple records
- Advanced search with JSON field queries
- Export to CSV format
- Record history/audit trail
