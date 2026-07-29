from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from x_unfollow.models import DecisionRecord, XUser


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class Storage:
    def __init__(self, app_dir: Path) -> None:
        self.app_dir = app_dir
        self.data_dir = app_dir / "data"
        self.exports_dir = app_dir / "exports"

    @property
    def decisions_path(self) -> Path:
        return self.data_dir / "decisions.json"

    @property
    def candidates_export_path(self) -> Path:
        return self.exports_dir / "candidates.csv"

    @property
    def unfollow_audit_path(self) -> Path:
        return self.data_dir / "unfollow_audit.jsonl"

    @property
    def scan_context_path(self) -> Path:
        return self.data_dir / "scan_context.json"

    def save_decisions(self, records: list[DecisionRecord]) -> None:
        payload = [asdict(record) for record in records]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_path.write_text(
            json.dumps(payload, default=_encode, indent=2),
            encoding="utf-8",
        )

    def load_decisions(self) -> list[DecisionRecord]:
        if not self.decisions_path.exists():
            return []

        payload = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        records = []
        for item in payload:
            user_raw = item["user"]
            records.append(
                DecisionRecord(
                    user=XUser(**user_raw),
                    last_own_post_at=_parse_dt(item.get("last_own_post_at")),
                    days_since_own_post=item.get("days_since_own_post"),
                    last_reply_at=_parse_dt(item.get("last_reply_at")),
                    days_since_reply=item.get("days_since_reply"),
                    rule_match_own_post=bool(item["rule_match_own_post"]),
                    rule_match_reply=bool(item["rule_match_reply"]),
                    decision=item["decision"],
                    reason=item["reason"],
                    review=item.get("review", "pending"),
                    account_status=item.get("account_status", "ok"),
                )
            )
        return records

    def save_scan_context(self, user_id: str, username: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.scan_context_path.write_text(
            json.dumps(
                {"source_user_id": user_id, "source_username": username},
                indent=2,
            ),
            encoding="utf-8",
        )

    def load_scan_context(self) -> dict[str, str] | None:
        if not self.scan_context_path.exists():
            return None
        try:
            payload = json.loads(self.scan_context_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        user_id = str(payload.get("source_user_id", "")).strip()
        username = str(payload.get("source_username", "")).strip()
        if not user_id or not username:
            return None
        return {"source_user_id": user_id, "source_username": username}

    def append_unfollow_audit(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.unfollow_audit_path.open("a", encoding="utf-8") as file:
            for entry in entries:
                file.write(json.dumps(entry, default=_encode))
                file.write("\n")

    def export_candidates_csv(self, records: list[DecisionRecord]) -> Path:
        headers = [
            "id",
            "username",
            "name",
            "decision",
            "account_status",
            "review",
            "reason",
            "last_own_post_at",
            "days_since_own_post",
            "last_reply_at",
            "days_since_reply",
        ]
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        with self.candidates_export_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=headers)
            writer.writeheader()
            for record in records:
                if record.decision != "candidate":
                    continue
                writer.writerow(
                    {
                        "id": record.user.id,
                        "username": record.user.username,
                        "name": record.user.name,
                        "decision": record.decision,
                        "account_status": record.account_status,
                        "review": record.review,
                        "reason": record.reason,
                        "last_own_post_at": _encode(record.last_own_post_at),
                        "days_since_own_post": record.days_since_own_post,
                        "last_reply_at": _encode(record.last_reply_at),
                        "days_since_reply": record.days_since_reply,
                    }
                )
        return self.candidates_export_path
