from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RuleConfig:
    activity_threshold_days: int = 180

    def __post_init__(self) -> None:
        if self.activity_threshold_days <= 0:
            raise ValueError("activity_threshold_days must be greater than 0")


@dataclass(frozen=True)
class SafetyConfig:
    require_review_before_unfollow: bool = True
    max_unfollows_per_run: int = 50
    max_evidence_age_hours: int = 24

    def __post_init__(self) -> None:
        if self.max_unfollows_per_run <= 0:
            raise ValueError("max_unfollows_per_run must be greater than 0")
        if self.max_evidence_age_hours <= 0:
            raise ValueError("max_evidence_age_hours must be greater than 0")


@dataclass(frozen=True)
class ApiConfig:
    page_size_following: int = 1000
    max_accounts_per_scan: int = 10
    max_scan_cost_usd: float = 0.50

    def __post_init__(self) -> None:
        if self.max_accounts_per_scan <= 0:
            raise ValueError("max_accounts_per_scan must be greater than 0")
        if not 1 <= self.page_size_following <= 1000:
            raise ValueError("page_size_following must be between 1 and 1000")
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
class DecisionRecord:
    user: XUser
    decision: str
    reason: str
    last_activity_at: datetime | None = None
    days_since_activity: int | None = None
    last_activity_id: str | None = None
    review: str = "pending"
    account_status: str = "ok"
    scanned_at: datetime | None = None
    scan_run_id: str | None = None
    scan_cycle_id: str | None = None
    scan_batch_number: int | None = None
    scan_position: int | None = None


@dataclass(frozen=True)
class ScanBatch:
    records: list[DecisionRecord]
    next_token: str | None


@dataclass(frozen=True)
class ScanCursor:
    source_user_id: str
    source_username: str
    cycle_id: str
    started_at: datetime
    updated_at: datetime
    scanned_count: int
    batch_number: int
    next_token: str | None
    complete: bool = False
    config_signature: str = ""
