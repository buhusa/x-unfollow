from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import x_unfollow.cli as cli
from create_demo_state import main as create_demo_state
from x_unfollow.models import XUser
from x_unfollow.scanner import X_SNOWFLAKE_EPOCH_MS


def _snowflake(at: datetime) -> str:
    timestamp_ms = int(at.timestamp() * 1000)
    return str((timestamp_ms - X_SNOWFLAKE_EPOCH_MS) << 22)


class DemoXApiClient:
    """Local API stand-in used only to record the documentation GIF."""

    def __init__(self, _token: str) -> None:
        now = datetime.now(timezone.utc)
        self.following = [
            XUser(
                id="demo-quiet-signal",
                username="quiet_signal",
                name="Quiet Signal",
                most_recent_tweet_id=_snowflake(now - timedelta(days=541)),
            ),
            XUser(
                id="demo-daily-builder",
                username="daily_builder",
                name="Daily Builder",
                most_recent_tweet_id=_snowflake(now - timedelta(days=2)),
            ),
            XUser(
                id="demo-weekly-notes",
                username="weekly_notes",
                name="Weekly Notes",
                most_recent_tweet_id=_snowflake(now - timedelta(days=8)),
            ),
        ]

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def get_me(self) -> XUser:
        return XUser(
            id="demo-user",
            username="demo_operator",
            name="Demo Operator",
            public_metrics={"following_count": 606},
        )

    def get_following_batch(
        self,
        _user_id: str,
        page_size: int,
        limit: int | None = None,
        pagination_token: str | None = None,
    ) -> tuple[list[XUser], None]:
        del page_size, pagination_token
        return self.following[:limit], None

    def unfollow(self, _source_user_id: str, _target_user_id: str) -> bool:
        return True


def main() -> None:
    create_demo_state()
    real_scan = cli.scan_account_batch

    def staged_scan(*args, progress=None, **kwargs):
        def slower_progress(stage, current, total, user):
            if progress is not None:
                progress(stage, current, total, user)
            time.sleep(0.8 if stage == "account" else 0.5)

        return real_scan(*args, progress=slower_progress, **kwargs)

    cli.XApiClient = DemoXApiClient
    cli.scan_account_batch = staged_scan
    cli.main()


if __name__ == "__main__":
    main()
