from datetime import datetime, timezone
import stat

import pytest

from x_unfollow.config import (
    default_app_dir,
    load_config,
    write_config,
    write_default_config,
)
from x_unfollow.models import (
    ApiConfig,
    AppConfig,
    DecisionRecord,
    RuleConfig,
    ScanCursor,
    XUser,
)
from x_unfollow.storage import Storage
from x_unfollow.oauth import OAuthToken
from x_unfollow.tokens import OAuthCredentials, TokenStore


def test_write_and_load_default_config(tmp_path):
    config_path = tmp_path / "config.toml"
    write_default_config(config_path)
    config = load_config(config_path)
    assert config.rules.activity_threshold_days == 180
    assert config.api.max_accounts_per_scan == 10
    assert config.api.max_scan_cost_usd == 0.50


def test_write_config_round_trips_custom_values(tmp_path):
    path = tmp_path / "config.toml"
    expected = AppConfig(
        rules=RuleConfig(activity_threshold_days=90),
        api=ApiConfig(
            page_size_following=100,
            max_accounts_per_scan=3,
        ),
    )

    write_config(path, expected)

    assert load_config(path) == expected


def test_default_app_dir_is_stable_and_supports_explicit_override(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("X_UNFOLLOW_HOME", str(tmp_path / "state"))

    assert default_app_dir() == tmp_path / "state"


def test_environment_overrides_config_file_values(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[rules]",
                "activity_threshold_days = 180",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("X_UNFOLLOW_ACTIVITY_THRESHOLD_DAYS", "90")

    config = load_config(config_path)

    assert config.rules.activity_threshold_days == 90


def test_boolean_environment_overrides_parse_true_false(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[safety]",
                "require_review_before_unfollow = true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("X_UNFOLLOW_REQUIRE_REVIEW_BEFORE_UNFOLLOW", "false")

    config = load_config(config_path)

    assert config.safety.require_review_before_unfollow is False


def test_storage_round_trip_decisions(tmp_path):
    storage = Storage(tmp_path)
    record = DecisionRecord(
        user=XUser(id="1", username="quiet", name="Quiet"),
        decision="candidate",
        reason="test",
        last_activity_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        days_since_activity=523,
        last_activity_id="123",
        account_status="ok",
    )
    storage.save_decisions([record])
    loaded = storage.load_decisions()
    assert loaded[0].user.username == "quiet"
    assert loaded[0].last_activity_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert loaded[0].account_status == "ok"


def test_token_file_is_created_with_owner_only_permissions(tmp_path):
    store = TokenStore(tmp_path)

    store.save_bearer_token("secret")

    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600
    assert store.load_bearer_token() == "secret"


def test_oauth_credentials_round_trip_in_secure_token_file(tmp_path):
    store = TokenStore(tmp_path)
    credentials = OAuthCredentials(
        client_id="client-id",
        token=OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
            scope=("tweet.read", "offline.access"),
        ),
    )

    store.save_oauth_credentials(credentials)

    assert store.load_oauth_credentials() == credentials
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_storage_load_decisions_on_empty_app_dir_does_not_create_directories(tmp_path):
    storage = Storage(tmp_path)

    assert storage.load_decisions() == []
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "exports").exists()


def test_storage_write_methods_create_only_needed_directories(tmp_path):
    storage = Storage(tmp_path)
    record = DecisionRecord(
        user=XUser(id="1", username="quiet", name="Quiet"),
        decision="candidate",
        reason="test",
        last_activity_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        days_since_activity=523,
        last_activity_id="123",
    )

    storage.save_decisions([record])

    assert (tmp_path / "data").is_dir()
    assert not (tmp_path / "exports").exists()

    storage.export_candidates_csv([record])

    assert (tmp_path / "exports").is_dir()


def test_storage_appends_jsonl_unfollow_audit(tmp_path):
    storage = Storage(tmp_path)

    storage.append_unfollow_audit(
        [
            {
                "timestamp": datetime(2026, 7, 29, tzinfo=timezone.utc),
                "user_id": "1",
                "username": "quiet",
                "success": True,
                "error": None,
            }
        ]
    )

    content = storage.unfollow_audit_path.read_text(encoding="utf-8")
    assert '"username": "quiet"' in content
    assert '"success": true' in content


def test_storage_round_trips_scan_account_context(tmp_path):
    storage = Storage(tmp_path)

    storage.save_scan_context("123", "buhusa")

    assert storage.load_scan_context() == {
        "source_user_id": "123",
        "source_username": "buhusa",
    }

    storage.save_connection_context("456", "connected")
    assert storage.load_connection_context() == {
        "source_user_id": "456",
        "source_username": "connected",
    }

    refreshed_at = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    storage.save_connection_context(
        "456",
        "connected",
        following_count=606,
        following_refreshed_at=refreshed_at,
    )
    assert storage.load_connection_context() == {
        "source_user_id": "456",
        "source_username": "connected",
        "following_count": 606,
        "following_refreshed_at": refreshed_at.isoformat(),
    }


def test_storage_round_trips_scan_cursor_and_clears_current_cycle(tmp_path):
    storage = Storage(tmp_path)
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    cursor = ScanCursor(
        source_user_id="123",
        source_username="owner",
        cycle_id="cycle-1",
        started_at=now,
        updated_at=now,
        scanned_count=100,
        batch_number=1,
        next_token="page-2",
        config_signature="rules-v1",
    )

    storage.save_scan_cursor(cursor)
    storage.save_decisions([])

    assert storage.load_scan_cursor() == cursor
    assert stat.S_IMODE(storage.scan_cursor_path.stat().st_mode) == 0o600

    storage.clear_scan_cycle()

    assert storage.load_scan_cursor() is None
    assert storage.load_decisions() == []


def test_storage_loads_legacy_decisions_with_ok_account_status(tmp_path):
    storage = Storage(tmp_path)
    storage.data_dir.mkdir(parents=True, exist_ok=True)
    storage.decisions_path.write_text(
        """
[
  {
    "user": {"id": "1", "username": "quiet", "name": "Quiet"},
    "last_own_post_at": null,
    "days_since_own_post": null,
    "last_reply_at": null,
    "days_since_reply": null,
    "rule_match_own_post": true,
    "rule_match_reply": true,
    "decision": "candidate",
    "reason": "legacy"
  }
]
""",
        encoding="utf-8",
    )

    loaded = storage.load_decisions()

    assert loaded[0].account_status == "ok"


def test_storage_exports_candidates_csv(tmp_path):
    storage = Storage(tmp_path)
    record = DecisionRecord(
        user=XUser(id="1", username="quiet", name="Quiet"),
        decision="candidate",
        reason="test reason",
        last_activity_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        days_since_activity=523,
        last_activity_id="123",
        review="unfollow",
        account_status="ok",
    )

    export_path = storage.export_candidates_csv([record])

    assert export_path.exists()
    content = export_path.read_text(encoding="utf-8")
    assert "username" in content
    assert "account_status" in content
    assert "review" in content
    assert "reason" in content
    assert "quiet" in content


def test_storage_exports_only_candidate_records(tmp_path):
    storage = Storage(tmp_path)
    candidate = DecisionRecord(
        user=XUser(id="1", username="quiet", name="Quiet"),
        decision="candidate",
        reason="candidate reason",
        last_activity_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        days_since_activity=523,
        last_activity_id="123",
        review="unfollow",
    )
    keep = DecisionRecord(
        user=XUser(id="2", username="active", name="Active"),
        decision="keep",
        reason="keep reason",
        last_activity_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        days_since_activity=6,
        last_activity_id="456",
    )

    export_path = storage.export_candidates_csv([candidate, keep])

    content = export_path.read_text(encoding="utf-8")
    assert "quiet" in content
    assert "active" not in content


def test_storage_exports_all_scan_results_with_evidence_urls_and_rule_snapshot(
    tmp_path,
):
    storage = Storage(tmp_path)
    record = DecisionRecord(
        user=XUser(id="1", username="quiet", name="\t=Formula"),
        decision="candidate",
        reason="inactive",
        last_activity_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        days_since_activity=583,
        last_activity_id="activity-1",
        scanned_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        scan_run_id="run-1",
        scan_cycle_id="cycle-1",
        scan_batch_number=1,
        scan_position=1,
    )

    results_path = storage.export_scan_results([record], AppConfig())
    history_path = storage.append_scan_history([record], AppConfig())
    storage.append_scan_history([record], AppConfig())

    results = results_path.read_text(encoding="utf-8")
    history = history_path.read_text(encoding="utf-8")
    assert "https://x.com/quiet/status/activity-1" in results
    assert "activity_threshold_days" in results
    assert "own_post_threshold_days" not in results
    assert "180" in results
    assert "'\t=Formula" in results
    assert "run-1" in history
    assert len(history_path.read_text(encoding="utf-8").splitlines()) == 2
    assert stat.S_IMODE(results_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(history_path.stat().st_mode) == 0o600


def test_scan_history_projects_legacy_rows_onto_current_export_schema(tmp_path):
    storage = Storage(tmp_path)
    storage.exports_dir.mkdir(parents=True)
    storage.scan_history_export_path.write_text(
        "id,username,own_post_threshold_days\n1,legacy,180\n",
        encoding="utf-8",
    )
    record = DecisionRecord(
        user=XUser(id="2", username="current", name="Current"),
        decision="keep",
        reason="recent activity",
        last_activity_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        days_since_activity=7,
        last_activity_id="123",
        scan_cycle_id="cycle-2",
        scan_position=1,
    )

    storage.append_scan_history([record], AppConfig())

    content = storage.scan_history_export_path.read_text(encoding="utf-8")
    assert "own_post_threshold_days" not in content
    assert "legacy" in content
    assert "current" in content
