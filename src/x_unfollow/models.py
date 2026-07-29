from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class CombinationMode(StrEnum):
    AND = "and"
    OR = "or"


@dataclass(frozen=True)
class RuleConfig:
    own_post_threshold_days: int = 180
    reply_threshold_days: int = 180
    combination: CombinationMode = CombinationMode.AND
    count_retweets_as_activity: bool = False
    count_quote_posts_as_own_posts: bool = True


@dataclass(frozen=True)
class SafetyConfig:
    require_review_before_unfollow: bool = True
    max_unfollows_per_run: int = 50


@dataclass(frozen=True)
class ApiConfig:
    page_size_following: int = 1000
    page_size_tweets: int = 5
    max_tweet_pages_per_user: int = 1
    max_accounts_per_scan: int = 10
    max_scan_cost_usd: float = 0.50

    def __post_init__(self) -> None:
        for field_name in ("max_tweet_pages_per_user", "max_accounts_per_scan"):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be greater than 0")
        if not 1 <= self.page_size_following <= 1000:
            raise ValueError("page_size_following must be between 1 and 1000")
        if not 5 <= self.page_size_tweets <= 100:
            raise ValueError("page_size_tweets must be between 5 and 100")
        if self.max_scan_cost_usd <= 0:
            raise ValueError("max_scan_cost_usd must be greater than 0")


@dataclass(frozen=True)
class AppConfig:
    rules: RuleConfig = field(default_factory=RuleConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    api: ApiConfig = field(default_factory=ApiConfig)


@dataclass(frozen=True)
class XUser:
    id: str
    username: str
    name: str
    most_recent_tweet_id: str | None = None
    protected: bool = False
    verified: bool = False
    public_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class XPost:
    id: str
    author_id: str
    created_at: datetime
    referenced_tweets: list[dict[str, str]] = field(default_factory=list)
    in_reply_to_user_id: str | None = None
    text: str = ""


@dataclass(frozen=True)
class ActivitySummary:
    last_own_post_at: datetime | None
    last_reply_at: datetime | None


@dataclass(frozen=True)
class DecisionRecord:
    user: XUser
    last_own_post_at: datetime | None
    days_since_own_post: int | None
    last_reply_at: datetime | None
    days_since_reply: int | None
    rule_match_own_post: bool
    rule_match_reply: bool
    decision: str
    reason: str
    review: str = "pending"
    account_status: str = "ok"
