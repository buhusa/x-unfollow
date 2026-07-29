from datetime import datetime, timezone

import pytest

from x_unfollow.models import (
    ApiConfig,
    AppConfig,
    CombinationMode,
    RuleConfig,
    XPost,
    XUser,
)
from x_unfollow.scanner import scan_accounts
from x_unfollow.x_api import RateLimitError, XApiError


NOW = datetime(2026, 6, 8, tzinfo=timezone.utc)


class FakeScanApi:
    def __init__(self, following, post_pages, latest_posts=None):
        self.following = following
        self.post_pages = post_pages
        self.latest_posts = latest_posts or []
        self.following_calls = []
        self.post_calls = []
        self.latest_post_calls = []

    def get_following(self, user_id: str, page_size: int, limit: int | None = None):
        self.following_calls.append((user_id, page_size, limit))
        return self.following[:limit]

    def get_user_posts(
        self,
        user_id: str,
        page_size: int,
        pagination_token: str | None = None,
        exclude_retweets: bool = False,
    ):
        self.post_calls.append(
            (user_id, page_size, pagination_token, exclude_retweets)
        )
        result = self.post_pages[(user_id, pagination_token)]
        if isinstance(result, Exception):
            raise result
        return result

    def get_posts_by_ids(self, post_ids: list[str]):
        self.latest_post_calls.append(post_ids)
        return [post for post in self.latest_posts if post.id in post_ids]


def post(
    post_id: str,
    user_id: str,
    created_at: datetime,
    *,
    reply: bool = False,
):
    return XPost(
        id=post_id,
        author_id=user_id,
        created_at=created_at,
        in_reply_to_user_id="source" if reply else None,
        referenced_tweets=[{"type": "replied_to", "id": "root"}] if reply else [],
    )


def test_scan_accounts_generates_candidate_records_from_followings():
    user = XUser(id="u1", username="quiet", name="Quiet")
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("u1", None): (
                [
                    post("own", "u1", datetime(2025, 1, 1, tzinfo=timezone.utc)),
                    post(
                        "reply",
                        "u1",
                        datetime(2025, 1, 2, tzinfo=timezone.utc),
                        reply=True,
                    ),
                ],
                None,
            )
        },
    )
    config = AppConfig(
        rules=RuleConfig(own_post_threshold_days=180, reply_threshold_days=180),
        api=ApiConfig(page_size_following=2, page_size_tweets=5),
    )

    records = scan_accounts(api, source_user_id="source", config=config, now=NOW)

    assert api.following_calls == [("source", 2, 10)]
    assert api.post_calls == [("u1", 5, None, True)]
    assert len(records) == 1
    assert records[0].user == user
    assert records[0].decision == "candidate"
    assert records[0].last_own_post_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert records[0].last_reply_at == datetime(2025, 1, 2, tzinfo=timezone.utc)


def test_scan_accounts_stops_fetching_after_both_activity_timestamps_are_known():
    user = XUser(id="active", username="active", name="Active")
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("active", None): (
                [
                    post("own", "active", datetime(2026, 6, 1, tzinfo=timezone.utc)),
                    post(
                        "reply",
                        "active",
                        datetime(2026, 5, 31, tzinfo=timezone.utc),
                        reply=True,
                    ),
                ],
                "older",
            ),
            ("active", "older"): ([], None),
        },
    )
    config = AppConfig(
        api=ApiConfig(
            page_size_following=10,
            page_size_tweets=5,
            max_tweet_pages_per_user=5,
        )
    )

    records = scan_accounts(api, source_user_id="source", config=config, now=NOW)

    assert api.post_calls == [("active", 5, None, True)]
    assert records[0].decision == "keep"


def test_and_rule_stops_after_one_recent_activity_type():
    user = XUser(id="active", username="active", name="Active")
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("active", None): (
                [post("own", "active", datetime(2026, 6, 1, tzinfo=timezone.utc))],
                "older",
            ),
        },
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.post_calls == [("active", 5, None, True)]
    assert records[0].decision == "keep"
    assert records[0].account_status == "ok"


