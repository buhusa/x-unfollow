from dataclasses import replace
from datetime import datetime, timezone

from typer.testing import CliRunner

import x_unfollow.cli as cli
from x_unfollow.cli import app
from x_unfollow.config import load_config
from x_unfollow.models import CombinationMode, DecisionRecord, XUser
from x_unfollow.storage import Storage


runner = CliRunner()


def record(username: str = "quiet") -> DecisionRecord:
    return DecisionRecord(
        user=XUser(id=username, username=username, name=username.title()),
        last_own_post_at=None,
        days_since_own_post=None,
        last_reply_at=None,
        days_since_reply=None,
        rule_match_own_post=True,
        rule_match_reply=True,
        decision="candidate",
        reason="test",
    )


def test_about_shows_banner_and_safety_positioning():
    result = runner.invoke(app, ["about"])

    assert result.exit_code == 0
    assert "x-unfollow" in result.output
    assert "API-only" in result.output
    assert "no browser scraping" in result.output
    assert "no LLM decisions" in result.output
    assert "review-first" in result.output
    assert "https://buhussy.xyz" in result.output
    assert "https://x.com/buhusa" in result.output


def test_config_init_writes_config(tmp_path):
    app_dir = tmp_path / "state"

    result = runner.invoke(app, ["config", "init", "--app-dir", str(app_dir)])

    assert result.exit_code == 0
    config_path = app_dir / "config.toml"
    assert config_path.exists()
    assert 'combination = "and"' in config_path.read_text(encoding="utf-8")


def test_config_init_preserves_existing_config(tmp_path):
    app_dir = tmp_path / "state"
    config_path = app_dir / "config.toml"
    app_dir.mkdir()
    config_path.write_text("# keep me\n[rules]\ncombination = \"or\"\n", encoding="utf-8")

    result = runner.invoke(app, ["config", "init", "--app-dir", str(app_dir)])

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == (
        "# keep me\n[rules]\ncombination = \"or\"\n"
    )


def test_config_edit_updates_values_from_guided_prompts(tmp_path):
    app_dir = tmp_path / "state"
    user_input = "\n".join(
        [
            "90",
            "30",
            "or",
            "n",
            "y",
            "3",
            "5",
            "2",
            "0.25",
            "10",
            "",
        ]
    )

    result = runner.invoke(
        app,
        ["config", "edit", "--app-dir", str(app_dir)],
        input=user_input,
    )

    assert result.exit_code == 0
    assert "Config saved" in result.output
    config = load_config(app_dir / "config.toml")
    assert config.rules.own_post_threshold_days == 90
    assert config.rules.reply_threshold_days == 30
    assert config.rules.combination == CombinationMode.OR
    assert config.api.max_accounts_per_scan == 3
    assert config.api.page_size_tweets == 5
    assert config.api.max_tweet_pages_per_user == 2
    assert config.api.max_scan_cost_usd == 0.25
    assert config.safety.max_unfollows_per_run == 10


def test_status_empty_app_dir_renders_counts_without_stack_trace(tmp_path):
    app_dir = tmp_path / "state"

    result = runner.invoke(app, ["status", "--app-dir", str(app_dir)])

    assert result.exit_code == 0
    assert str(app_dir / "config.toml") in result.output
    assert "X connection: not connected" in result.output
    assert "and" in result.output
    assert "Own-post threshold: 180 days" in result.output
    assert "Reply threshold: 180 days" in result.output
    assert "Maximum accounts per scan: 10" in result.output
    assert "Hard scan budget: $0.50" in result.output
    assert "Candidate count" in result.output
    assert "Marked unfollow count" in result.output
    assert "0" in result.output
    assert "Traceback" not in result.output
    assert not (app_dir / "data").exists()
    assert not (app_dir / "exports").exists()


def test_status_malformed_config_exits_2_with_friendly_message(tmp_path):
    app_dir = tmp_path / "state"
    config_path = app_dir / "config.toml"
    app_dir.mkdir()
    config_path.write_text("[rules]\ncombination = \"xor\"\n", encoding="utf-8")

    result = runner.invoke(app, ["status", "--app-dir", str(app_dir)])

    assert result.exit_code == 2
    assert "Invalid config" in result.output
    assert str(config_path) in result.output
    assert "Traceback" not in result.output


def test_status_distinguishes_unverified_oauth_login(tmp_path):
    app_dir = tmp_path / "state"
    cli.TokenStore(app_dir).save_oauth_credentials(
        cli.OAuthCredentials(
            client_id="client-id",
            token=cli.OAuthToken(
                access_token="access",
                refresh_token="refresh",
                expires_at=None,
                scope=cli.DEFAULT_SCOPES,
            ),
            verified=False,
        )
    )

    result = runner.invoke(app, ["status", "--app-dir", str(app_dir)])

    assert result.exit_code == 0
    assert "OAuth 2.0 login saved, connection test pending" in result.output


def test_unfollow_malformed_config_exits_2_with_friendly_message(tmp_path):
    app_dir = tmp_path / "state"
    config_path = app_dir / "config.toml"
    app_dir.mkdir()
    config_path.write_text("[rules]\ncombination = \"xor\"\n", encoding="utf-8")

    result = runner.invoke(app, ["unfollow", "--app-dir", str(app_dir), "--dry-run"])

    assert result.exit_code == 2
    assert "Invalid config" in result.output
    assert str(config_path) in result.output
    assert "Traceback" not in result.output


