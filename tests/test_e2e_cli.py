from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

import x_unfollow.cli as cli
from x_unfollow.models import DecisionRecord, XUser
from x_unfollow.oauth import DEFAULT_SCOPES, OAuthToken
from x_unfollow.storage import Storage
from x_unfollow.x_api import XApiError


runner = CliRunner()


def record(username: str, *, user_id: str | None = None) -> DecisionRecord:
    return DecisionRecord(
        user=XUser(id=user_id or username, username=username, name=username.title()),
        last_own_post_at=None,
        days_since_own_post=None,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision="candidate",
        reason="no recent activity",
    )


def test_config_init_and_status_flow_still_works(tmp_path):
    app_dir = tmp_path / "state"

    init_result = runner.invoke(cli.app, ["config", "init", "--app-dir", str(app_dir)])
    status_result = runner.invoke(cli.app, ["status", "--app-dir", str(app_dir)])

    assert init_result.exit_code == 0
    assert status_result.exit_code == 0
    assert "Wrote config" in init_result.output
    assert "x-unfollow status" in status_result.output
    assert "Candidate count: 0" in status_result.output
    assert "Traceback" not in status_result.output


def test_setup_without_token_blank_prompt_exits_friendly(tmp_path):
    app_dir = tmp_path / "state"

    result = runner.invoke(cli.app, ["setup", "--app-dir", str(app_dir)], input="\n")

    assert result.exit_code == 1
    assert "No Client ID provided" in result.output
    assert "Traceback" not in result.output
    assert not (app_dir / "tokens.json").exists()


def test_setup_connects_oauth_account_and_stores_refreshable_credentials(
    tmp_path, monkeypatch
):
    app_dir = tmp_path / "state"
    created = []

    class FakeOAuth:
        def __init__(self, client_id):
            self.client_id = client_id
            created.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def authorize(self, **kwargs):
            assert kwargs["open_browser"] is True
            kwargs["authorization_url_handler"]("https://x.com/authorize-test")
            return OAuthToken(
                access_token="access",
                refresh_token="refresh",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
                scope=DEFAULT_SCOPES,
            )

    monkeypatch.setattr(cli, "XOAuth2PKCE", FakeOAuth)

    result = runner.invoke(
        cli.app,
        ["setup", "--app-dir", str(app_dir), "--client-id", "client-id"],
    )

    assert result.exit_code == 0
    assert "X account connected" in result.output
    assert "refresh" not in result.output
    assert created[0].client_id == "client-id"
    saved = cli.TokenStore(app_dir).load_oauth_credentials()
    assert saved.client_id == "client-id"
    assert saved.token.refresh_token == "refresh"


def test_expired_oauth_token_is_refreshed_and_saved(tmp_path, monkeypatch):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_oauth_credentials(
        cli.OAuthCredentials(
            client_id="client-id",
            token=OAuthToken(
                access_token="expired",
                refresh_token="old-refresh",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                scope=DEFAULT_SCOPES,
            ),
        )
    )

    class FakeOAuth:
        def __init__(self, client_id):
            assert client_id == "client-id"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def refresh(self, refresh_token, scopes):
            assert refresh_token == "old-refresh"
            assert scopes == DEFAULT_SCOPES
            return OAuthToken(
                access_token="new-access",
                refresh_token="new-refresh",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
                scope=DEFAULT_SCOPES,
            )

    monkeypatch.setattr(cli, "XOAuth2PKCE", FakeOAuth)

    assert cli._load_access_token(app_dir) == "new-access"
    saved = cli.TokenStore(app_dir).load_oauth_credentials()
    assert saved.token.refresh_token == "new-refresh"