def test_default_rules_use_one_latest_post_per_account_without_timeline_pages():
    recent_user = XUser(
        id="recent",
        username="recent",
        name="Recent",
        most_recent_tweet_id="recent-post",
    )
    old_user = XUser(
        id="old",
        username="old",
        name="Old",
        most_recent_tweet_id="old-post",
    )
    api = FakeScanApi(
        following=[recent_user, old_user],
        post_pages={},
        latest_posts=[
            post(
                "recent-post",
                "recent",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            ),
            post("old-post", "old", datetime(2025, 1, 1, tzinfo=timezone.utc)),
        ],
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.latest_post_calls == [["recent-post", "old-post"]]
    assert api.post_calls == []
    assert [record.decision for record in records] == ["keep", "candidate"]


def test_latest_posts_are_matched_by_requested_post_id_not_response_author():
    user = XUser(
        id="account",
        username="account",
        name="Account",
        most_recent_tweet_id="latest-post",
    )
    api = FakeScanApi(
        following=[user],
        post_pages={},
        latest_posts=[
            post(
                "latest-post",
                "unexpected-author",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )
        ],
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.post_calls == []
    assert records[0].decision == "keep"
    assert records[0].last_own_post_at == datetime(
        2026, 6, 1, tzinfo=timezone.utc
    )


def test_latest_retweet_falls_back_to_filtered_timeline():
    user = XUser(
        id="reposter",
        username="reposter",
        name="Reposter",
        most_recent_tweet_id="retweet",
    )
    latest_retweet = XPost(
        id="retweet",
        author_id="reposter",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        referenced_tweets=[{"type": "retweeted", "id": "original"}],
    )
    api = FakeScanApi(
        following=[user],
        latest_posts=[latest_retweet],
        post_pages={
            ("reposter", None): (
                [post("own", "reposter", datetime(2025, 1, 1, tzinfo=timezone.utc))],
                None,
            )
        },
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.latest_post_calls == [["retweet"]]
    assert api.post_calls == [("reposter", 5, None, True)]
    assert records[0].decision == "candidate"


def test_equal_and_thresholds_use_oldest_latest_activity_as_complete_proof():
    user = XUser(id="quiet", username="quiet", name="Quiet")
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("quiet", None): (
                [post("own", "quiet", datetime(2025, 1, 1, tzinfo=timezone.utc))],
                "older",
            ),
        },
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.post_calls == [("quiet", 5, None, True)]
    assert records[0].decision == "candidate"
    assert records[0].account_status == "ok"


def test_or_rule_stops_when_one_known_activity_type_is_old_enough():
    user = XUser(id="quiet", username="quiet", name="Quiet")
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("quiet", None): (
                [post("own", "quiet", datetime(2025, 1, 1, tzinfo=timezone.utc))],
                "older",
            ),
        },
    )
    config = AppConfig(
        rules=RuleConfig(combination=CombinationMode.OR),
    )

    records = scan_accounts(api, source_user_id="source", config=config, now=NOW)

    assert api.post_calls == [("quiet", 5, None, True)]
    assert records[0].decision == "candidate"


def test_scan_accounts_keeps_account_when_page_cap_makes_result_incomplete():
    user = XUser(id="empty", username="empty", name="Empty")
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("empty", None): ([], "page-2"),
            ("empty", "page-2"): ([], "page-3"),
            ("empty", "page-3"): ([], "page-4"),
        },
    )
    config = AppConfig(
        api=ApiConfig(
            page_size_following=10,
            page_size_tweets=5,
            max_tweet_pages_per_user=2,
        )
    )

    records = scan_accounts(api, source_user_id="source", config=config, now=NOW)

    assert api.post_calls == [
        ("empty", 5, None, True),
        ("empty", 5, "page-2", True),
    ]
    assert records[0].decision == "keep"
    assert records[0].account_status == "incomplete"
    assert "page limit" in records[0].reason
    assert records[0].last_own_post_at is None
    assert records[0].last_reply_at is None


def test_scan_accounts_uses_explicit_limit_instead_of_config_default():
    users = [
        XUser(id=str(index), username=f"user{index}", name=f"User {index}")
        for index in range(3)
    ]
    api = FakeScanApi(
        following=users,
        post_pages={
            ("0", None): ([], None),
            ("1", None): ([], None),
        },
    )

    records = scan_accounts(
        api,
        source_user_id="source",
        config=AppConfig(),
        now=NOW,
        limit=2,
    )

    assert api.following_calls == [("source", 1000, 2)]
    assert [record.user.username for record in records] == ["user0", "user1"]


