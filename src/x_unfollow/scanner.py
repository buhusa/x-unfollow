from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol

from x_unfollow.decisions import classify_posts, evaluate_account
from x_unfollow.models import (
    ActivitySummary,
    AppConfig,
    CombinationMode,
    DecisionRecord,
    XPost,
    XUser,
)
from x_unfollow.x_api import XApiError


class ScanApi(Protocol):
    def get_following(
        self,
        user_id: str,
        page_size: int,
        limit: int | None = None,
    ) -> list[XUser]:
        ...

    def get_user_posts(
        self,
        user_id: str,
        page_size: int,
        pagination_token: str | None = None,
        exclude_retweets: bool = False,
    ) -> tuple[list[XPost], str | None]:
        ...

    def get_posts_by_ids(self, post_ids: list[str]) -> list[XPost]:
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
    if progress is not None:
        progress("following_loaded", len(following), len(following), None)
        progress("latest_activity", 0, len(following), None)
    latest_posts = _load_latest_posts(api, following, config)

    records = []
    total = len(following)
    for index, user in enumerate(following, start=1):
        if progress is not None:
            progress("account", index, total, user)
        records.append(
            _scan_account(
                api=api,
                user=user,
                config=config,
                now=now,
                latest_post=latest_posts.get(user.id),
            )
        )
    return records


def _load_latest_posts(
    api: ScanApi,
    following: list[XUser],
    config: AppConfig,
) -> dict[str, XPost]:
    rules = config.rules
    fast_path_enabled = (
        rules.combination == CombinationMode.AND
        and rules.own_post_threshold_days == rules.reply_threshold_days
        and not rules.count_retweets_as_activity
    )
    if not fast_path_enabled:
        return {}

    post_ids = [
        user.most_recent_tweet_id
        for user in following
        if user.most_recent_tweet_id
    ]
    posts: list[XPost] = []
    for start in range(0, len(post_ids), 100):
        posts.extend(api.get_posts_by_ids(post_ids[start : start + 100]))
    posts_by_id = {post.id: post for post in posts}
    return {
        user.id: posts_by_id[user.most_recent_tweet_id]
        for user in following
        if user.most_recent_tweet_id in posts_by_id
    }


def _scan_account(
    api: ScanApi,
    user: XUser,
    config: AppConfig,
    now: datetime,
    latest_post: XPost | None = None,
) -> DecisionRecord:
    if latest_post is not None and not any(
        ref.get("type") == "retweeted" for ref in latest_post.referenced_tweets
    ):
        activity = classify_posts([latest_post], config.rules)
        return evaluate_account(
            user=user,
            last_own_post_at=activity.last_own_post_at,
            last_reply_at=activity.last_reply_at,
            now=now,
            rules=config.rules,
        )

    posts: list[XPost] = []
    next_token: str | None = None
    activity = classify_posts(posts, config.rules)

    for _ in range(config.api.max_tweet_pages_per_user):
        try:
            page_posts, next_token = api.get_user_posts(
                user.id,
                page_size=config.api.page_size_tweets,
                pagination_token=next_token,
                exclude_retweets=not config.rules.count_retweets_as_activity,
            )
        except XApiError as exc:
            if exc.status_code in {401, 403, 404}:
                return _inaccessible_record(
                    user=user,
                    reason=(
                        "Account is inaccessible via X API "
                        f"(status {exc.status_code}); keeping for manual review."
                    ),
                    account_status="inaccessible",
                )
            raise

        posts.extend(page_posts)
        activity = classify_posts(posts, config.rules)

        if activity.last_own_post_at and activity.last_reply_at:
            break
        if _partial_activity_is_decisive(activity, posts, config, now):
            return evaluate_account(
                user=user,
                last_own_post_at=activity.last_own_post_at,
                last_reply_at=activity.last_reply_at,
                now=now,
                rules=config.rules,
            )
        if not next_token:
            break

    if next_token and (
        activity.last_own_post_at is None or activity.last_reply_at is None
    ):
        return _inaccessible_record(
            user=user,
            reason=(
                "Scan page limit reached before both activity types were found; "
                "keeping because the result is incomplete."
            ),
            account_status="incomplete",
        )

    if (
        user.most_recent_tweet_id
        and activity.last_own_post_at is None
        and activity.last_reply_at is None
    ):
        return _inaccessible_record(
            user=user,
            reason=(
                "X reports recent activity, but no qualifying own post or reply "
                "could be verified; keeping because the result is incomplete."
            ),
            account_status="incomplete",
        )

    return evaluate_account(
        user=user,
        last_own_post_at=activity.last_own_post_at,
        last_reply_at=activity.last_reply_at,
        now=now,
        rules=config.rules,
    )


def _partial_activity_is_decisive(
    activity: ActivitySummary,
    posts: list[XPost],
    config: AppConfig,
    now: datetime,
) -> bool:
    own_days = (
        (now - activity.last_own_post_at).days
        if activity.last_own_post_at is not None
        else None
    )
    reply_days = (
        (now - activity.last_reply_at).days
        if activity.last_reply_at is not None
        else None
    )

    if config.rules.combination == CombinationMode.AND:
        if (
            own_days is not None
            and own_days < config.rules.own_post_threshold_days
        ):
            return True
        if (
            reply_days is not None
            and reply_days < config.rules.reply_threshold_days
        ):
            return True

        relevant_posts = [
            post
            for post in posts
            if config.rules.count_retweets_as_activity
            or not any(
                ref.get("type") == "retweeted" for ref in post.referenced_tweets
            )
        ]
        if relevant_posts:
            newest_activity = max(post.created_at for post in relevant_posts)
            oldest_required_days = max(
                config.rules.own_post_threshold_days,
                config.rules.reply_threshold_days,
            )
            if (now - newest_activity).days >= oldest_required_days:
                return True
        return False

    own_is_inactive = (
        own_days is not None
        and own_days >= config.rules.own_post_threshold_days
    )
    reply_is_inactive = (
        reply_days is not None
        and reply_days >= config.rules.reply_threshold_days
    )
    return own_is_inactive or reply_is_inactive


def _inaccessible_record(
    *,
    user: XUser,
    reason: str,
    account_status: str,
) -> DecisionRecord:
    return DecisionRecord(
        user=user,
        last_own_post_at=None,
        days_since_own_post=None,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=False,
        rule_match_reply=False,
        decision="keep",
        reason=reason,
        account_status=account_status,
    )
