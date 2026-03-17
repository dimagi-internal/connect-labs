"""Tests for TokenManager multi-profile support."""

import json
from datetime import datetime, timedelta

from commcare_connect.labs.integrations.connect.cli.token_manager import TokenManager


def _make_token(username="testuser", expired=False):
    """Create a token data dict for testing."""
    if expired:
        expires_at = (datetime.now() - timedelta(hours=1)).isoformat()
    else:
        expires_at = (datetime.now() + timedelta(hours=10)).isoformat()
    return {
        "access_token": f"tok_{username}",
        "token_type": "Bearer",
        "expires_at": expires_at,
        "saved_at": datetime.now().isoformat(),
        "user_profile": {"username": username, "email": f"{username}@example.com"},
    }


class TestV1Migration:
    def test_auto_migrates_v1_on_load(self, tmp_path):
        """Old flat token.json is migrated to v2 on first load."""
        token_file = tmp_path / "token.json"
        v1_data = _make_token("alice")
        token_file.write_text(json.dumps(v1_data))

        tm = TokenManager(token_file=str(token_file))
        loaded = tm.load_token()

        assert loaded is not None
        assert loaded["access_token"] == "tok_alice"

        # File is now v2 format
        raw = json.loads(token_file.read_text())
        assert raw["_version"] == 2
        assert raw["_active_profile"] == "alice"
        assert "alice" in raw["profiles"]

    def test_v1_without_user_profile_uses_default(self, tmp_path):
        """V1 token without user_profile gets profile name 'default'."""
        token_file = tmp_path / "token.json"
        v1_data = {
            "access_token": "tok_anon",
            "expires_at": (datetime.now() + timedelta(hours=10)).isoformat(),
        }
        token_file.write_text(json.dumps(v1_data))

        tm = TokenManager(token_file=str(token_file))
        loaded = tm.load_token()

        assert loaded["access_token"] == "tok_anon"
        assert tm.get_active_profile_name() == "default"


class TestSaveAndLoad:
    def test_save_creates_profile(self, tmp_path):
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file))

        token_data = _make_token("bob")
        assert tm.save_token(token_data)

        loaded = tm.load_token()
        assert loaded["access_token"] == "tok_bob"
        assert tm.get_active_profile_name() == "bob"

    def test_save_with_explicit_profile(self, tmp_path):
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file), profile="custom")

        token_data = _make_token("bob")
        assert tm.save_token(token_data)

        assert tm.get_active_profile_name() == "custom"
        loaded = tm.load_token()
        assert loaded["access_token"] == "tok_bob"

    def test_multiple_profiles(self, tmp_path):
        token_file = tmp_path / "token.json"

        # Save first profile
        tm1 = TokenManager(token_file=str(token_file), profile="alice")
        tm1.save_token(_make_token("alice"))

        # Save second profile
        tm2 = TokenManager(token_file=str(token_file), profile="bob")
        tm2.save_token(_make_token("bob"))

        # Active should be the last saved
        tm_default = TokenManager(token_file=str(token_file))
        assert tm_default.get_active_profile_name() == "bob"
        assert tm_default.load_token()["access_token"] == "tok_bob"

        # Can still read alice explicitly
        tm_alice = TokenManager(token_file=str(token_file), profile="alice")
        assert tm_alice.load_token()["access_token"] == "tok_alice"


class TestSwitchProfile:
    def test_switch_active_profile(self, tmp_path):
        token_file = tmp_path / "token.json"

        TokenManager(token_file=str(token_file), profile="alice").save_token(_make_token("alice"))
        TokenManager(token_file=str(token_file), profile="bob").save_token(_make_token("bob"))

        tm = TokenManager(token_file=str(token_file))
        assert tm.get_active_profile_name() == "bob"

        assert tm.set_active_profile("alice")
        assert tm.get_active_profile_name() == "alice"
        assert tm.load_token()["access_token"] == "tok_alice"

    def test_switch_to_nonexistent_fails(self, tmp_path):
        token_file = tmp_path / "token.json"

        TokenManager(token_file=str(token_file), profile="alice").save_token(_make_token("alice"))

        tm = TokenManager(token_file=str(token_file))
        assert not tm.set_active_profile("nobody")


