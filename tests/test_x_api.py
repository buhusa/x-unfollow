from datetime import datetime, timezone

import httpx
import pytest

from x_unfollow.models import XPost, XUser
from x_unfollow.x_api import USER_FIELDS, RateLimitError, XApiClient, XApiError


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return XApiClient("test-token", transport=transport)


def json_response(status_code=200, payload=None, headers=None):
    return httpx.Response(status_code, json=payload or {}, headers=headers)


def test_get_me_sends_bearer_auth_and_parses_user():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/2/users/me"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers["user-agent"].startswith("x-unfollow/")
        assert request.url.params["user.fields"] == USER_FIELDS
        return json_response(
            payload={
                "data": {
                    "id": "123",
                    "username": "quiet",
                    "name": "Quiet User",
                    "protected": True,
                    "verified": False,
                    "public_metrics": {"followers_count": 10},
                }
            }
        )

    assert make_client(handler).get_me() == XUser(
        id="123",
        username="quiet",
        name="Quiet User",
        protected=True,
        verified=False,
        public_metrics={"followers_count": 10},
    )


def test_get_me_works_with_injected_plain_httpx_client():
    def handler(request):
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "api.x.com"
        assert request.url.path == "/2/users/me"
        assert request.url.params["user.fields"] == USER_FIELDS
        return json_response(
            payload={
                "data": {
                    "id": "123",
                    "username": "quiet",
                    "name": "Quiet User",
                }
            }
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))

    assert XApiClient("test-token", client=http_client).get_me() == XUser(
        id="123",
        username="quiet",
        name="Quiet User",
    )


def test_get_following_paginates_with_user_fields_and_next_token():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/2/users/123/following"
        assert request.url.params["max_results"] == "2"
        assert request.url.params["user.fields"] == (
            "id,username,name,protected,verified,public_metrics,most_recent_tweet_id"
        )
        if len(requests) == 1:
            assert "pagination_token" not in request.url.params
            return json_response(
                payload={
                    "data": [
                        {
                            "id": "1",
                            "username": "one",
                            "name": "One",
                            "most_recent_tweet_id": "latest-1",
                        }
                    ],
                    "meta": {"next_token": "next-page"},
                }
            )
        assert request.url.params["pagination_token"] == "next-page"
        return json_response(
            payload={"data": [{"id": "2", "username": "two", "name": "Two"}], "meta": {}}
        )

    users = make_client(handler).get_following("123", page_size=2)

    assert [user.username for user in users] == ["one", "two"]
    assert users[0].most_recent_tweet_id == "latest-1"
    assert len(requests) == 2


def test_get_following_limits_api_page_size_to_avoid_extra_billable_users():
    def handler(request):
        assert request.url.params["max_results"] == "3"
        return json_response(
            payload={
                "data": [
                    {"id": str(index), "username": f"u{index}", "name": f"U {index}"}
                    for index in range(3)
                ],
                "meta": {"next_token": "unused"},
            }
        )

    users = make_client(handler).get_following("123", page_size=1000, limit=3)

    assert len(users) == 3


def test_get_posts_by_ids_batches_fields_and_parses_activity():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/2/tweets"
        assert request.url.params["ids"] == "p1,p2"
        assert request.url.params["tweet.fields"] == (
            "id,created_at,author_id,referenced_tweets,in_reply_to_user_id,text"
        )
        return json_response(
            payload={
                "data": [
                    {
                        "id": "p1",
                        "author_id": "u1",
                        "created_at": "2026-06-01T00:00:00Z",
                        "text": "latest",
                    }
                ],
                "errors": [{"resource_id": "p2", "title": "Not Found"}],
            }
        )

    posts = make_client(handler).get_posts_by_ids(["p1", "p2"])

    assert [post.id for post in posts] == ["p1"]


def test_get_user_posts_returns_posts_and_next_token_with_aware_created_at():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/2/users/123/tweets"
        assert request.url.params["max_results"] == "10"
        assert request.url.params["pagination_token"] == "older"
        assert request.url.params["exclude"] == "retweets"
        assert request.url.params["tweet.fields"] == (
            "id,created_at,author_id,referenced_tweets,in_reply_to_user_id,text"
        )
        return json_response(
            payload={
                "data": [
                    {
                        "id": "p1",
                        "author_id": "123",
                        "created_at": "2026-06-08T10:11:12.000Z",
                        "referenced_tweets": [{"type": "replied_to", "id": "root"}],
                        "in_reply_to_user_id": "456",
                        "text": "hello",
                    }
                ],
                "meta": {"next_token": "newer"},
            }
        )

    posts, next_token = make_client(handler).get_user_posts(
        "123",
        page_size=10,
        pagination_token="older",
        exclude_retweets=True,
    )

    assert posts == [
        XPost(
            id="p1",
            author_id="123",
            created_at=datetime(2026, 6, 8, 10, 11, 12, tzinfo=timezone.utc),
            referenced_tweets=[{"type": "replied_to", "id": "root"}],
            in_reply_to_user_id="456",
            text="hello",
        )
    ]
    assert next_token == "newer"


def test_unfollow_returns_true_only_when_api_reports_not_following():
    responses = [
        {"data": {"following": False}},
        {"data": {"following": True}},
    ]

    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == "/2/users/source/following/target"
        return json_response(payload=responses.pop(0))

    client = make_client(handler)

    assert client.unfollow("source", "target") is True
    assert client.unfollow("source", "target") is False


def test_rate_limit_error_includes_reset_at_from_epoch_header():
    def handler(request):
        return json_response(
            status_code=429,
            payload={"title": "Too Many Requests", "detail": "Slow down"},
            headers={"x-rate-limit-reset": "1780912800"},
        )

    with pytest.raises(RateLimitError) as exc_info:
        make_client(handler).get_me()

    assert exc_info.value.reset_at == datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
    assert "rate limit" in str(exc_info.value).lower()


def test_api_error_uses_friendly_message_from_error_payload():
    def handler(request):
        return json_response(
            status_code=403,
            payload={
                "title": "Forbidden",
                "detail": "Your app lacks this OAuth scope.",
                "errors": [{"message": "Missing scope: follows.write"}],
            },
        )

    with pytest.raises(XApiError) as exc_info:
        make_client(handler).unfollow("source", "target")

    message = str(exc_info.value)
    assert exc_info.value.status_code == 403
    assert "X API returned 403" in message
    assert "Your app lacks this OAuth scope." in message
    assert "Missing scope: follows.write" in message


def test_api_error_handles_non_dict_json_payload():
    def handler(request):
        return httpx.Response(500, json=["temporary failure"])

    with pytest.raises(XApiError) as exc_info:
        make_client(handler).get_me()

    message = str(exc_info.value)
    assert "X API returned 500" in message
    assert "temporary failure" in message


def test_transport_error_is_wrapped_as_friendly_x_api_error():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(XApiError) as exc_info:
        make_client(handler).get_me()

    assert exc_info.value.status_code is None
    assert "X API request failed" in str(exc_info.value)
    assert "connection refused" in str(exc_info.value)
