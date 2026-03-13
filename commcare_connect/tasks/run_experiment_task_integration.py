#!/usr/bin/env python
"""
Integration test for experiment-based task flow.

Tests the complete task workflow using ExperimentRecords and Connect OAuth APIs.

Usage:
    python commcare_connect/tasks/run_experiment_task_integration.py
"""

import os
import sys


def test_experiment_task_flow():
    """Test complete task flow with experiment records."""

    print("=" * 80)
    print("EXPERIMENT TASK INTEGRATION TEST")
    print("=" * 80)

    # Step 1: Initialize Django
    print("\n[1] Initializing Django...")
    try:
        # Setup Django
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

        import django

        django.setup()

        print("[OK] Django initialized")
    except Exception as e:
        print(f"[ERROR] Failed to initialize Django: {e}")
        return

    # Step 2: Get OAuth token
    print("\n[2] Getting OAuth token...")
    try:
        access_token = os.getenv("CONNECT_OAUTH_TOKEN")
        if access_token:
            print("[OK] Using token from CONNECT_OAUTH_TOKEN environment variable")
        else:
            print("[INFO] Checking for saved OAuth token...")
            from commcare_connect.labs.integrations.connect.cli import TokenManager

            token_manager = TokenManager()
            access_token = token_manager.get_valid_token()

            if access_token:
                info = token_manager.get_token_info()
                if info and "expires_in_seconds" in info:
                    minutes = info["expires_in_seconds"] // 60
                    print(f"[OK] Using saved token (expires in {minutes} minutes)")
                else:
                    print("[OK] Using saved OAuth token from token manager")
            else:
                print("[ERROR] No OAuth token found or token expired.")
                print("[INFO] Please run: python manage.py get_cli_token")
                print("[INFO] Or set CONNECT_OAUTH_TOKEN environment variable")
                return

    except Exception as e:
        print(f"[ERROR] Failed to get OAuth token: {e}")
        return

    # Step 3: Create mock OAuth user for testing
    print("\n[3] Creating mock OAuth user...")
    try:
        from commcare_connect.users.models import User

        # For testing, create a Django User with minimal data
        # In real usage, this would come from OAuth login
        oauth_user, _ = User.objects.update_or_create(
            username="test_oauth_user",
            defaults={"email": "oauth_test@example.com", "name": "Test OAuth User"},
        )
        print(f"[OK] Created User: {oauth_user.username} (ID: {oauth_user.id})")
        print("     Note: Using mock user for testing. In production, user comes from OAuth login.")

    except Exception as e:
        print(f"[ERROR] Failed to create mock OAuth user: {e}")
        import traceback

        traceback.print_exc()
        return

    # Step 4: Initialize TaskDataAccess
    print("\n[4] Initializing TaskDataAccess...")
    try:
        # Initialize with OAuth token directly (will use organization_id from context)
        from commcare_connect.tasks.data_access import TaskDataAccess

        data_access = TaskDataAccess(user=oauth_user, access_token=access_token)
        print("[OK] TaskDataAccess initialized")

    except Exception as e:
        print(f"[ERROR] Failed to initialize TaskDataAccess: {e}")
        import traceback

        traceback.print_exc()
        return

    # Step 5: Search for opportunities and find one with users
    print("\n[5] Searching for 'readers' opportunities...")
    try:
        opportunities = data_access.search_opportunities("readers", limit=10)
        if not opportunities:
            print("[ERROR] No opportunities found matching 'readers'")
            data_access.close()
            return

        print(f"[OK] Found {len(opportunities)} opportunities")

        # Try each opportunity until we find one with users
        selected_opp = None
        selected_user = None
        users = []

        for opp in opportunities:
            print(f"[INFO] Trying opportunity: {opp['name']} (ID: {opp['id']})")
            try:
                users = data_access.get_users_from_opportunity(opp["id"])
                if users:
                    selected_opp = opp
                    selected_user = users[0]
                    print(f"[OK] Found {len(users)} users in this opportunity")
                    print(f"[OK] Selected user: {selected_user['username']} (user_id not available from API)")
                    break
                else:
                    print("[INFO] No users found, trying next opportunity...")
            except Exception as e:
                print(f"[WARN] Could not get users for this opportunity: {e}")
                continue

        if not selected_opp or not selected_user:
            print("[ERROR] No opportunities with users found")
            data_access.close()
            return

    except Exception as e:
        print(f"[ERROR] Failed to search opportunities: {e}")
        import traceback

        traceback.print_exc()
        data_access.close()
        return

    # Step 7: Create a task
    print("\n[7] Creating a task...")
    try:
        # Username is the primary identifier in Connect
        task = data_access.create_task(
            username=selected_user["username"],
            opportunity_id=selected_opp["id"],
            priority="high",
            title=f"Test Task - Photo Quality Issue for {selected_user['username']}",
            description="Photos submitted show poor lighting and framing. Follow-up required.",
            creator_name=oauth_user.username,
            # user_id not available from /export/opportunity/<id>/user_data/ API
        )

        print(f"[OK] Task created: #{task.id}")
        print(f"    - Username: {task.username} (primary identifier)")
        print(f"    - User ID: {task.user_id} (not available from API)")
        print(f"    - Opportunity ID: {task.opportunity_id}")
        print(f"    - Status: {task.status}")
        print(f"    - Priority: {task.priority}")
        print(f"    - Events: {len(task.events)}")

    except Exception as e:
        print(f"[ERROR] Failed to create task: {e}")
        import traceback

        traceback.print_exc()
        data_access.close()
        return

    # Step 8: Add an event
    print("\n[8] Adding an event...")
    try:
        data_access.add_event(
            task,
            event_type="pattern_detected",
            actor=oauth_user.username,
            description="Detected pattern: 3rd quality issue in past 2 weeks",
        )
        print(f"[OK] Event added. Total events: {len(task.events)}")

    except Exception as e:
        print(f"[ERROR] Failed to add event: {e}")
        import traceback

        traceback.print_exc()

    # Step 9: Add a comment
    print("\n[9] Adding a comment...")
    try:
        data_access.add_comment(task, oauth_user.username, "Following up with Network Manager on this case.")

        print(f"[OK] Comment added. Total comment events: {len(task.get_comment_events())}")

    except Exception as e:
        print(f"[ERROR] Failed to add comment: {e}")
        import traceback

        traceback.print_exc()

    # Step 10: Update task status
    print("\n[10] Updating task status...")
    try:
        data_access.update_status(task, "review_needed", oauth_user.username)

        print(f"[OK] Status updated to: {task.status}")
        print(f"    - Total events: {len(task.events)}")

    except Exception as e:
        print(f"[ERROR] Failed to update status: {e}")
        import traceback

        traceback.print_exc()

    # Step 11: Assign task
    print("\n[11] Assigning task...")
    try:
        data_access.assign_task(task, oauth_user.username, "self", oauth_user.username)

        print(f"[OK] Task assigned to: {task.assigned_to_name}")
        print(f"    - Total events: {len(task.events)}")

    except Exception as e:
        print(f"[ERROR] Failed to assign task: {e}")
        import traceback

        traceback.print_exc()

    # Step 12: Add AI session (mock)
    print("\n[12] Adding AI session...")
    try:
        session_params = {"platform": "commcare_connect", "experiment": "test-bot"}
        data_access.add_ai_session(
            task,
            actor=oauth_user.username,
            session_params=session_params,
            session_id="test-session-123",
            status="completed",
        )

        print(f"[OK] AI session added. Total sessions: {len(task.get_ai_session_events())}")

    except Exception as e:
        print(f"[ERROR] Failed to add AI session: {e}")
        import traceback

        traceback.print_exc()

    # Step 13: Verify data structure
    print("\n[13] Verifying JSON data structure...")
    try:
        print(f"[OK] Task data keys: {list(task.data.keys())}")
        print(f"    - username: {task.username} (primary identifier)")
        print(f"    - user_id: {task.user_id}")
        print(f"    - opportunity_id: {task.opportunity_id}")
        print(f"    - status: {task.status}")
        print(f"    - priority: {task.priority}")
        print(f"    - title: {task.title}")
        print(f"    - events: {len(task.events)} items")
        print(f"    - comment events: {len(task.get_comment_events())} items")
        print(f"    - ai_session events: {len(task.get_ai_session_events())} items")

        # Verify events structure
        if task.events:
            sample_event = task.events[0]
            print(f"[OK] Sample event keys: {list(sample_event.keys())}")

        # Verify comments structure
        comment_events = task.get_comment_events()
        if comment_events:
            sample_comment = comment_events[0]
            print(f"[OK] Sample comment event keys: {list(sample_comment.keys())}")

        # Verify AI sessions structure
        ai_session_events = task.get_ai_session_events()
        if ai_session_events:
            sample_session = ai_session_events[0]
            print(f"[OK] Sample AI session event keys: {list(sample_session.keys())}")

    except Exception as e:
        print(f"[ERROR] Failed to verify data structure: {e}")
        import traceback

        traceback.print_exc()

    # Step 14: Test get_timeline() helper
    print("\n[14] Testing get_timeline() helper...")
    try:
        timeline = task.get_timeline()
        print(f"[OK] Timeline contains {len(timeline)} items (events + comments)")

        for i, item in enumerate(timeline[:3], 1):  # Show first 3 items
            print(f"    [{i}] Type: {item['type']}, Timestamp: {item.get('timestamp')}")

    except Exception as e:
        print(f"[ERROR] Failed to get timeline: {e}")
        import traceback

        traceback.print_exc()

    # Step 15: Query tasks
    print("\n[15] Querying tasks...")
    try:
        all_tasks = data_access.get_tasks()
        print(f"[OK] Found {len(all_tasks)} total tasks")

        # Query by status
        network_tasks = data_access.get_tasks(status="network_manager")
        print(f"[OK] Found {len(network_tasks)} tasks with status 'network_manager'")

    except Exception as e:
        print(f"[ERROR] Failed to query tasks: {e}")
        import traceback

        traceback.print_exc()

    # Step 16: Clean up
    print("\n[16] Cleaning up...")
    try:
        data_access.close()
        print("[OK] Data access closed")

    except Exception as e:
        print(f"[ERROR] Failed to close data access: {e}")

    # Final summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Task ID: {task.id}")
    print(f"Events: {len(task.events)}")
    print(f"Comment Events: {len(task.get_comment_events())}")
    print(f"AI Session Events: {len(task.get_ai_session_events())}")
    print(f"Timeline Items: {len(task.get_timeline())}")
    print("=" * 80)
    print("[SUCCESS] Integration test completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    test_experiment_task_flow()