class TestListProfiles:
    def test_list_profiles(self, tmp_path):
        token_file = tmp_path / "token.json"

        TokenManager(token_file=str(token_file), profile="alice").save_token(_make_token("alice"))
        TokenManager(token_file=str(token_file), profile="bob").save_token(_make_token("bob"))

        tm = TokenManager(token_file=str(token_file))
        profiles = tm.list_profiles()

        assert len(profiles) == 2
        names = {p["name"] for p in profiles}
        assert names == {"alice", "bob"}

        active = [p for p in profiles if p["active"]]
        assert len(active) == 1
        assert active[0]["name"] == "bob"

    def test_list_empty(self, tmp_path):
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file))
        assert tm.list_profiles() == []


class TestClearToken:
    def test_clear_removes_profile(self, tmp_path):
        token_file = tmp_path / "token.json"

        TokenManager(token_file=str(token_file), profile="alice").save_token(_make_token("alice"))
        TokenManager(token_file=str(token_file), profile="bob").save_token(_make_token("bob"))

        tm = TokenManager(token_file=str(token_file), profile="alice")
        assert tm.clear_token()

        profiles = TokenManager(token_file=str(token_file)).list_profiles()
        assert len(profiles) == 1
        assert profiles[0]["name"] == "bob"

    def test_clear_active_switches_to_remaining(self, tmp_path):
        token_file = tmp_path / "token.json"

        TokenManager(token_file=str(token_file), profile="alice").save_token(_make_token("alice"))
        TokenManager(token_file=str(token_file), profile="bob").save_token(_make_token("bob"))

        # bob is active (last saved), clear it
        tm = TokenManager(token_file=str(token_file))
        assert tm.get_active_profile_name() == "bob"
        tm.clear_token()

        tm2 = TokenManager(token_file=str(token_file))
        assert tm2.get_active_profile_name() == "alice"

    def test_clear_last_profile_sets_none(self, tmp_path):
        token_file = tmp_path / "token.json"

        TokenManager(token_file=str(token_file), profile="only").save_token(_make_token("only"))

        tm = TokenManager(token_file=str(token_file))
        tm.clear_token()

        assert tm.get_active_profile_name() is None
        assert tm.load_token() is None


class TestBackwardCompatibility:
    def test_token_file_param_still_works(self, tmp_path):
        """TokenManager(token_file=...) without profile uses active profile."""
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file))
        tm.save_token(_make_token("user1"))

        loaded = tm.load_token()
        assert loaded["access_token"] == "tok_user1"

    def test_no_args_constructor(self, tmp_path, monkeypatch):
        """TokenManager() with no args still works (uses default file path)."""
        # Just verify it doesn't crash — don't modify the real token file
        tm = TokenManager(token_file=str(tmp_path / "token.json"))
        assert tm.load_token() is None  # No file yet

    def test_get_valid_token(self, tmp_path):
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file))
        tm.save_token(_make_token("user1"))

        assert tm.get_valid_token() == "tok_user1"

    def test_get_valid_token_expired(self, tmp_path):
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file))
        tm.save_token(_make_token("user1", expired=True))

        assert tm.get_valid_token() is None

    def test_is_expired(self, tmp_path):
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file))
        tm.save_token(_make_token("user1"))

        assert not tm.is_expired()

    def test_get_token_info(self, tmp_path):
        token_file = tmp_path / "token.json"
        tm = TokenManager(token_file=str(token_file))
        tm.save_token(_make_token("user1"))

        info = tm.get_token_info()
        assert info is not None
        assert info["is_valid"]
        assert "expires_in_seconds" in info


class TestExplicitProfileOverride:
    def test_explicit_profile_ignores_active(self, tmp_path):
        """When profile= is set, it overrides the active profile."""
        token_file = tmp_path / "token.json"

        TokenManager(token_file=str(token_file), profile="alice").save_token(_make_token("alice"))
        TokenManager(token_file=str(token_file), profile="bob").save_token(_make_token("bob"))

        # Active is bob, but we explicitly request alice
        tm = TokenManager(token_file=str(token_file), profile="alice")
        assert tm.load_token()["access_token"] == "tok_alice"
        assert tm.get_valid_token() == "tok_alice"