def test_check_verifies_connected_username_with_read_only_request(
    tmp_path, monkeypatch
):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_oauth_credentials(
        cli.OAuthCredentials(
            client_id="client-id",
            token=OAuthToken(
                access_token="test-token",
                refresh_token="refresh",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
                scope=DEFAULT_SCOPES,
            ),
            verified=False,
        )
    )
    clients = []

    class FakeClient:
        def __init__(self, token):
            assert token == "test-token"
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def get_me(self):
            return XUser(id="123", username="buhusa", name="Buhusa")

    monkeypatch.setattr(cli, "XApiClient", FakeClient)

    result = runner.invoke(
        cli.app,
        ["check", "--app-dir", str(app_dir), "--yes"],
    )

    assert result.exit_code == 0
    assert "Connected as @buhusa" in result.output
    assert clients[0].closed is True
    assert cli.TokenStore(app_dir).load_oauth_credentials().verified is True


def test_scan_without_token_exits_1_friendly(tmp_path):
    app_dir = tmp_path / "state"

    result = runner.invoke(cli.app, ["scan", "--app-dir", str(app_dir), "--yes"])

    assert result.exit_code == 1
    assert "Run `x-unfollow setup`" in result.output
    assert "Traceback" not in result.output


def test_scan_with_fake_api_saves_decisions_and_exports_candidates(tmp_path, monkeypatch):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_bearer_token("test-token")
    created_clients = []

    class FakeClient:
        def __init__(self, token):
            self.token = token
            self.closed = False
            created_clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def get_me(self):
            return XUser(id="me", username="me", name="Me")

    def fake_scan_accounts(api, source_user_id, config, limit=None, progress=None):
        assert isinstance(api, FakeClient)
        assert api.token == "test-token"
        assert source_user_id == "me"
        assert limit is None
        assert progress is not None
        progress("following_loaded", 2, 2, None)
        progress("latest_activity", 0, 2, None)
        progress(
            "account",
            1,
            2,
            XUser(id="quiet", username="quiet", name="Quiet"),
        )
        return [record("quiet"), replace(record("active"), decision="keep")]

    monkeypatch.setattr(cli, "XApiClient", FakeClient)
    monkeypatch.setattr(cli, "scan_accounts", fake_scan_accounts)

    result = runner.invoke(cli.app, ["scan", "--app-dir", str(app_dir), "--yes"])

    assert result.exit_code == 0
    assert "Scanned 2 account(s)" in result.output
    assert "Candidates: 1" in result.output
    assert "Loaded 2 followed account(s)" in result.output
    assert "[1/2] Scanning @quiet" in result.output
    assert str(app_dir / "exports" / "candidates.csv") in result.output
    assert len(created_clients) == 1
    assert created_clients[0].closed is True
    records = Storage(app_dir).load_decisions()
    assert [item.user.username for item in records] == ["quiet", "active"]
    assert (app_dir / "exports" / "candidates.csv").exists()


def test_review_marks_first_candidate_unfollow_and_second_keep_using_input(tmp_path):
    app_dir = tmp_path / "state"
    Storage(app_dir).save_decisions([record("quiet"), record("active")])

    result = runner.invoke(cli.app, ["review", "--app-dir", str(app_dir)], input="u\nk\n")

    assert result.exit_code == 0
    assert "@quiet" in result.output
    assert "@active" in result.output
    assert "Marked @quiet for unfollow" in result.output
    assert "Use menu option 3 to preview or option 4 to execute" in result.output
    assert "Nothing has been unfollowed yet" in result.output
    records = Storage(app_dir).load_decisions()
    assert [item.review for item in records] == ["unfollow", "keep"]


def test_review_output_includes_last_own_post_and_reply_days(tmp_path):
    app_dir = tmp_path / "state"
    Storage(app_dir).save_decisions(
        [
            replace(
                record("quiet"),
                last_own_post_at=datetime(
                    2025,
                    12,
                    19,
                    8,
                    30,
                    tzinfo=timezone.utc,
                ),
                days_since_own_post=222,
                last_reply_at=datetime(
                    2026,
                    7,
                    17,
                    9,
                    45,
                    tzinfo=timezone.utc,
                ),
                days_since_reply=12,
            )
        ]
    )

    result = runner.invoke(cli.app, ["review", "--app-dir", str(app_dir)], input="s\n")

    assert result.exit_code == 0
    assert "Last own post: 2025-12-19 08:30 UTC (222 days ago)" in result.output
    assert "Last reply: 2026-07-17 09:45 UTC (12 days ago)" in result.output


