# Coverage Management Commands

## Setup OAuth Tokens

The coverage commands require two OAuth tokens:

### 1. Connect OAuth Token (for opportunity/visits data)

```bash
python manage.py get_cli_token
```

This saves to `~/.commcare-connect/token.json`

### 2. CommCare OAuth Token (for delivery units from CommCare HQ)

```bash
python manage.py get_commcare_token
```

This saves to `~/.commcare-connect/commcare_token.json`

**Note:** You'll need CommCare OAuth credentials configured:

- `COMMCARE_OAUTH_CLIENT_ID` in settings
- `COMMCARE_HQ_URL` (defaults to https://www.commcarehq.org)

## Test Coverage Data Loading

Test the full coverage data loading pipeline:

```bash
python manage.py test_coverage_load --opportunity-id 575
```

Options:

- `--opportunity-id`: Opportunity ID to test (default: 575)
- `--verbose`: Enable detailed debug output
- `--skip-visits`: Skip fetching user visits (test DUs only)
- `--commcare-token`: Provide CommCare token directly (alternative to token file)

### Example Output

```
Testing Coverage Data Load for Opportunity 575
======================================================================

[1/5] Loading OAuth Tokens...
  Connect token loaded (expires: 2025-11-24T07:18:30)
  CommCare token loaded (expires: 2025-11-24T08:00:00)

[2/5] Creating mock request...
  Mock request created

[3/5] Fetching opportunity metadata...
  Opportunity: CHC JHF 2024-25
  CommCare Domain: ccc-chc-jhf-2024-25

[4/5] Fetching delivery units from CommCare...
  Fetched 150 delivery units

[5/5] Fetching user visits from Connect...
  Fetched 1234 user visits
  Columns: ['id', 'user_id', 'username', 'form_json', ...]

  Checking form_json column...
    All form_json values are valid JSON

======================================================================
Test completed successfully!
```

## Export Token from Web Session

If you've already authorized CommCare via the web UI, you can export the token:

```bash
python manage.py export_commcare_token --session-key <your_session_key>
```

Or find by username:

```bash
python manage.py export_commcare_token --username "user@example.com"
```

## Troubleshooting

### "Connect OAuth token not found"

Run: `python manage.py get_cli_token`

### "CommCare OAuth token not found"

Run: `python manage.py get_commcare_token`

### "Token expired"

Re-run the get token command to refresh it

### "No OAuth client ID"

Set `COMMCARE_OAUTH_CLIENT_ID` in your Django settings
