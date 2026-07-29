from __future__ import annotations

from datetime import datetime

from x_unfollow.models import (
    ActivitySummary,
    CombinationMode,
    DecisionRecord,
    RuleConfig,
    XPost,
    XUser,
)


def _has_reference(post: XPost, ref_type: str) -> bool:
    return any(ref.get("type") == ref_type for ref in post.referenced_tweets)


def classify_posts(posts: list[XPost], rules: RuleConfig | None = None) -> ActivitySummary:
    rules = rules or RuleConfig()
    last_own_post_at = None
    last_reply_at = None

    for post in sorted(posts, key=lambda item: item.created_at, reverse=True):
        is_retweet = _has_reference(post, "retweeted")
        is_reply = bool(post.in_reply_to_user_id) or _has_reference(post, "replied_to")
        is_quote = _has_reference(post, "quoted")

        if is_reply and last_reply_at is None:
            last_reply_at = post.created_at

        counts_as_own = not is_reply and not is_quote
        if is_retweet and not rules.count_retweets_as_activity:
            counts_as_own = False
        if is_quote and rules.count_quote_posts_as_own_posts:
            counts_as_own = True

        if counts_as_own and last_own_post_at is None:
            last_own_post_at = post.created_at

        if last_own_post_at and last_reply_at:
            break

    return ActivitySummary(
        last_own_post_at=last_own_post_at,
        last_reply_at=last_reply_at,
    )


def _days_since(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return (now - value).days


def evaluate_account(
    user: XUser,
    last_own_post_at: datetime | None,
    last_reply_at: datetime | None,
    now: datetime,
    rules: RuleConfig,
) -> DecisionRecord:
    days_since_own_post = _days_since(last_own_post_at, now)
    days_since_reply = _days_since(last_reply_at, now)

    own_match = (
        days_since_own_post is None
        or days_since_own_post >= rules.own_post_threshold_days
    )
    reply_match = (
        days_since_reply is None
        or days_since_reply >= rules.reply_threshold_days
    )

    if rules.combination == CombinationMode.AND:
        is_candidate = own_match and reply_match
        joiner = "AND"
    else:
        is_candidate = own_match or reply_match
        joiner = "OR"

    reason = (
        f"no own post >= {rules.own_post_threshold_days}d {joiner} "
        f"no reply >= {rules.reply_threshold_days}d"
    )

    return DecisionRecord(
        user=user,
        last_own_post_at=last_own_post_at,
        days_since_own_post=days_since_own_post,
        last_reply_at=last_reply_at,
        days_since_reply=days_since_reply,
        rule_match_own_post=own_match,
        rule_match_reply=reply_match,
        decision="candidate" if is_candidate else "keep",
        reason=reason,
    )