def test_unfollow_dry_run_with_reviewed_candidate_does_not_construct_client(
    tmp_path, monkeypatch
):
    app_dir = tmp_path / "state"
    Storage(app_dir).save_decisions([replace(record("quiet"), review="unfollow")])

    def fail_client(_token):
        raise AssertionError("dry-run should not construct XApiClient")

    monkeypatch.setattr(cli, "XApiClient", fail_client)

    result = runner.invoke(cli.app, ["unfollow", "--app-dir", str(app_dir), "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run" in result.output


def test_unfollow_preview_lists_only_targets_inside_per_run_limit(tmp_path):
    app_dir = tmp_path / "state"
    app_dir.mkdir()
    (app_dir / "config.toml").write_text(
        "[safety]\nmax_unfollows_per_run = 1\n",
        encoding="utf-8",
    )
    Storage(app_dir).save_decisions(
        [
            replace(record("first"), review="unfollow"),
            replace(record("second"), review="unfollow"),
        ]
    )

    result = runner.invoke(
        cli.app,
        ["unfollow", "--app-dir", str(app_dir), "--dry-run"],
    )

    assert result.exit_code == 0
    assert "@first" in result.output
    assert "@second" not in result.output
    assert "1 additional marked account(s) are deferred" in result.output


def test_unfollow_yes_with_fake_client_executes_real_path_and_prints_success(
    tmp_path, monkeypatch
):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_bearer_token("test-token")
    Storage(app_dir).save_decisions(
        [replace(record("quiet", user_id="target"), review="unfollow")]
    )
    Storage(app_dir).save_scan_context("me", "me")
    calls = []
    created_clients = []

    class FakeClient:
        def __init__(self, token):
            self.token = token
            self.closed = False
            created_clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def get_me(self):
            return XUser(id="me", username="me", name="Me")

    def fake_execute_unfollows(api, source_user_id, records, safety, dry_run):
        calls.append((api, source_user_id, records, dry_run))

        class Result:
            attempted_count = 1
            success_count = 1
            failed_count = 0
            dry_run = False

        return Result()

    monkeypatch.setattr(cli, "XApiClient", FakeClient)
    monkeypatch.setattr(cli, "execute_unfollows", fake_execute_unfollows)

    result = runner.invoke(cli.app, ["unfollow", "--app-dir", str(app_dir), "--yes"])

    assert result.exit_code == 0
    assert "@quiet" in result.output
    assert "Unfollow complete" in result.output
    assert "Success: 1" in result.output
    assert len(calls) == 1
    api, source_user_id, records, dry_run = calls[0]
    assert isinstance(api, FakeClient)
    assert api.token == "test-token"
    assert source_user_id == "me"
    assert [item.user.id for item in records] == ["target"]
    assert dry_run is False
    assert created_clients[0].closed is True


def test_unfollow_yes_prints_targets_before_executing(tmp_path, monkeypatch):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_bearer_token("test-token")
    Storage(app_dir).save_decisions(
        [replace(record("quiet", user_id="target"), review="unfollow")]
    )
    Storage(app_dir).save_scan_context("me", "me")
    printed = []
    original_console = cli.console

    class CapturingConsole:
        def print(self, *args, **kwargs):
            printed.append(" ".join(str(arg) for arg in args))
            return original_console.print(*args, **kwargs)

    class FakeClient:
        def __init__(self, token):
            self.token = token

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_me(self):
            return XUser(id="me", username="me", name="Me")

    def fake_execute_unfollows(api, source_user_id, records, safety, dry_run):
        assert any("@quiet" in line for line in printed)

        class Result:
            attempted_count = 1
            success_count = 1
            failed_count = 0
            dry_run = False

        return Result()

    monkeypatch.setattr(cli, "console", CapturingConsole())
    monkeypatch.setattr(cli, "XApiClient", FakeClient)
    monkeypatch.setattr(cli, "execute_unfollows", fake_execute_unfollows)

    result = runner.invoke(cli.app, ["unfollow", "--app-dir", str(app_dir), "--yes"])

    assert result.exit_code == 0
    assert "@quiet" in result.output


def test_real_unfollow_marks_success_and_writes_audit(tmp_path, monkeypatch):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_bearer_token("test-token")
    Storage(app_dir).save_decisions(
        [replace(record("quiet", user_id="target"), review="unfollow")]
    )
    Storage(app_dir).save_scan_context("me", "buhusa")

    class FakeClient:
        def __init__(self, token):
            assert token == "test-token"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_me(self):
            return XUser(id="me", username="buhusa", name="Buhusa")

        def unfollow(self, source_user_id, target_user_id):
            assert (source_user_id, target_user_id) == ("me", "target")
            return True

    monkeypatch.setattr(cli, "XApiClient", FakeClient)

    result = runner.invoke(
        cli.app,
        ["unfollow", "--app-dir", str(app_dir), "--yes"],
    )

    assert result.exit_code == 0
    assert "Success: 1" in result.output
    assert Storage(app_dir).load_decisions()[0].review == "unfollowed"
    audit = Storage(app_dir).unfollow_audit_path.read_text(encoding="utf-8")
    assert '"username": "quiet"' in audit
    assert '"success": true' in audit


def test_real_unfollow_refuses_candidates_scanned_for_different_account(
    tmp_path, monkeypatch
):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_bearer_token("test-token")
    Storage(app_dir).save_decisions(
        [replace(record("quiet", user_id="target"), review="unfollow")]
    )
    Storage(app_dir).save_scan_context("buhusa-id", "buhusa")

    class FakeClient:
        def __init__(self, token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_me(self):
            return XUser(id="other-id", username="other", name="Other")

        def unfollow(self, source_user_id, target_user_id):
            raise AssertionError("account mismatch must block all unfollows")

    monkeypatch.setattr(cli, "XApiClient", FakeClient)

    result = runner.invoke(
        cli.app,
        ["unfollow", "--app-dir", str(app_dir), "--yes"],
    )

    assert result.exit_code == 1
    assert "Account mismatch" in result.output
    assert "@buhusa" in result.output
    assert "@other" in result.output


def test_x_api_error_in_scan_exits_friendly(tmp_path, monkeypatch):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_bearer_token("test-token")

    class FakeClient:
        def __init__(self, token):
            self.token = token

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_me(self):
            return XUser(id="me", username="me", name="Me")

    def fake_scan_accounts(api, source_user_id, config, limit=None, progress=None):
        raise XApiError("X API returned 403: access denied", status_code=403)

    monkeypatch.setattr(cli, "XApiClient", FakeClient)
    monkeypatch.setattr(cli, "scan_accounts", fake_scan_accounts)

    result = runner.invoke(cli.app, ["scan", "--app-dir", str(app_dir), "--yes"])

    assert result.exit_code == 1
    assert "X API returned 403: access denied" in result.output
    assert "Developer Console" in result.output
    assert "access" in result.output
    assert "credits" in result.output
    assert "scopes" in result.output
    assert "Traceback" not in result.output


def test_scan_shows_cost_and_can_cancel_without_loading_token(tmp_path):
    app_dir = tmp_path / "state"

    result = runner.invoke(
        cli.app,
        ["scan", "--app-dir", str(app_dir), "--limit", "3"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "up to 3 account(s)" in result.output
    assert "$0.10" in result.output
    assert "Cancelled" in result.output
    assert "Missing bearer token" not in result.output


def test_scan_hard_budget_blocks_before_token_or_api_use(tmp_path):
    app_dir = tmp_path / "state"
    app_dir.mkdir()
    (app_dir / "config.toml").write_text(
        "[api]\nmax_scan_cost_usd = 0.05\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli.app,
        ["scan", "--app-dir", str(app_dir), "--limit", "3", "--yes"],
    )

    assert result.exit_code == 2
    assert "Blocked by hard scan budget $0.05" in result.output
    assert "Missing X user login" not in result.output
