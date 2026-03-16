# Labs Development Guide

## Overview

Labs is a rapid prototyping environment for CommCare Connect experiments. It uses OAuth authentication (no local database users) and reads/writes data to production via APIs.

## Local Setup

Follow the standard CommCare Connect setup in the main [README.md](../../README.md), then:

1.  **Install labs requirements**:

    $ pip install -r requirements/labs.txt

2.  **Run with local settings** (NOT labs settings):

        $ ./manage.py runserver

    **Important**: Use `config.settings.local` (the default), NOT `config.settings.labs_aws`. The `labs_aws` settings are only for the AWS deployment at `labs.connect.dimagi.com`. The `local.py` settings already have `IS_LABS_ENVIRONMENT = True` and all labs middleware configured.

3.  **Get a CLI OAuth token** (for scripts and management commands):

        $ python manage.py get_cli_token

    This opens a browser for OAuth authentication and saves the token to `~/.commcare-connect/token.json`.

    **Multi-profile support**: You can store multiple tokens (e.g., your personal account and a test user):

        # Save under a named profile
        $ python manage.py get_cli_token --profile test-user

        # List all profiles
        $ python manage.py get_cli_token --list-profiles

        # Switch active profile
        $ python manage.py get_cli_token --switch-profile test-user

    In code, specify a profile explicitly:

    ```python
    tm = TokenManager(profile="test-user")
    token = tm.get_valid_token()
    ```

    When no profile is specified, the active profile is used automatically.

4.  **Access Labs features** at `http://localhost:8000/labs/login/`

## Key Architecture

- **OAuth Authentication**: Session-based, no local user database
- **Data Storage**: Production LabsRecord API (not local database)
- **API Client**: `LabsRecordAPIClient` for all data operations
- **Transient Objects**: `LabsUser` and `LocalLabsRecord` (never saved locally)

## Getting Started

### 1. OAuth Setup

**For CLI/Scripts:**

```bash
# Get OAuth token via browser
python manage.py get_cli_token

# Token saved to ~/.commcare-connect/token.json
# User profile is fetched at runtime via token introspection (not stored locally)
```

**In Python Scripts:**

```python
from commcare_connect.labs.integrations.connect.cli import TokenManager
from commcare_connect.labs.integrations.connect.oauth import introspect_token
from django.conf import settings

# Load saved token
token_manager = TokenManager()
access_token = token_manager.get_valid_token()

# Introspect token at runtime to get user profile
user_profile = introspect_token(
    access_token=access_token,
    client_id=settings.CLI_OAUTH_CLIENT_ID,
    client_secret=settings.CLI_OAUTH_CLIENT_SECRET,
    production_url=settings.CONNECT_PRODUCTION_URL
)

# user_profile contains: {id, username, email, first_name, last_name}
```

**Create LabsUser from CLI Token:**

```python
from commcare_connect.labs.integrations.connect.cli import get_labs_user_from_token

# Introspects token at runtime
user = get_labs_user_from_token()
if user:
    print(f"Logged in as: {user.username}")
```

### 2. Data Access Pattern

**Initialize API Client:**

```python
from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.labs.integrations.connect.cli import TokenManager

# Get token
token_manager = TokenManager()
access_token = token_manager.get_valid_token()

# Initialize client (opportunity-scoped)
client = LabsRecordAPIClient(
    access_token=access_token,
    opportunity_id=764  # Your opportunity ID
)
```

**Basic Operations:**

```python
# Create record
record = client.create_record(
    experiment="my_experiment",
    type="MyRecordType",
    data={"status": "active", "value": 100},
    username="user@example.com",  # From user_profile
    program_id=25
)

# Query records - all parameters are optional for filtering
# Get all records for the opportunity/program/org
all_records = client.get_records()

# Or filter by experiment, type, username
filtered_records = client.get_records(
    experiment="my_experiment",
    type="MyRecordType",
    username="user@example.com"
)

# Update record
updated = client.update_record(
    record_id=record.id,
    data={"status": "completed", "value": 150}
)
```

### 3. Create Proxy Models

**Define typed proxies for your data:**

```python
# your_app/models.py
from commcare_connect.labs.models import LocalLabsRecord

class MyRecord(LocalLabsRecord):
    """Proxy for MyRecordType records."""

    @property
    def status(self):
        return self.data.get("status")

    @property
    def value(self):
        return self.data.get("value", 0)

    def set_status(self, status):
        self.data["status"] = status
```

### 4. Data Access Layer Pattern

**Wrap API client with app-specific logic:**

```python
# your_app/data_access.py
from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from .models import MyRecord

class MyAppDataAccess:
    def __init__(self, opportunity_id: int, access_token: str):
        self.client = LabsRecordAPIClient(
            access_token=access_token,
            opportunity_id=opportunity_id
        )

    def get_my_records(self, username: str | None = None) -> list[MyRecord]:
        """Get MyRecord instances."""
        # Filters are optional - experiment/type can improve query performance
        return self.client.get_records(
            experiment="my_app",
            type="MyRecordType",
            username=username
        )

    def create_my_record(self, username: str, data: dict) -> MyRecord:
        """Create new MyRecord."""
        return self.client.create_record(
            experiment="my_app",
            type="MyRecordType",
            data=data,
            username=username
        )
```