def test_scan_accounts_treats_empty_posts_as_candidate_under_default_and_rules():
    user = XUser(id="silent", username="silent", name="Silent")
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("silent", None): ([], None),
        },
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.post_calls == [("silent", 5, None, True)]
    assert records[0].decision == "candidate"
    assert records[0].rule_match_own_post is True
    assert records[0].rule_match_reply is True


def test_missing_latest_lookup_and_empty_timeline_is_incomplete_not_candidate():
    user = XUser(
        id="active",
        username="active",
        name="Active",
        most_recent_tweet_id="missing-from-lookup",
    )
    api = FakeScanApi(
        following=[user],
        latest_posts=[],
        post_pages={
            ("active", None): ([], None),
        },
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert records[0].decision == "keep"
    assert records[0].account_status == "incomplete"
    assert "no qualifying own post or reply" in records[0].reason


def test_scan_accounts_can_read_protected_users_followed_by_authenticated_user():
    user = XUser(id="locked", username="locked", name="Locked", protected=True)
    api = FakeScanApi(
        following=[user],
        post_pages={
            ("locked", None): (
                [
                    post(
                        "own",
                        "locked",
                        datetime(2026, 6, 1, tzinfo=timezone.utc),
                    )
                ],
                None,
            )
        },
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.post_calls == [("locked", 5, None, True)]
    assert len(records) == 1
    assert records[0].user == user
    assert records[0].decision == "keep"
    assert records[0].account_status == "ok"
    assert records[0].last_own_post_at == datetime(
        2026, 6, 1, tzinfo=timezone.utc
    )
    assert records[0].days_since_own_post == 7
    assert records[0].last_reply_at is None
    assert records[0].days_since_reply is None
    assert records[0].rule_match_own_post is False
    assert records[0].rule_match_reply is True


def test_scan_accounts_marks_inaccessible_user_as_keep_and_continues_scanning():
    inaccessible = XUser(id="denied", username="denied", name="Denied")
    active = XUser(id="active", username="active", name="Active")
    inaccessible_error = XApiError("X API returned 403: Forbidden")
    inaccessible_error.status_code = 403
    api = FakeScanApi(
        following=[inaccessible, active],
        post_pages={
            ("denied", None): inaccessible_error,
            ("active", None): (
                [
                    post("own", "active", datetime(2026, 6, 1, tzinfo=timezone.utc)),
                    post(
                        "reply",
                        "active",
                        datetime(2026, 6, 2, tzinfo=timezone.utc),
                        reply=True,
                    ),
                ],
                None,
            ),
        },
    )

    records = scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)

    assert api.post_calls == [
        ("denied", 5, None, True),
        ("active", 5, None, True),
    ]
    assert records[0].user == inaccessible
    assert records[0].decision == "keep"
    assert records[0].account_status == "inaccessible"
    assert records[0].rule_match_own_post is False
    assert records[0].rule_match_reply is False
    assert records[0].last_own_post_at is None
    assert records[0].last_reply_at is None
    assert "inaccessible" in records[0].reason
    assert "403" in records[0].reason
    assert records[1].user == active
    assert records[1].decision == "keep"
    assert records[1].account_status == "ok"


def test_scan_accounts_propagates_rate_limit_errors():
    user = XUser(id="error", username="error", name="Error")
    api_error = RateLimitError("X API rate limit reached")
    api = FakeScanApi(
        following=[user],
        post_pages={("error", None): api_error},
    )

    with pytest.raises(type(api_error)):
        scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)


def test_scan_accounts_propagates_server_api_errors():
    user = XUser(id="error", username="error", name="Error")
    api_error = XApiError("X API returned 500: temporary failure")
    api_error.status_code = 500
    api = FakeScanApi(
        following=[user],
        post_pages={("error", None): api_error},
    )

    with pytest.raises(XApiError):
        scan_accounts(api, source_user_id="source", config=AppConfig(), now=NOW)
