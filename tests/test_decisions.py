from datetime import datetime, timezone

from x_unfollow.decisions import classify_posts, evaluate_account
from x_unfollow.models import CombinationMode, RuleConfig, XPost, XUser


NOW = datetime(2026, 6, 8, tzinfo=timezone.utc)


def test_and_policy_requires_both_rules_to_match():
    user = XUser(id="1", username="quiet", name="Quiet")
    result = evaluate_account(
        user=user,
        last_own_post_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        last_reply_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        now=NOW,
        rules=RuleConfig(
            own_post_threshold_days=180,
            reply_threshold_days=180,
            combination=CombinationMode.AND,
        ),
    )
    assert result.decision == "keep"
    assert result.rule_match_own_post is True
    assert result.rule_match_reply is False


def test_or_policy_matches_one_rule():
    user = XUser(id="1", username="quiet", name="Quiet")
    result = evaluate_account(
        user=user,
        last_own_post_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        last_reply_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        now=NOW,
        rules=RuleConfig(
            own_post_threshold_days=180,
            reply_threshold_days=180,
            combination=CombinationMode.OR,
        ),
    )
    assert result.decision == "candidate"


def test_classify_posts_ignores_retweets_and_counts_quotes_as_own_posts():
    posts = [
        XPost(
            id="r",
            author_id="1",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            referenced_tweets=[{"type": "retweeted", "id": "x"}],
        ),
        XPost(
            id="q",
            author_id="1",
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            referenced_tweets=[{"type": "quoted", "id": "y"}],
        ),
        XPost(
            id="reply",
            author_id="1",
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            in_reply_to_user_id="2",
            referenced_tweets=[{"type": "replied_to", "id": "z"}],
        ),
    ]
    activity = classify_posts(posts)
    assert activity.last_own_post_at == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert activity.last_reply_at == datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_classify_posts_can_exclude_quotes_from_own_post_activity():
    quote = XPost(
        id="q",
        author_id="1",
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        referenced_tweets=[{"type": "quoted", "id": "y"}],
    )

    activity = classify_posts(
        [quote],
        RuleConfig(count_quote_posts_as_own_posts=False),
    )

    assert activity.last_own_post_at is None
