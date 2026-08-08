from datetime import datetime, timezone

from x_unfollow.models import ApiConfig, AppConfig, RuleConfig, XUser
from x_unfollow.scanner import (
    X_SNOWFLAKE_EPOCH_MS,
    scan_account_batch,
    scan_accounts,
    snowflake_created_at,
)


NOW = datetime(2026, 6, 8, tzinfo=timezone.utc)


def snowflake_id(created_at: datetime) -> str:
    timestamp_ms = int(created_at.timestamp() * 1000)
    return str((timestamp_ms - X_SNOWFLAKE_EPOCH_MS) << 22)


class FakeScanApi:
    def __init__(self, following):
        self.following = following
        self.following_calls = []

    def get_following(self, user_id: str, page_size: int, limit: int | None = None):
        self.following_calls.append((user_id, page_size, limit))
        return self.following[:limit]

    def get_following_batch(
        self,
        user_id: str,
        page_size: int,
        limit: int | None = None,
        pagination_token: str | None = None,
    ):
        self.following_calls.append((user_id, page_size, limit, pagination_token))
        start = int(pagination_token or 0)
        end = start + (limit or len(self.following))
        next_token = str(end) if end < len(self.following) else None
        return self.following[start:end], next_token


def test_scan_classifies_latest_activity_without_post_api_methods():
    recent_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    old_at = datetime(2025, 1, 1, 8, 30, tzinfo=timezone.utc)
    users = [
        XUser(
            id="recent",
            username="recent",
            name="Recent",
            most_recent_tweet_id=snowflake_id(recent_at),
        ),
        XUser(
            id="old",
            username="old",
            name="Old",
            most_recent_tweet_id=snowflake_id(old_at),
        ),
    ]
    api = FakeScanApi(users)

    records = scan_accounts(api, "source", AppConfig(), now=NOW)

    assert api.following_calls == [("source", 1000, 10)]
    assert [record.decision for record in records] == ["keep", "candidate"]
    assert records[0].last_activity_at == recent_at
    assert records[1].last_activity_at == old_at
    assert not hasattr(api, "get_user_posts")
    assert not hasattr(api, "get_posts_by_ids")


def test_scan_uses_configurable_any_activity_threshold():
    activity_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    user = XUser(
        id="user",
        username="user",
        name="User",
        most_recent_tweet_id=snowflake_id(activity_at),
    )
    api = FakeScanApi([user])
    config = AppConfig(rules=RuleConfig(activity_threshold_days=100))

    record = scan_accounts(api, "source", config, now=NOW)[0]

    assert record.decision == "candidate"
    assert record.reason == "no X activity of any type >= 100d"


def test_scan_keeps_missing_invalid_and_future_activity_as_incomplete():
    future_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    users = [
        XUser(id="missing", username="missing", name="Missing"),
        XUser(
            id="invalid",
            username="invalid",
            name="Invalid",
            most_recent_tweet_id="not-a-snowflake",
        ),
        XUser(
            id="future",
            username="future",
            name="Future",
            most_recent_tweet_id=snowflake_id(future_at),
        ),
    ]

    records = scan_accounts(FakeScanApi(users), "source", AppConfig(), now=NOW)

    assert [record.decision for record in records] == ["keep", "keep", "keep"]
    assert all(record.account_status == "incomplete" for record in records)


def test_scan_batch_uses_and_returns_following_cursor():
    users = [
        XUser(
            id=str(index),
            username=f"u{index}",
            name=f"U {index}",
            most_recent_tweet_id=snowflake_id(
                datetime(2025, 1, index + 1, tzinfo=timezone.utc)
            ),
        )
        for index in range(3)
    ]
    api = FakeScanApi(users)

    batch = scan_account_batch(
        api,
        "source",
        AppConfig(api=ApiConfig(max_accounts_per_scan=2)),
        now=NOW,
        pagination_token="1",
    )

    assert [record.user.username for record in batch.records] == ["u1", "u2"]
    assert batch.next_token is None
    assert api.following_calls == [("source", 1000, 2, "1")]


def test_scan_reports_stable_progress_stages():
    user = XUser(
        id="user",
        username="user",
        name="User",
        most_recent_tweet_id=snowflake_id(datetime(2025, 1, 1, tzinfo=timezone.utc)),
    )
    events = []

    scan_accounts(
        FakeScanApi([user]),
        "source",
        AppConfig(),
        now=NOW,
        progress=lambda *event: events.append(event),
    )

    assert [event[0] for event in events] == [
        "following_loaded",
        "local_activity",
        "account",
    ]


def test_snowflake_decoder_preserves_milliseconds_and_rejects_legacy_ids():
    created_at = datetime(2024, 3, 2, 1, 2, 3, 456000, tzinfo=timezone.utc)

    assert snowflake_created_at(snowflake_id(created_at)) == created_at
    assert snowflake_created_at("20") is None
