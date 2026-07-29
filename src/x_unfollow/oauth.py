from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


AUTHORIZATION_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
DEFAULT_SCOPES = (
    "tweet.read",
    "users.read",
    "follows.read",
    "follows.write",
    "offline.access",
)


class OAuthError(RuntimeError):
    """Raised when the OAuth flow cannot be completed safely."""

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error = error
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    authorization_url: str
    state: str
    code_verifier: str


@dataclass(frozen=True, slots=True)
class OAuthToken:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scope: tuple[str, ...]

    def is_expired(
        self,
        *,
        now: datetime | None = None,
        leeway_seconds: int = 30,
    ) -> bool:
        if self.expires_at is None:
            return False
        if leeway_seconds < 0:
            raise ValueError("leeway_seconds must not be negative")
        current = _as_utc(now or datetime.now(timezone.utc))
        return current + timedelta(seconds=leeway_seconds) >= _as_utc(self.expires_at)


CallbackReceiver = Callable[[str, str, float], str]
BrowserOpener = Callable[[str], bool]


class _LoopbackHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class XOAuth2PKCE:
    """OAuth 2.0 Authorization Code client for public desktop applications."""

    def __init__(
        self,
        client_id: str,
        *,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        scopes: Iterable[str] = DEFAULT_SCOPES,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not client_id.strip():
            raise ValueError("client_id must not be empty")
        _validate_loopback_redirect(redirect_uri)

        normalized_scopes = tuple(dict.fromkeys(scopes))
        if not normalized_scopes:
            raise ValueError("at least one OAuth scope is required")

        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scopes = normalized_scopes
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._owns_client = client is None
        self._client = client or httpx.Client(transport=transport, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> XOAuth2PKCE:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def create_authorization_request(self) -> AuthorizationRequest:
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _code_challenge(code_verifier)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return AuthorizationRequest(
            authorization_url=f"{AUTHORIZATION_URL}?{query}",
            state=state,
            code_verifier=code_verifier,
        )

    def authorize(
        self,
        *,
        open_browser: bool = True,
        callback_timeout: float = 180.0,
        browser_opener: BrowserOpener = webbrowser.open,
        callback_receiver: CallbackReceiver | None = None,
        authorization_url_handler: Callable[[str], None] | None = None,
    ) -> OAuthToken:
        request = self.create_authorization_request()

        def present_authorization_url() -> None:
            if authorization_url_handler is not None:
                authorization_url_handler(request.authorization_url)
            if open_browser and not browser_opener(request.authorization_url):
                raise OAuthError(
                    "The browser could not be opened. Open the authorization URL manually."
                )

        if callback_receiver is None:
            code = wait_for_callback(
                self.redirect_uri,
                request.state,
                timeout=callback_timeout,
                on_listening=present_authorization_url,
            )
        else:
            present_authorization_url()
            code = callback_receiver(
                self.redirect_uri,
                request.state,
                callback_timeout,
            )

        return self.exchange_code(code, request.code_verifier)

    def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        if not code:
            raise OAuthError("The authorization code is missing.")
        if not code_verifier:
            raise OAuthError("The PKCE code verifier is missing.")
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            },
            fallback_scopes=self.scopes,
        )

    def refresh(
        self,
        refresh_token: str,
        *,
        scopes: Iterable[str] | None = None,
    ) -> OAuthToken:
        if not refresh_token:
            raise OAuthError("The refresh token is missing.")
        requested_scopes = tuple(dict.fromkeys(scopes or ()))
        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": refresh_token,
        }
        if requested_scopes:
            data["scope"] = " ".join(requested_scopes)
        return self._token_request(
            data,
            fallback_refresh_token=refresh_token,
            fallback_scopes=requested_scopes or self.scopes,
        )

    def _token_request(
        self,
        data: dict[str, str],
        *,
        fallback_refresh_token: str | None = None,
        fallback_scopes: tuple[str, ...] = (),
    ) -> OAuthToken:
        try:
            response = self._client.post(
                TOKEN_URL,
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"OAuth token request failed: {exc}") from exc

        if response.status_code >= 400:
            raise _oauth_error_from_response(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthError("X returned an invalid JSON token response.") from exc
        if not isinstance(payload, dict):
            raise OAuthError("X returned an invalid token response.")

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("X token response did not include an access token.")

        refresh_token = payload.get("refresh_token", fallback_refresh_token)
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise OAuthError("X returned an invalid refresh token.")

        expires_at = _expires_at(payload.get("expires_in"), self._now())
        scope = _parse_scope(payload.get("scope")) or fallback_scopes
        return OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope,
        )


