# Labs URL Testing Commands

This directory contains management commands for testing Labs project URLs to ensure they load correctly.

## Overview

These commands test all URLs in labs projects by:

1. Logging in as a test user
2. Loading each base URL
3. Following all links found on each page
4. Testing pagination links
5. Reporting any failures

The tests run in the same environment as `runserver` using Django's test client with `force_login`.

## Commands

### Test All Labs Projects

Test all three labs projects (solicitations, tasks, audit) in one command:

```bash
python manage.py test_all_labs_urls --settings=config.settings.labs_aws
```

Options:

- `--user <email>`: Specify user email (default: `jjackson-dev@dimagi.com`)
- `--verbose`: Show detailed output for each URL tested
- `--project <name>`: Test only a specific project (solicitations/tasks/audit)

Examples:

```bash
# Test all projects with default user
python manage.py test_all_labs_urls --settings=config.settings.labs_aws

# Test only solicitations
python manage.py test_all_labs_urls --project solicitations --settings=config.settings.labs_aws

# Test with verbose output
python manage.py test_all_labs_urls --verbose --settings=config.settings.labs_aws

# Test with different user
python manage.py test_all_labs_urls --user another-user@example.com --settings=config.settings.labs_aws
```

### Test Individual Projects

Test a specific project:

```bash
# Test solicitations
python manage.py test_solicitations_urls --settings=config.settings.labs_aws

# Test tasks
python manage.py test_tasks_urls --settings=config.settings.labs_aws

# Test audit
python manage.py test_audit_urls --settings=config.settings.labs_aws
```

Options (same for all individual commands):

- `--user <email>`: Specify user email
- `--url <path>`: Test only a specific URL
- `--verbose`: Show detailed output

Examples:

```bash
# Test specific URL in solicitations
python manage.py test_solicitations_urls --url /solicitations/dashboard/ --settings=config.settings.labs_aws

# Test with verbose output
python manage.py test_solicitations_urls --verbose --settings=config.settings.labs_aws
```

## Adding Tests for New Labs Projects

To add URL testing for a new labs project:

1. Create a new command file in the project's management/commands directory:

   ```python
   # connect_labs/myproject/management/commands/test_myproject_urls.py
   from connect_labs.labs.management.commands.base_labs_url_test import BaseLabsURLTest

   class Command(BaseLabsURLTest):
       help = "Test all myproject URLs"

       project_name = "myproject"
       base_urls = [
           "/myproject/",
           "/myproject/dashboard/",
       ]
       exclude_patterns = ["sort=", "oauth", "logout"]
   ```

2. Add your project to the `test_all_labs_urls` command in this file.

## How It Works

The base class `BaseLabsURLTest` provides:

- **User Authentication**: Logs in using Django's `force_login()`
- **Link Extraction**: Parses HTML to find project-specific links
- **Pagination Testing**: Automatically tests pagination links
- **Error Reporting**: Tracks and reports all failed URLs
- **Exclusion Patterns**: Skips URLs matching certain patterns (e.g., sort links, OAuth)

The test client runs in the same environment as your development server, so:

- It uses the same database
- It respects middleware and authentication
- It follows the same URL routing
- It's equivalent to clicking links in a browser

## Output Format

### Verbose Mode

```
=== Testing Solicitations URLs for user@example.com ===

--- Testing /solicitations/ ---
  ✓ Base URL: /solicitations/
  Found 10 links to test
  ✓ View: /solicitations/detail/1/
  ✓ Edit: /solicitations/edit/1/
  ...

============================================================
Total: 25 URLs tested
Passed: 25
All tests passed for solicitations!
```

### Quiet Mode (Default)

```
=== Testing Solicitations URLs for user@example.com ===

--- Testing /solicitations/ ---

============================================================
Total: 25 URLs tested
Passed: 25
All tests passed for solicitations!
```

### With Failures

```
============================================================
Total: 25 URLs tested
Passed: 23
Failed: 2

Failed URLs:
  - Detail: /solicitations/detail/999/ [HTTP 404]
  - Action: /solicitations/broken/ ['LocalLabsRecord' object has no attribute 'date_created']
```

## Troubleshooting

### User Not Found

```
User user@example.com not found.
```

**Solution**: Make sure the user exists in your database or specify a different user with `--user`.

### Permission Denied

Some URLs may fail if the test user doesn't have the required permissions. This is expected behavior.

### Attribute Errors

If you see errors like `'LocalLabsRecord' object has no attribute 'date_created'`, this indicates a missing field in the model. Check that all required fields are present in `LocalLabsRecord.__init__()`.

## Integration with CI/CD

These commands can be integrated into CI/CD pipelines:

```bash
# In your CI script
python manage.py test_all_labs_urls --settings=config.settings.labs_aws --user ci-test-user@example.com

# Check exit code
if [ $? -ne 0 ]; then
    echo "URL tests failed"
    exit 1
fi
```
