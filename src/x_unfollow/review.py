from __future__ import annotations

from dataclasses import replace

from x_unfollow.models import DecisionRecord


CHOICES = {
    "u": "unfollow",
    "k": "keep",
    "s": "skip",
}


def apply_review_choice(record: DecisionRecord, choice: str) -> DecisionRecord:
    normalized = choice.strip().lower()
    if normalized not in CHOICES:
        raise ValueError(f"Unknown review choice: {choice}")

    review = CHOICES[normalized]
    if (
        review == "unfollow"
        and (record.decision != "candidate" or record.account_status != "ok")
    ):
        raise ValueError(
            "Only candidate records with ok account status can be marked for unfollow."
        )

    return replace(record, review=review)
