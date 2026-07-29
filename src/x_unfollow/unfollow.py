from __future__ import annotations

from dataclasses import dataclass
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
    targets = _eligible_targets(records)[: max(safety.max_unfollows_per_run, 0)]

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


def _eligible_targets(records: list[DecisionRecord]) -> list[DecisionRecord]:
    return [
        record
        for record in records
        if record.review == "unfollow"
        and record.decision == "candidate"
        and record.account_status == "ok"
    ]