## OAuth Functions Reference

### Connect OAuth Helpers (`integrations/connect/oauth.py`)

```python
from commcare_connect.labs.integrations.connect.oauth import (
    introspect_token,
    fetch_user_organization_data
)

# Introspect token to get user profile
user_profile = introspect_token(
    access_token="token",
    client_id="client_id",
    client_secret="secret",
    production_url="https://connect.dimagi.com"
)
# Returns: {id, username, email, first_name, last_name}

# Fetch user's organizations, programs, opportunities
org_data = fetch_user_organization_data(access_token="token")
# Returns: {organizations: [...], programs: [...], opportunities: [...]}
```

### CLI OAuth (`integrations/connect/cli/`)

```python
from commcare_connect.labs.integrations.connect.cli import (
    get_oauth_token,
    get_labs_user_from_token,
    TokenManager
)

# Get token via browser OAuth (CLI)
token_data = get_oauth_token(
    client_id="client_id",
    production_url="https://connect.dimagi.com",
    introspect=True,  # Include user profile
    client_secret="secret"  # Required for introspection
)

# Create LabsUser from saved token
user = get_labs_user_from_token()

# Manage tokens
manager = TokenManager()
access_token = manager.get_valid_token()
user_profile = manager.get_user_profile()
has_profile = manager.has_user_profile()
```

## Settings Configuration

**Required Settings:**

```python
# config/settings/labs_aws.py or local.py
CONNECT_PRODUCTION_URL = "https://connect.dimagi.com"
CONNECT_OAUTH_CLIENT_ID = "your_web_client_id"
CONNECT_OAUTH_CLIENT_SECRET = "your_web_client_secret"
CLI_OAUTH_CLIENT_ID = "your_cli_client_id"  # For get_cli_token command
CLI_OAUTH_CLIENT_SECRET = "your_cli_client_secret"  # For introspection
```

## Common Patterns

### Pattern 1: Django View with OAuth

```python
from django.views.generic import ListView
from your_app.data_access import MyAppDataAccess

class MyRecordListView(ListView):
    template_name = "my_app/records.html"

    def get_queryset(self):
        # Get OAuth token from session
        access_token = self.request.session["labs_oauth"]["access_token"]

        # Initialize data access
        data_access = MyAppDataAccess(
            opportunity_id=self.kwargs["opportunity_id"],
            access_token=access_token
        )

        # Fetch records (returns list, not QuerySet)
        return data_access.get_my_records(
            username=self.request.user.username
        )
```

### Pattern 2: CLI Script

```python
#!/usr/bin/env python
from django.conf import settings
from commcare_connect.labs.integrations.connect.cli import TokenManager
from commcare_connect.labs.integrations.connect.oauth import introspect_token
from your_app.data_access import MyAppDataAccess

def main():
    # Load token
    token_manager = TokenManager()
    access_token = token_manager.get_valid_token()

    if not access_token:
        print("Please run: python manage.py get_cli_token")
        return

    # Introspect token at runtime to get user profile
    user_profile = introspect_token(
        access_token=access_token,
        client_id=settings.CLI_OAUTH_CLIENT_ID,
        client_secret=settings.CLI_OAUTH_CLIENT_SECRET,
        production_url=settings.CONNECT_PRODUCTION_URL
    )

    if not user_profile:
        print("Failed to introspect token")
        return

    # Initialize data access
    data_access = MyAppDataAccess(
        opportunity_id=764,
        access_token=access_token
    )

    # Do work
    records = data_access.get_my_records(username=user_profile["username"])
    print(f"Found {len(records)} records")

if __name__ == "__main__":
    main()
```

## Important Notes

1. **No Local Storage**: User profiles are NOT stored locally - they're fetched at runtime via token introspection
2. **No Local Database Writes**: `LocalLabsRecord` and `LabsUser` cannot be saved locally
3. **Opportunity Scoping**: All API calls are scoped to an opportunity_id
4. **Username, not User ID**: Production API uses username as primary identifier
5. **Lists, not QuerySets**: API returns Python lists, not Django QuerySets
6. **Token Expiration**: Tokens expire, use `TokenManager.get_valid_token()` to check
7. **Introspection Requires Secret**: Token introspection requires `CLI_OAUTH_CLIENT_SECRET` to be configured

## Reference Examples

- **Audit App**: `commcare_connect/audit/` - Complete implementation
- **Integration Test**: `commcare_connect/audit/run_audit_integration.py` - CLI usage example
- **API Client**: `commcare_connect/labs/integrations/connect/api_client.py` - Full API reference

## Getting Help

- See individual app READMEs for app-specific patterns
- OAuth flow details in `integrations/connect/oauth_views.py` for web authentication
