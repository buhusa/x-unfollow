from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from x_unfollow.config import write_default_config
from x_unfollow.models import DecisionRecord, XUser
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

    own_post_at = datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc)
    reply_at = datetime(2025, 2, 3, 18, 45, tzinfo=timezone.utc)
    candidate = DecisionRecord(
        user=XUser(
            id="demo-quiet-signal",
            username="quiet_signal",
            name="Quiet Signal",
            most_recent_tweet_id="demo-post-1",
        ),
        last_own_post_at=own_post_at,
        days_since_own_post=560,
        last_reply_at=reply_at,
        days_since_reply=541,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision="candidate",
        reason="no own post >= 180d AND no reply >= 180d",
    )

    storage = Storage(target)
    storage.save_decisions([candidate])
    storage.save_scan_context("demo-user", "demo_operator")


if __name__ == "__main__":
    main()
