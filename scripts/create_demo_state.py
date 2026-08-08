from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from x_unfollow.config import write_default_config
from x_unfollow.oauth import DEFAULT_SCOPES, OAuthToken
from x_unfollow.storage import Storage
from x_unfollow.tokens import OAuthCredentials, TokenStore


def main() -> None:
    target = Path("/tmp/x-unfollow-demo")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, mode=0o700)

    write_default_config(target / "config.toml")
    TokenStore(target).save_oauth_credentials(
        OAuthCredentials(
            client_id="demo",
            token=OAuthToken(
                access_token="demo",
                refresh_token="demo",
                expires_at=None,
                scope=DEFAULT_SCOPES,
            ),
            verified=True,
        )
    )

    storage = Storage(target)
    storage.save_connection_context(
        "demo-user",
        "demo_operator",
        following_count=606,
        following_refreshed_at=datetime.now(timezone.utc),
    )


if __name__ == "__main__":
    main()
