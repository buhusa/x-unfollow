import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import x_unfollow.oauth as oauth_module
from x_unfollow.oauth import (
    AUTHORIZATION_URL,
    DEFAULT_REDIRECT_URI,
    DEFAULT_SCOPES,
    TOKEN_URL,
    OAuthError,
    OAuthToken,
    XOAuth2PKCE,
    parse_callback_url,
    wait_for_callback,
)


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def make_oauth(handler, **kwargs):
    return XOAuth2PKCE(
        "desktop-client-id",
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
        **kwargs,
    )


def test_authorization_request_uses_x_pkce_s256_and_required_scopes():
    oauth = make_oauth(lambda request: httpx.Response(500))

    request = oauth.create_authorization_request()
    parsed = urlparse(request.authorization_url)
    query = parse_qs(parsed.query)
    expected_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(request.code_verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZATION_URL
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["desktop-client-id"]
    assert query["redirect_uri"] == [DEFAULT_REDIRECT_URI]
    assert query["scope"] == [" ".join(DEFAULT_SCOPES)]
    assert query["state"] == [request.state]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [expected_challenge]
    assert 43 <= len(request.code_verifier) <= 128
    assert len(request.state) >= 32
    assert request.state != oauth.create_authorization_request().state


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://127.0.0.1:8765/callback",
        "http://localhost:8765/callback",
        "http://example.com:8765/callback",
        "http://127.0.0.1/callback",
    ],
)
def test_only_explicit_ipv4_loopback_redirects_are_accepted(redirect_uri):
    with pytest.raises(ValueError, match="loopback"):
        XOAuth2PKCE("client", redirect_uri=redirect_uri)


def test_callback_returns_code_only_for_matching_state():
    assert (
        parse_callback_url(
            "http://127.0.0.1:8765/callback?code=good-code&state=safe-state",
            "safe-state",
        )
        == "good-code"
    )

    with pytest.raises(OAuthError, match="did not match") as exc_info:
        parse_callback_url(
            "http://127.0.0.1:8765/callback?code=bad-code&state=other",
            "safe-state",
        )

    assert exc_info.value.error == "state_mismatch"


def test_callback_surfaces_denial_and_missing_code_clearly():
    with pytest.raises(OAuthError, match="User denied access") as denied:
        parse_callback_url(
            "http://127.0.0.1:8765/callback"
            "?error=access_denied&error_description=User+denied+access&state=expected",
            "expected",
        )
    assert denied.value.error == "access_denied"

    with pytest.raises(OAuthError, match="authorization code"):
        parse_callback_url(
            "http://127.0.0.1:8765/callback?state=expected",
            "expected",
        )


def test_callback_server_timeout_is_clear_and_always_closes(monkeypatch):
    server_instances = []
    monotonic_values = iter((10.0, 10.0, 10.2))

    class FakeServer:
        timeout = None

        def __init__(self, address, handler):
            self.address = address
            self.handler = handler
            self.closed = False
            server_instances.append(self)

        def handle_request(self):
            return None

        def server_close(self):
            self.closed = True

    monkeypatch.setattr(oauth_module, "_LoopbackHTTPServer", FakeServer)
    monkeypatch.setattr(oauth_module.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(OAuthError, match="Timed out") as exc_info:
        wait_for_callback(
            DEFAULT_REDIRECT_URI,
            "expected-state",
            timeout=0.1,
        )

    assert exc_info.value.error == "callback_timeout"
    assert server_instances[0].address == ("127.0.0.1", 8765)
    assert server_instances[0].closed is True


def test_code_exchange_is_public_client_request_and_parses_token():
    def handler(request):
        assert str(request.url) == TOKEN_URL
        assert request.method == "POST"
        assert "authorization" not in request.headers
        form = parse_qs(request.content.decode())
        assert form == {
            "grant_type": ["authorization_code"],
            "client_id": ["desktop-client-id"],
            "code": ["auth-code"],
            "redirect_uri": [DEFAULT_REDIRECT_URI],
            "code_verifier": ["pkce-verifier"],
        }
        return httpx.Response(
            200,
            json={
                "token_type": "bearer",
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 7200,
                "scope": "tweet.read users.read follows.write offline.access",
            },
        )

    token = make_oauth(handler).exchange_code("auth-code", "pkce-verifier")

    assert token == OAuthToken(
        access_token="access",
        refresh_token="refresh",
        expires_at=NOW + timedelta(hours=2),
        scope=("tweet.read", "users.read", "follows.write", "offline.access"),
    )


def test_code_exchange_uses_requested_scopes_when_response_omits_scope():
    oauth = make_oauth(
        lambda request: httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 7200,
            },
        )
    )

    token = oauth.exchange_code("auth-code", "pkce-verifier")

    assert token.scope == DEFAULT_SCOPES