def test_unfollow_dry_run_with_no_data_prints_friendly_message(tmp_path):
    result = runner.invoke(
        app,
        ["unfollow", "--app-dir", str(tmp_path / "state"), "--dry-run"],
    )

    assert result.exit_code == 0
    assert "No reviewed accounts marked for unfollow." in result.output
    assert "Traceback" not in result.output


def test_unfollow_dry_run_uses_executor_without_real_api(tmp_path, monkeypatch):
    app_dir = tmp_path / "state"
    storage = Storage(app_dir)
    storage.save_decisions([replace(record("quiet"), review="unfollow")])
    calls = []

    def fake_execute_unfollows(api, source_user_id, records, safety, dry_run):
        calls.append((api, source_user_id, records, safety, dry_run))

        class Result:
            attempted_count = 1
            success_count = 0
            failed_count = 0
            dry_run = True

        return Result()

    monkeypatch.setattr("x_unfollow.cli.execute_unfollows", fake_execute_unfollows)

    result = runner.invoke(app, ["unfollow", "--app-dir", str(app_dir), "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "1" in result.output
    assert len(calls) == 1
    api, source_user_id, records, _safety, dry_run = calls[0]
    assert api is None
    assert source_user_id == "dry-run"
    assert [record.user.username for record in records] == ["quiet"]
    assert dry_run is True


def test_review_shows_activity_timestamp_and_age(tmp_path):
    app_dir = tmp_path / "state"
    activity_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    Storage(app_dir).save_decisions(
        [
            replace(
                record("quiet"),
                last_own_post_at=activity_at,
                days_since_own_post=208,
            )
        ]
    )

    result = runner.invoke(
        app,
        ["review", "--app-dir", str(app_dir)],
        input="s\n",
    )

    assert result.exit_code == 0
    assert "Last own post: 2026-01-02 03:04 UTC (208 days ago)" in result.output
    assert "Last reply: not found in scanned activity" in result.output


def test_review_excludes_candidate_with_contradictory_missing_activity(tmp_path):
    app_dir = tmp_path / "state"
    unsafe_record = replace(
        record("active"),
        user=replace(record("active").user, most_recent_tweet_id="recent-post"),
    )
    Storage(app_dir).save_decisions([unsafe_record])

    result = runner.invoke(app, ["review", "--app-dir", str(app_dir)])

    assert result.exit_code == 0
    assert "Safety notice: 1 candidate(s)" in result.output
    assert "Run a new scan" in result.output
    assert "[u] mark for unfollow" not in result.output


def test_root_menu_exits_cleanly_when_user_skips_setup_and_enters_q(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("X_UNFOLLOW_HOME", str(tmp_path / "state"))

    result = runner.invoke(app, input="n\nq\n")

    assert result.exit_code == 0
    assert "First-time setup" in result.output
    assert "Setup skipped" in result.output
    assert "[6]" in result.output
    assert "Connect or change X account" in result.output
    assert "q  Quit" in result.output


def test_root_menu_runs_real_status_action_and_returns_to_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("X_UNFOLLOW_HOME", str(tmp_path / "state"))

    result = runner.invoke(app, input="n\n7\n\nq\n")

    assert result.exit_code == 0
    assert "x-unfollow status" in result.output
    assert result.output.count("q  Quit") == 2


def test_exports_menu_action_reports_empty_directory_without_creating_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("X_UNFOLLOW_HOME", str(tmp_path / "state"))

    result = runner.invoke(app, input="n\n8\n\nq\n")

    assert result.exit_code == 0
    assert "No exports yet" in result.output
    assert not (tmp_path / "state" / "exports").exists()


def test_first_time_onboarding_connects_and_checks_after_confirmation(
    tmp_path, monkeypatch
):
    app_dir = tmp_path / "state"
    monkeypatch.setenv("X_UNFOLLOW_HOME", str(app_dir))
    calls = []

    def fake_connect(received_app_dir, *, check_without_prompt):
        calls.append((received_app_dir, check_without_prompt))

    monkeypatch.setattr(cli, "_connect_account", fake_connect)

    result = runner.invoke(app, input="y\nq\n")

    assert result.exit_code == 0
    assert "First-time setup" in result.output
    assert calls == [(app_dir, True)]


def test_tui_down_key_moves_selection(monkeypatch):
    monkeypatch.setattr(cli.readchar, "readkey", lambda: cli.readchar.key.DOWN)

    selected_index, choice = cli._read_tui_menu_choice(0)

    assert selected_index == 1
    assert choice is None


def test_tui_number_key_opens_item_directly(monkeypatch):
    monkeypatch.setattr(cli.readchar, "readkey", lambda: "8")

    selected_index, choice = cli._read_tui_menu_choice(0)

    assert selected_index == cli._menu_index_for_key("8")
    assert choice == "8"


def test_merge_scan_records_replaces_duplicate_and_resets_destructive_review():
    older = replace(record("quiet"), review="unfollow")
    newer = replace(
        record("quiet"),
        decision="keep",
        rule_match_own_post=False,
        rule_match_reply=False,
    )

    merged = cli._merge_scan_records([older], [newer])

    assert len(merged) == 1
    assert merged[0].decision == "keep"
    assert merged[0].review == "pending"
