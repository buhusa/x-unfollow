from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from x_unfollow.models import AppConfig, DecisionRecord, ScanBatch, XUser


X_SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
X_SNOWFLAKE_RELIABLE_FROM = datetime(2010, 11, 6, tzinfo=timezone.utc)


class ScanApi(Protocol):
    def get_following(
        self,
        user_id: str,
        page_size: int,
        limit: int | None = None,
    ) -> list[XUser]:
        ...

    def get_following_batch(
        self,
        user_id: str,
        page_size: int,
        limit: int | None = None,
        pagination_token: str | None = None,
    ) -> tuple[list[XUser], str | None]:
        ...


ScanProgress = Callable[[str, int, int, XUser | None], None]


def scan_accounts(
    api: ScanApi,
    source_user_id: str,
    config: AppConfig,
    now: datetime | None = None,
    limit: int | None = None,
    progress: ScanProgress | None = None,
) -> list[DecisionRecord]:
    now = now or datetime.now(timezone.utc)
    effective_limit = limit or config.api.max_accounts_per_scan
    following = api.get_following(
        source_user_id,
        page_size=config.api.page_size_following,
        limit=effective_limit,
    )
    return _scan_following(following, config, now, progress)


def scan_account_batch(
    api: ScanApi,
    source_user_id: str,
    config: AppConfig,
    now: datetime | None = None,
    limit: int | None = None,
    pagination_token: str | None = None,
    progress: ScanProgress | None = None,
) -> ScanBatch:
    now = now or datetime.now(timezone.utc)
    effective_limit = limit or config.api.max_accounts_per_scan
    following, next_token = api.get_following_batch(
        source_user_id,
        page_size=config.api.page_size_following,
        limit=effective_limit,
        pagination_token=pagination_token,
    )
    records = _scan_following(following, config, now, progress)
    return ScanBatch(records=records, next_token=next_token)


def _scan_following(
    following: list[XUser],
    config: AppConfig,
    now: datetime,
    progress: ScanProgress | None,
) -> list[DecisionRecord]:
    total = len(following)
    if progress is not None:
        progress("following_loaded", total, total, None)
        progress("local_activity", 0, total, None)
    return [
        _scan_account(user, config, now, index, total, progress)
        for index, user in enumerate(following, start=1)
    ]


def snowflake_created_at(post_id: str) -> datetime | None:
    try:
        snowflake = int(post_id)
    except (TypeError, ValueError):
        return None
    if snowflake <= 0:
        return None

    timestamp_ms = (snowflake >> 22) + X_SNOWFLAKE_EPOCH_MS
    try:
        created_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if created_at < X_SNOWFLAKE_RELIABLE_FROM:
        return None
    return created_at


def _scan_account(
    user: XUser,
    config: AppConfig,
    now: datetime,
    index: int,
    total: int,
    progress: ScanProgress | None,
) -> DecisionRecord:
    if progress is not None:
        progress("account", index, total, user)

    post_id = user.most_recent_tweet_id
    activity_at = snowflake_created_at(post_id) if post_id else None
    if activity_at is None or activity_at > now + timedelta(days=1):
        return DecisionRecord(
            user=user,
            decision="keep",
            reason=(
                "X did not provide a valid latest-activity Snowflake ID; "
                "keeping because the result is incomplete."
            ),
            account_status="incomplete",
        )

    days_since_activity = max(0, (now - activity_at).days)
    threshold = config.rules.activity_threshold_days
    is_candidate = days_since_activity >= threshold
    reason = (
        f"no X activity of any type >= {threshold}d"
        if is_candidate
        else f"X activity found within the last {threshold}d"
    )
    return DecisionRecord(
        user=user,
        decision="candidate" if is_candidate else "keep",
        reason=reason,
        last_activity_at=activity_at,
        days_since_activity=days_since_activity,
        last_activity_id=post_id,
    )