def test_refresh_uses_client_id_and_keeps_rotating_or_existing_refresh_token():
    requests = []

    def handler(request):
        requests.append(parse_qs(request.content.decode()))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                    "scope": "tweet.read users.read",
                },
            )
        return httpx.Response(
            200,
            json={"access_token": "newer-access", "expires_in": 3600},
        )

    oauth = make_oauth(handler)
    rotated = oauth.refresh(
        "old-refresh",
        scopes=("tweet.read", "users.read"),
    )
    preserved = oauth.refresh("rotated-refresh")

    assert requests[0] == {
        "grant_type": ["refresh_token"],
        "client_id": ["desktop-client-id"],
        "refresh_token": ["old-refresh"],
        "scope": ["tweet.read users.read"],
    }
    assert rotated.refresh_token == "rotated-refresh"
    assert preserved.refresh_token == "rotated-refresh"
    assert preserved.scope == DEFAULT_SCOPES


def test_token_expiry_check_is_timezone_aware_and_supports_leeway():
    token = OAuthToken(
        access_token="access",
        refresh_token=None,
        expires_at=NOW + timedelta(seconds=60),
        scope=(),
    )

    assert token.is_expired(now=NOW, leeway_seconds=30) is False
    assert token.is_expired(now=NOW, leeway_seconds=60) is True
    assert OAuthToken("access", None, None, ()).is_expired(now=NOW) is False

    with pytest.raises(ValueError, match="negative"):
        token.is_expired(now=NOW, leeway_seconds=-1)


def test_authorize_can_skip_browser_and_use_injected_callback_receiver():
    opened_urls = []
    callback_calls = []

    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 60,
                "scope": " ".join(DEFAULT_SCOPES),
            },
        )

    def receive_callback(redirect_uri, state, timeout):
        callback_calls.append((redirect_uri, state, timeout))
        return "authorization-code"

    token = make_oauth(handler).authorize(
        open_browser=False,
        callback_timeout=12,
        browser_opener=lambda url: opened_urls.append(url) or True,
        callback_receiver=receive_callback,
        authorization_url_handler=opened_urls.append,
    )

    assert token.access_token == "access"
    assert len(opened_urls) == 1
    assert callback_calls[0][0] == DEFAULT_REDIRECT_URI
    assert callback_calls[0][1] in opened_urls[0]
    assert callback_calls[0][2] == 12


def test_authorize_uses_injected_browser_opener_when_enabled():
    opened_urls = []

    def handler(request):
        return httpx.Response(200, json={"access_token": "access"})

    token = make_oauth(handler).authorize(
        browser_opener=lambda url: opened_urls.append(url) or True,
        callback_receiver=lambda redirect_uri, state, timeout: "code",
    )

    assert token.access_token == "access"
    assert len(opened_urls) == 1
    assert opened_urls[0].startswith(AUTHORIZATION_URL)


def test_token_endpoint_errors_and_transport_errors_become_oauth_errors():
    def rejected(request):
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Authorization code expired.",
            },
        )

    with pytest.raises(OAuthError, match="Authorization code expired") as rejected_info:
        make_oauth(rejected).exchange_code("code", "verifier")
    assert rejected_info.value.status_code == 400
    assert rejected_info.value.error == "invalid_grant"

    def disconnected(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(OAuthError, match="connection refused"):
        make_oauth(disconnected).refresh("refresh")


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "access token"),
        ({"access_token": "ok", "expires_in": "soon"}, "token expiry"),
        ({"access_token": "ok", "scope": ["tweet.read"]}, "OAuth scope"),
    ],
)
def test_invalid_token_responses_are_rejected(payload, message):
    oauth = make_oauth(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(OAuthError, match=message):
        oauth.exchange_code("code", "verifier")
