from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from x_unfollow.models import DecisionRecord, SafetyConfig
from x_unfollow.x_api import RateLimitError, XApiError


class UnfollowApi(Protocol):
    def unfollow(self, source_user_id: str, target_user_id: str) -> bool:
        ...


@dataclass(frozen=True)
class UnfollowResult:
    attempted_count: int
    success_count: int
    failed_count: int
    dry_run: bool
    successful_user_ids: tuple[str, ...] = ()
    failures: tuple[tuple[str, str], ...] = ()


def execute_unfollows(
    api: UnfollowApi,
    source_user_id: str,
    records: list[DecisionRecord],
    safety: SafetyConfig,
    dry_run: bool,
) -> UnfollowResult:
    targets = eligible_targets(records, safety)[: safety.max_unfollows_per_run]

    if dry_run:
        return UnfollowResult(
            attempted_count=len(targets),
            success_count=0,
            failed_count=0,
            dry_run=True,
        )

    successful_user_ids: list[str] = []
    failures: list[tuple[str, str]] = []
    attempted_count = 0
    for record in targets:
        attempted_count += 1
        try:
            succeeded = api.unfollow(source_user_id, record.user.id)
        except XApiError as exc:
            failures.append((record.user.id, str(exc)))
            if isinstance(exc, RateLimitError):
                break
            continue
        if succeeded:
            successful_user_ids.append(record.user.id)
        else:
            failures.append(
                (record.user.id, "X API did not confirm that the account was unfollowed.")
            )

    return UnfollowResult(
        attempted_count=attempted_count,
        success_count=len(successful_user_ids),
        failed_count=len(failures),
        dry_run=False,
        successful_user_ids=tuple(successful_user_ids),
        failures=tuple(failures),
    )


def has_activity_evidence(record: DecisionRecord) -> bool:
    return (
        record.last_activity_at is not None
        and record.last_activity_id is not None
    )


def has_fresh_evidence(
    record: DecisionRecord,
    safety: SafetyConfig,
    now: datetime | None = None,
) -> bool:
    if not has_activity_evidence(record) or record.scanned_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    age = now - record.scanned_at
    return timedelta(0) <= age <= timedelta(hours=safety.max_evidence_age_hours)


def eligible_targets(
    records: list[DecisionRecord],
    safety: SafetyConfig,
    now: datetime | None = None,
) -> list[DecisionRecord]:
    return [
        record
        for record in records
        if record.review == "unfollow"
        and record.decision == "candidate"
        and record.account_status == "ok"
        and has_fresh_evidence(record, safety, now)
    ]
