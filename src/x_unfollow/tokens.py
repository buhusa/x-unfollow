from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from x_unfollow.oauth import OAuthToken


class MissingTokenError(RuntimeError):
    """Raised when no usable user access token is stored locally."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Missing X user login. Run `x-unfollow setup`.")


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    client_id: str
    token: OAuthToken
    verified: bool = False


class TokenStore:
    def __init__(self, app_dir: Path) -> None:
        self.app_dir = app_dir

    @property
    def path(self) -> Path:
        return self.app_dir / "tokens.json"

    def save_bearer_token(self, token: str) -> None:
        cleaned = token.strip()
        self._write_payload({"bearer_token": cleaned})

    def save_oauth_credentials(self, credentials: OAuthCredentials) -> None:
        token = credentials.token
        self._write_payload(
            {
                "client_id": credentials.client_id,
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": (
                    token.expires_at.isoformat() if token.expires_at is not None else None
                ),
                "scope": list(token.scope),
                "verified": credentials.verified,
            }
        )

    def _write_payload(self, payload: dict) -> None:
        self.app_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            dir=self.app_dir,
            prefix=".tokens-",
            suffix=".tmp",
            text=True,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            os.replace(temporary_path, self.path)
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    def load_oauth_credentials(self) -> OAuthCredentials:
        payload = self._load_payload()
        client_id = str(payload.get("client_id", "")).strip()
        access_token = str(payload.get("access_token", "")).strip()
        if not client_id or not access_token:
            raise MissingTokenError()

        refresh_value = payload.get("refresh_token")
        refresh_token = (
            str(refresh_value).strip() if refresh_value is not None else None
        )
        if refresh_token == "":
            refresh_token = None

        expires_value = payload.get("expires_at")
        try:
            expires_at = (
                datetime.fromisoformat(str(expires_value))
                if expires_value is not None
                else None
            )
        except ValueError as exc:
            raise MissingTokenError("Stored X login is invalid. Run `x-unfollow setup`.") from exc

        scope_value = payload.get("scope", [])
        if not isinstance(scope_value, list) or not all(
            isinstance(item, str) for item in scope_value
        ):
            raise MissingTokenError("Stored X login is invalid. Run `x-unfollow setup`.")
        verified_value = payload.get("verified", True)
        if not isinstance(verified_value, bool):
            raise MissingTokenError("Stored X login is invalid. Run `x-unfollow setup`.")

        return OAuthCredentials(
            client_id=client_id,
            token=OAuthToken(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scope=tuple(scope_value),
            ),
            verified=verified_value,
        )

    def load_bearer_token(self) -> str:
        payload = self._load_payload()

        token = str(payload.get("bearer_token", "")).strip()
        if not token:
            raise MissingTokenError()
        return token

    def _load_payload(self) -> dict:
        if not self.path.exists():
            raise MissingTokenError()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise MissingTokenError() from exc
        if not isinstance(payload, dict):
            raise MissingTokenError()
        return payload
