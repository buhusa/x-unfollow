from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from x_unfollow.models import AppConfig, DecisionRecord, ScanCursor, XUser


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, default=_encode, indent=2),
        encoding="utf-8",
    )
    temporary_path.chmod(0o600)
    temporary_path.replace(path)


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.chmod(0o600)
    temporary_path.replace(path)


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
    def scan_results_export_path(self) -> Path:
        return self.exports_dir / "scan_results.csv"

    @property
    def scan_history_export_path(self) -> Path:
        return self.exports_dir / "scan_history.csv"

    @property
    def unfollow_audit_path(self) -> Path:
        return self.data_dir / "unfollow_audit.jsonl"

    @property
    def scan_context_path(self) -> Path:
        return self.data_dir / "scan_context.json"

    @property
    def scan_cursor_path(self) -> Path:
        return self.data_dir / "scan_cursor.json"

    @property
    def connection_context_path(self) -> Path:
        return self.data_dir / "connection_context.json"

    def save_decisions(self, records: list[DecisionRecord]) -> None:
        payload = [asdict(record) for record in records]
        _write_json(self.decisions_path, payload)

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
                    decision=item["decision"],
                    reason=item["reason"],
                    last_activity_at=_parse_dt(item.get("last_activity_at")),
                    days_since_activity=item.get("days_since_activity"),
                    last_activity_id=item.get("last_activity_id"),
                    review=item.get("review", "pending"),
                    account_status=item.get("account_status", "ok"),
                    scanned_at=_parse_dt(item.get("scanned_at")),
                    scan_run_id=item.get("scan_run_id"),
                    scan_cycle_id=item.get("scan_cycle_id"),
                    scan_batch_number=item.get("scan_batch_number"),
                    scan_position=item.get("scan_position"),
                )
            )
        return records

    def save_scan_cursor(self, cursor: ScanCursor) -> None:
        _write_json(self.scan_cursor_path, asdict(cursor))

    def load_scan_cursor(self) -> ScanCursor | None:
        if not self.scan_cursor_path.exists():
            return None
        try:
            payload = json.loads(self.scan_cursor_path.read_text(encoding="utf-8"))
            started_at = _parse_dt(payload["started_at"])
            updated_at = _parse_dt(payload["updated_at"])
            if started_at is None or updated_at is None:
                return None
            return ScanCursor(
                source_user_id=str(payload["source_user_id"]),
                source_username=str(payload["source_username"]),
                cycle_id=str(payload["cycle_id"]),
                started_at=started_at,
                updated_at=updated_at,
                scanned_count=int(payload["scanned_count"]),
                batch_number=int(payload["batch_number"]),
                next_token=payload.get("next_token"),
                complete=bool(payload.get("complete", False)),
                config_signature=str(payload.get("config_signature", "")),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return None

    def clear_scan_cycle(self) -> None:
        for path in (self.scan_cursor_path, self.decisions_path):
            path.unlink(missing_ok=True)

    def save_scan_context(self, user_id: str, username: str) -> None:
        _write_json(
            self.scan_context_path,
            {"source_user_id": user_id, "source_username": username},
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

    def save_connection_context(
        self,
        user_id: str,
        username: str,
        *,
        following_count: int | None = None,
        following_refreshed_at: datetime | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "source_user_id": user_id,
            "source_username": username,
        }
        if following_count is not None:
            payload["following_count"] = following_count
            payload["following_refreshed_at"] = (
                following_refreshed_at or datetime.now(timezone.utc)
            )
        _write_json(
            self.connection_context_path,
            payload,
        )

    def load_connection_context(self) -> dict[str, Any] | None:
        if not self.connection_context_path.exists():
            return None
        try:
            payload = json.loads(
                self.connection_context_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        user_id = str(payload.get("source_user_id", "")).strip()
        username = str(payload.get("source_username", "")).strip()
        if not user_id or not username:
            return None
        context: dict[str, Any] = {
            "source_user_id": user_id,
            "source_username": username,
        }
        following_count = payload.get("following_count")
        if isinstance(following_count, int) and not isinstance(following_count, bool):
            context["following_count"] = following_count
            refreshed_at = payload.get("following_refreshed_at")
            if isinstance(refreshed_at, str) and refreshed_at:
                context["following_refreshed_at"] = refreshed_at
        return context

    def append_unfollow_audit(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.unfollow_audit_path.open("a", encoding="utf-8") as file:
            for entry in entries:
                file.write(json.dumps(entry, default=_encode))
                file.write("\n")

    def export_candidates_csv(self, records: list[DecisionRecord]) -> Path:
        headers = _scan_export_headers(include_rules=False)
        rows = [
            _scan_export_row(record)
            for record in records
            if record.decision == "candidate"
        ]
        _write_csv(self.candidates_export_path, headers, rows)
        return self.candidates_export_path

    def export_scan_results(self, records: list[DecisionRecord], config: AppConfig) -> Path:
        headers = _scan_export_headers(include_rules=True)
        rows = [_scan_export_row(record, config) for record in records]
        _write_csv(self.scan_results_export_path, headers, rows)
        return self.scan_results_export_path

    def append_scan_history(
        self,
        records: list[DecisionRecord],
        config: AppConfig,
    ) -> Path:
        headers = _scan_export_headers(include_rules=True)
        existing_rows: list[dict[str, Any]] = []
        if self.scan_history_export_path.exists():
            with self.scan_history_export_path.open(
                encoding="utf-8", newline=""
            ) as file:
                existing_rows = list(csv.DictReader(file))
        existing_keys = {
            (row.get("scan_cycle_id"), row.get("scan_position"), row.get("id"))
            for row in existing_rows
        }
        new_rows = []
        for record in records:
            row = _scan_export_row(record, config)
            key = (
                str(row.get("scan_cycle_id") or ""),
                str(row.get("scan_position") or ""),
                str(row.get("id") or ""),
            )
            if key not in existing_keys:
                new_rows.append(row)
                existing_keys.add(key)
        _write_csv(
            self.scan_history_export_path,
            headers,
            [
                *(
                    {header: row.get(header, "") for header in headers}
                    for row in existing_rows
                ),
                *new_rows,
            ],
        )
        return self.scan_history_export_path


def _scan_export_headers(*, include_rules: bool) -> list[str]:
    headers = [
        "scanned_at",
        "scan_run_id",
        "scan_cycle_id",
        "scan_batch_number",
        "scan_position",
        "id",
        "username",
        "name",
        "profile_url",
        "decision",
        "account_status",
        "reason",
        "last_activity_at",
        "days_since_activity",
        "last_activity_id",
        "last_activity_url",
        "protected",
        "verified",
    ]
    if include_rules:
        headers.extend(
            [
                "activity_threshold_days",
            ]
        )
    else:
        headers.insert(11, "review")
    return headers


def _post_url(username: str, post_id: str | None) -> str:
    if not post_id:
        return ""
    return f"https://x.com/{username}/status/{post_id}"


def _csv_safe(value: str) -> str:
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _scan_export_row(
    record: DecisionRecord,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "scanned_at": _encode(record.scanned_at),
        "scan_run_id": record.scan_run_id,
        "scan_cycle_id": record.scan_cycle_id,
        "scan_batch_number": record.scan_batch_number,
        "scan_position": record.scan_position,
        "id": record.user.id,
        "username": _csv_safe(record.user.username),
        "name": _csv_safe(record.user.name),
        "profile_url": f"https://x.com/{record.user.username}",
        "decision": record.decision,
        "account_status": record.account_status,
        "review": record.review,
        "reason": _csv_safe(record.reason),
        "last_activity_at": _encode(record.last_activity_at),
        "days_since_activity": record.days_since_activity,
        "last_activity_id": record.last_activity_id,
        "last_activity_url": _post_url(
            record.user.username, record.last_activity_id
        ),
        "protected": record.user.protected,
        "verified": record.user.verified,
    }
    if config is not None:
        row.pop("review")
        row.update(
            {
                "activity_threshold_days": config.rules.activity_threshold_days,
            }
        )
    return row
