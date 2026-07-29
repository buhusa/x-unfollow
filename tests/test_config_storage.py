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
    CombinationMode,
    DecisionRecord,
    RuleConfig,
    XUser,
)
from x_unfollow.storage import Storage
from x_unfollow.oauth import OAuthToken
from x_unfollow.tokens import OAuthCredentials, TokenStore


def test_write_and_load_default_config(tmp_path):
    config_path = tmp_path / "config.toml"
    write_default_config(config_path)
    config = load_config(config_path)
    assert config.rules.own_post_threshold_days == 180
    assert config.rules.combination == CombinationMode.AND
    assert config.api.max_accounts_per_scan == 10
    assert config.api.max_tweet_pages_per_user == 1
    assert config.api.max_scan_cost_usd == 0.50


def test_write_config_round_trips_custom_values(tmp_path):
    path = tmp_path / "config.toml"
    expected = AppConfig(
        rules=RuleConfig(
            own_post_threshold_days=90,
            reply_threshold_days=30,
            combination=CombinationMode.OR,
        ),
        api=ApiConfig(
            page_size_following=100,
            page_size_tweets=5,
            max_tweet_pages_per_user=2,
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
                "own_post_threshold_days = 180",
                'combination = "and"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("X_UNFOLLOW_COMBINATION", "or")
    monkeypatch.setenv("X_UNFOLLOW_OWN_POST_THRESHOLD_DAYS", "90")

    config = load_config(config_path)

    assert config.rules.combination == CombinationMode.OR
    assert config.rules.own_post_threshold_days == 90


@pytest.mark.parametrize("max_pages", [0, -1])
def test_api_config_rejects_non_positive_max_tweet_pages(max_pages):
    with pytest.raises(ValueError, match="max_tweet_pages_per_user"):
        ApiConfig(max_tweet_pages_per_user=max_pages)


def test_api_config_rejects_non_positive_page_size_tweets():
    with pytest.raises(ValueError, match="page_size_tweets"):
        ApiConfig(page_size_tweets=0)


def test_api_config_rejects_tweet_page_size_outside_x_api_range():
    with pytest.raises(ValueError, match="page_size_tweets"):
        ApiConfig(page_size_tweets=101)


def test_boolean_environment_overrides_parse_true_false(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[rules]",
                "count_retweets_as_activity = false",
                "count_quote_posts_as_own_posts = true",
                "",
                "[safety]",
                "require_review_before_unfollow = true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("X_UNFOLLOW_COUNT_RETWEETS_AS_ACTIVITY", "true")
    monkeypatch.setenv("X_UNFOLLOW_COUNT_QUOTE_POSTS_AS_OWN_POSTS", "false")
    monkeypatch.setenv("X_UNFOLLOW_REQUIRE_REVIEW_BEFORE_UNFOLLOW", "false")

    config = load_config(config_path)

    assert config.rules.count_retweets_as_activity is True
    assert config.rules.count_quote_posts_as_own_posts is False
    assert config.safety.require_review_before_unfollow is False


def test_storage_round_trip_decisions(tmp_path):
    storage = Storage(tmp_path)
    record = DecisionRecord(
        user=XUser(id="1", username="quiet", name="Quiet"),
        last_own_post_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        days_since_own_post=523,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision="candidate",
        reason="test",
        account_status="ok",
    )
    storage.save_decisions([record])
    loaded = storage.load_decisions()
    assert loaded[0].user.username == "quiet"
    assert loaded[0].last_own_post_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
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
        last_own_post_at=None,
        days_since_own_post=None,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision="candidate",
        reason="test",
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
        last_own_post_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        days_since_own_post=523,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision="candidate",
        reason="test reason",
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
        last_own_post_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        days_since_own_post=523,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision="candidate",
        reason="candidate reason",
        review="unfollow",
    )
    keep = DecisionRecord(
        user=XUser(id="2", username="active", name="Active"),
        last_own_post_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        days_since_own_post=7,
        last_reply_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        days_since_reply=6,
        rule_match_own_post=False,
        rule_match_reply=False,
        decision="keep",
        reason="keep reason",
    )

    export_path = storage.export_candidates_csv([candidate, keep])

    content = export_path.read_text(encoding="utf-8")
    assert "quiet" in content
    assert "active" not in content