def parse_callback_url(callback_url: str, expected_state: str) -> str:
    """Validate an OAuth callback URL and return its authorization code."""
    parsed = urlparse(callback_url)
    values = parse_qs(parsed.query, keep_blank_values=True)
    returned_state = _one_query_value(values, "state")
    if returned_state is None or not hmac.compare_digest(returned_state, expected_state):
        raise OAuthError(
            "OAuth callback state did not match. Authorization was cancelled for safety.",
            error="state_mismatch",
        )

    error = _one_query_value(values, "error")
    if error:
        description = _one_query_value(values, "error_description")
        detail = description or "X rejected the authorization request."
        raise OAuthError(f"OAuth authorization failed: {detail}", error=error)

    code = _one_query_value(values, "code")
    if not code:
        raise OAuthError("OAuth callback did not include an authorization code.")
    return code


def wait_for_callback(
    redirect_uri: str,
    expected_state: str,
    *,
    timeout: float = 180.0,
    on_listening: Callable[[], None] | None = None,
) -> str:
    """Wait for one validated OAuth redirect on the configured loopback address."""
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    parsed = _validate_loopback_redirect(redirect_uri)
    result: dict[str, str | OAuthError] = {}
    callback_path = parsed.path or "/"

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            request_url = urlparse(self.path)
            if request_url.path != callback_path:
                self._respond(404, "Unknown OAuth callback path.")
                return
            try:
                code = parse_callback_url(self.path, expected_state)
            except OAuthError as exc:
                result["error"] = exc
                self._respond(400, f"Authorization failed: {exc}")
                return
            result["code"] = code
            self._respond(200, "Authorization complete. You can close this window.")

        def _respond(self, status: int, message: str) -> None:
            body = (
                "<!doctype html><html><body>"
                f"<p>{html.escape(message)}</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    try:
        server = _LoopbackHTTPServer((parsed.hostname, parsed.port), CallbackHandler)
    except OSError as exc:
        raise OAuthError(
            f"Could not start the local OAuth callback server on "
            f"{parsed.hostname}:{parsed.port}: {exc}"
        ) from exc

    try:
        deadline = time.monotonic() + timeout
        server.timeout = min(0.5, timeout)
        if on_listening is not None:
            on_listening()
        while not result:
            if time.monotonic() >= deadline:
                raise OAuthError(
                    "Timed out waiting for the OAuth callback. Please try again.",
                    error="callback_timeout",
                )
            server.handle_request()
    finally:
        server.server_close()

    error = result.get("error")
    if isinstance(error, OAuthError):
        raise error
    code = result.get("code")
    if not isinstance(code, str):
        raise OAuthError("OAuth callback completed without an authorization code.")
    return code


def _validate_loopback_redirect(redirect_uri: str):
    parsed = urlparse(redirect_uri)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "redirect_uri must be an HTTP loopback URL using 127.0.0.1 and a port"
        )
    return parsed


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _one_query_value(values: dict[str, list[str]], key: str) -> str | None:
    candidates = values.get(key)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise OAuthError(f"OAuth callback included duplicate '{key}' values.")
    return candidates[0]


def _parse_scope(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, str):
        raise OAuthError("X returned an invalid OAuth scope.")
    return tuple(dict.fromkeys(value.split()))


def _expires_at(value: Any, now: datetime) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OAuthError("X returned an invalid token expiry.")
    if value < 0:
        raise OAuthError("X returned an invalid token expiry.")
    return _as_utc(now) + timedelta(seconds=float(value))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _oauth_error_from_response(response: httpx.Response) -> OAuthError:
    error: str | None = None
    description: str | None = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        raw_error = payload.get("error")
        raw_description = payload.get("error_description")
        if isinstance(raw_error, str):
            error = raw_error
        if isinstance(raw_description, str):
            description = raw_description
    detail = description or error or response.text.strip() or "Unknown OAuth error"
    return OAuthError(
        f"X OAuth token request returned {response.status_code}: {detail}",
        error=error,
        status_code=response.status_code,
    )
