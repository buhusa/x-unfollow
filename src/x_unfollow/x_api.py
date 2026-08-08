from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from x_unfollow import __version__
from x_unfollow.models import XPost, XUser


USER_FIELDS = "id,username,name,protected,verified,public_metrics,most_recent_tweet_id"
TWEET_FIELDS = "id,created_at,author_id,referenced_tweets,in_reply_to_user_id,text"


class XApiError(RuntimeError):
    """Raised when X returns an error response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(XApiError):
    """Raised when X rate limits a request."""

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message, status_code=429)
        self.reset_at = reset_at


class XApiClient:
    base_url = "https://api.x.com/2"

    def __init__(
        self,
        bearer_token: str,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": f"x-unfollow/{__version__}",
        }
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers=headers,
            transport=transport,
            timeout=timeout,
        )
        if client is not None:
            self._client.headers.update(headers)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> XApiClient:
        return self

    def __exit__(self, *args: object) -> None:
        if self._owns_client:
            self.close()

    def get_me(self) -> XUser:
        response = self._request("GET", "/users/me", params={"user.fields": USER_FIELDS})
        return _parse_user(response.json()["data"])

    def get_following(
        self,
        user_id: str,
        page_size: int,
        limit: int | None = None,
    ) -> list[XUser]:
        users, _next_token = self.get_following_batch(
            user_id,
            page_size,
            limit,
        )
        return users

    def get_following_batch(
        self,
        user_id: str,
        page_size: int,
        limit: int | None = None,
        pagination_token: str | None = None,
    ) -> tuple[list[XUser], str | None]:
        users: list[XUser] = []
        next_token = pagination_token

        while True:
            remaining = None if limit is None else limit - len(users)
            if remaining is not None and remaining <= 0:
                return users, next_token
            params = {
                "max_results": page_size if remaining is None else min(page_size, remaining),
                "user.fields": USER_FIELDS,
            }
            if next_token:
                params["pagination_token"] = next_token

            response = self._request("GET", f"/users/{user_id}/following", params=params)
            payload = response.json()
            users.extend(_parse_user(item) for item in payload.get("data", []))
            next_token = payload.get("meta", {}).get("next_token")
            if limit is not None and len(users) >= limit:
                return users[:limit], next_token
            if not next_token:
                return users, None

    def get_user_posts(
        self,
        user_id: str,
        page_size: int,
        pagination_token: str | None = None,
        exclude_retweets: bool = False,
    ) -> tuple[list[XPost], str | None]:
        params = {
            "max_results": page_size,
            "tweet.fields": TWEET_FIELDS,
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        if exclude_retweets:
            params["exclude"] = "retweets"

        response = self._request("GET", f"/users/{user_id}/tweets", params=params)
        payload = response.json()
        posts = [_parse_post(item) for item in payload.get("data", [])]
        next_token = payload.get("meta", {}).get("next_token")
        return posts, next_token

    def get_posts_by_ids(self, post_ids: list[str]) -> list[XPost]:
        if not post_ids:
            return []
        if len(post_ids) > 100:
            raise ValueError("X allows at most 100 post IDs per lookup request")
        response = self._request(
            "GET",
            "/tweets",
            params={
                "ids": ",".join(post_ids),
                "tweet.fields": TWEET_FIELDS,
            },
        )
        return [_parse_post(item) for item in response.json().get("data", [])]

    def unfollow(self, source_user_id: str, target_user_id: str) -> bool:
        response = self._request(
            "DELETE",
            f"/users/{source_user_id}/following/{target_user_id}",
        )
        payload = response.json()
        return payload.get("data", {}).get("following") is False

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise XApiError(f"X API request failed: {exc}") from exc
        if response.status_code >= 400:
            raise _error_from_response(response)
        return response


def _parse_user(data: dict[str, Any]) -> XUser:
    return XUser(
        id=str(data["id"]),
        username=data["username"],
        name=data["name"],
        most_recent_tweet_id=(
            str(data["most_recent_tweet_id"])
            if data.get("most_recent_tweet_id")
            else None
        ),
        protected=bool(data.get("protected", False)),
        verified=bool(data.get("verified", False)),
        public_metrics=dict(data.get("public_metrics", {})),
    )


def _parse_post(data: dict[str, Any]) -> XPost:
    return XPost(
        id=str(data["id"]),
        author_id=str(data["author_id"]),
        created_at=_parse_x_datetime(data["created_at"]),
        referenced_tweets=list(data.get("referenced_tweets", [])),
        in_reply_to_user_id=data.get("in_reply_to_user_id"),
        text=data.get("text", ""),
    )


def _parse_x_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _error_from_response(response: httpx.Response) -> XApiError:
    message = _friendly_error_message(response)
    if response.status_code == 429:
        reset_at = _parse_rate_limit_reset(response.headers.get("x-rate-limit-reset"))
        message = f"X API rate limit reached: {message}"
        return RateLimitError(message, reset_at=reset_at)
    return XApiError(message, status_code=response.status_code)


def _friendly_error_message(response: httpx.Response) -> str:
    parts = [f"X API returned {response.status_code}"]
    try:
        payload = response.json()
    except ValueError:
        if response.text:
            parts.append(response.text)
        return ": ".join(parts)

    if not isinstance(payload, dict):
        if payload:
            parts.append(str(payload))
        return ": ".join(parts)

    for key in ("title", "detail", "message"):
        value = payload.get(key)
        if value:
            parts.append(str(value))

    for error in payload.get("errors", []):
        if isinstance(error, dict):
            value = error.get("message") or error.get("detail") or error.get("title")
            if value:
                parts.append(str(value))

    return ": ".join(parts)


def _parse_rate_limit_reset(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except ValueError:
        return None
