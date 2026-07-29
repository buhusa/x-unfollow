from dataclasses import replace

import pytest

from x_unfollow.models import DecisionRecord, SafetyConfig, XUser
from x_unfollow.review import apply_review_choice
from x_unfollow.unfollow import execute_unfollows
from x_unfollow.x_api import XApiError


def record(
    username: str = "quiet",
    *,
    user_id: str | None = None,
    decision: str = "candidate",
    account_status: str = "ok",
) -> DecisionRecord:
    return DecisionRecord(
        user=XUser(id=user_id or username, username=username, name=username.title()),
        last_own_post_at=None,
        days_since_own_post=None,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision=decision,
        reason="test",
        account_status=account_status,
    )


class FakeUnfollowApi:
    def __init__(self, results: dict[str, bool] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, str]] = []

    def unfollow(self, source_user_id: str, target_user_id: str) -> bool:
        self.calls.append((source_user_id, target_user_id))
        return self.results.get(target_user_id, True)


@pytest.mark.parametrize(
    ("choice", "review"),
    [
        ("u", "unfollow"),
        ("k", "keep"),
        ("s", "skip"),
    ],
)
def test_apply_review_choice_marks_review_state_on_a_copy(choice, review):
    original = record()

    updated = apply_review_choice(original, choice)

    assert updated is not original
    assert updated.review == review
    assert original.review == "pending"


def test_apply_review_choice_rejects_invalid_choice():
    with pytest.raises(ValueError):
        apply_review_choice(record(), "x")


@pytest.mark.parametrize(
    "unsafe_record",
    [
        record("active", decision="keep"),
        record("locked", decision="keep", account_status="protected"),
        record("denied", decision="keep", account_status="inaccessible"),
        record("candidate_locked", account_status="protected"),
    ],
)
def test_apply_review_choice_cannot_mark_non_candidate_or_unavailable_for_unfollow(
    unsafe_record,
):
    with pytest.raises(ValueError):
        apply_review_choice(unsafe_record, "u")


def test_execute_unfollows_dry_run_counts_capped_targets_without_api_calls():
    api = FakeUnfollowApi()
    records = [
        apply_review_choice(record("one"), "u"),
        apply_review_choice(record("two"), "u"),
        apply_review_choice(record("three"), "u"),
    ]

    result = execute_unfollows(
        api,
        "me",
        records,
        SafetyConfig(max_unfollows_per_run=2),
        dry_run=True,
    )

    assert result.attempted_count == 2
    assert result.success_count == 0
    assert result.failed_count == 0
    assert result.dry_run is True
    assert api.calls == []


def test_execute_unfollows_calls_only_reviewed_ok_candidates_after_cap():
    api = FakeUnfollowApi()
    records = [
        apply_review_choice(record("one"), "u"),
        apply_review_choice(record("kept"), "k"),
        apply_review_choice(record("skipped"), "s"),
        record("pending"),
        replace(record("active", decision="keep"), review="unfollow"),
        replace(record("locked", account_status="protected"), review="unfollow"),
        apply_review_choice(record("two"), "u"),
        apply_review_choice(record("three"), "u"),
    ]

    result = execute_unfollows(
        api,
        "me",
        records,
        SafetyConfig(max_unfollows_per_run=2),
        dry_run=False,
    )

    assert result.attempted_count == 2
    assert result.success_count == 2
    assert result.failed_count == 0
    assert result.dry_run is False
    assert api.calls == [("me", "one"), ("me", "two")]


def test_execute_unfollows_counts_failed_api_results():
    api = FakeUnfollowApi(results={"fail": False})
    records = [
        apply_review_choice(record("ok"), "u"),
        apply_review_choice(record("fail"), "u"),
    ]

    result = execute_unfollows(
        api,
        "me",
        records,
        SafetyConfig(max_unfollows_per_run=50),
        dry_run=False,
    )

    assert result.attempted_count == 2
    assert result.success_count == 1
    assert result.failed_count == 1
    assert api.calls == [("me", "ok"), ("me", "fail")]
    assert result.successful_user_ids == ("ok",)
    assert result.failures == (
        ("fail", "X API did not confirm that the account was unfollowed."),
    )


def test_execute_unfollows_keeps_partial_success_when_one_api_call_errors():
    class PartiallyFailingApi(FakeUnfollowApi):
        def unfollow(self, source_user_id: str, target_user_id: str) -> bool:
            self.calls.append((source_user_id, target_user_id))
            if target_user_id == "fail":
                raise XApiError("temporary account error", status_code=500)
            return True

    api = PartiallyFailingApi()
    records = [
        apply_review_choice(record("one"), "u"),
        apply_review_choice(record("fail"), "u"),
        apply_review_choice(record("two"), "u"),
    ]

    result = execute_unfollows(
        api,
        "me",
        records,
        SafetyConfig(max_unfollows_per_run=50),
        dry_run=False,
    )

    assert result.attempted_count == 3
    assert result.successful_user_ids == ("one", "two")
    assert result.failures == (("fail", "temporary account error"),)
