from __future__ import annotations

import tomllib
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import typer
import readchar
from rich.console import Console
from rich.table import Table
from rich.text import Text

from x_unfollow.banner import ABOUT, BANNER
from x_unfollow.config import (
    default_app_dir,
    load_config,
    write_config,
    write_default_config,
)
from x_unfollow.models import (
    ApiConfig,
    AppConfig,
    CombinationMode,
    DecisionRecord,
    RuleConfig,
    SafetyConfig,
)
from x_unfollow.oauth import (
    DEFAULT_REDIRECT_URI,
    DEFAULT_SCOPES,
    OAuthError,
    OAuthToken,
    XOAuth2PKCE,
)
from x_unfollow.review import apply_review_choice
from x_unfollow.scanner import scan_accounts
from x_unfollow.storage import Storage
from x_unfollow.tokens import MissingTokenError, OAuthCredentials, TokenStore
from x_unfollow.unfollow import execute_unfollows
from x_unfollow.x_api import RateLimitError, XApiClient, XApiError


console = Console()
_interactive_tui = False

USER_READ_COST_USD = 0.01
OWNED_FOLLOWING_READ_COST_USD = 0.001
POST_READ_COST_USD = 0.005

app = typer.Typer(
    help="x-unfollow: review and unfollow inactive X accounts.",
    no_args_is_help=False,
)
config_app = typer.Typer(help="Manage local x-unfollow configuration.")
app.add_typer(config_app, name="config")


def _banner_text() -> Text:
    lines = BANNER.splitlines()
    rendered = Text()
    for index, line in enumerate(lines):
        rendered.append(line[:8], style="bold bright_magenta")
        rendered.append(line[8:], style="bold bright_cyan")
        if index < len(lines) - 1:
            rendered.append("\n")
    return rendered


def _print_banner() -> None:
    console.print(_banner_text())
    console.print("REVIEW FIRST. UNFOLLOW WITH CONTROL.", style="dim")


def _render_tui_header(title: str) -> None:
    if not _interactive_tui:
        return
    console.clear()
    _print_banner()
    console.print()
    console.print(title, markup=False)
    console.print()


def _config_path(app_dir: Path) -> Path:
    return app_dir / "config.toml"


def _resolve_app_dir(app_dir: Path | None) -> Path:
    return app_dir or default_app_dir()


def _load_config_or_exit(path: Path):
    try:
        return load_config(path)
    except (tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        console.print(f"Invalid config: {path}: {exc}", soft_wrap=True)
        raise typer.Exit(2) from exc


def _eligible_unfollow_records(records: list[DecisionRecord]) -> list[DecisionRecord]:
    return [
        record
        for record in records
        if record.review == "unfollow"
        and record.decision == "candidate"
        and record.account_status == "ok"
        and _has_activity_evidence(record)
    ]


def _has_activity_evidence(record: DecisionRecord) -> bool:
    return not (
        record.user.most_recent_tweet_id
        and record.last_own_post_at is None
        and record.last_reply_at is None
    )


def _format_activity(value: datetime | None, days: int | None) -> str:
    if value is None or days is None:
        return "not found in scanned activity"
    timestamp = value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if days == 1:
        age = "1 day ago"
    else:
        age = f"{days} days ago"
    return f"{timestamp} ({age})"


def _print_unfollow_targets(records: list[DecisionRecord]) -> None:
    console.print("Accounts marked for unfollow:", soft_wrap=True)
    for record in records:
        console.print(f"- @{record.user.username} ({record.user.name})", soft_wrap=True)


def _print_cli_error(exc: Exception) -> None:
    console.print(str(exc), soft_wrap=True)


def _validate_oauth_scopes(token: OAuthToken) -> None:
    missing = [scope for scope in DEFAULT_SCOPES if scope not in token.scope]
    if missing:
        raise OAuthError(
            "X did not grant all required permissions. Missing scopes: "
            + ", ".join(missing)
        )


def _load_access_token(app_dir: Path) -> str:
    store = TokenStore(app_dir)
    try:
        credentials = store.load_oauth_credentials()
    except MissingTokenError:
        return store.load_bearer_token()

    token = credentials.token
    if not token.is_expired(leeway_seconds=60):
        return token.access_token
    if not token.refresh_token:
        raise MissingTokenError(
            "X login expired and has no refresh token. Run `x-unfollow setup`."
        )

    with XOAuth2PKCE(credentials.client_id) as oauth:
        refreshed = oauth.refresh(token.refresh_token, scopes=token.scope)
    _validate_oauth_scopes(refreshed)
    store.save_oauth_credentials(
        OAuthCredentials(
            client_id=credentials.client_id,
            token=refreshed,
            verified=credentials.verified,
        )
    )
    return refreshed.access_token


def _prompt_positive_int(label: str, default: int) -> int:
    while True:
        value = typer.prompt(label, default=default, type=int)
        if value > 0:
            return value
        console.print("Please enter a number greater than 0.")


def _prompt_int_range(label: str, default: int, minimum: int, maximum: int) -> int:
    while True:
        value = typer.prompt(label, default=default, type=int)
        if minimum <= value <= maximum:
            return value
        console.print(f"Please enter a number from {minimum} to {maximum}.")


def _prompt_positive_float(label: str, default: float) -> float:
    while True:
        value = typer.prompt(label, default=default, type=float)
        if value > 0:
            return value
        console.print("Please enter a number greater than 0.")


def _prompt_combination(default: CombinationMode) -> CombinationMode:
    while True:
        value = typer.prompt(
            "Rule combination [and/or]",
            default=default.value,
        ).strip().lower()
        try:
            return CombinationMode(value)
        except ValueError:
            console.print("Please enter 'and' or 'or'.")


def _estimated_scan_cost_usd(config, limit: int | None) -> float:
    account_count = limit or config.api.max_accounts_per_scan
    latest_lookup_cost = 0.0
    rules = config.rules
    if (
        rules.combination == CombinationMode.AND
        and rules.own_post_threshold_days == rules.reply_threshold_days
        and not rules.count_retweets_as_activity
    ):
        latest_lookup_cost = account_count * POST_READ_COST_USD
    return (
        USER_READ_COST_USD
        + account_count * OWNED_FOLLOWING_READ_COST_USD
        + latest_lookup_cost
        + account_count
        * config.api.page_size_tweets
        * config.api.max_tweet_pages_per_user
        * POST_READ_COST_USD
    )


def _print_api_error(exc: XApiError) -> None:
    if isinstance(exc, RateLimitError) and exc.reset_at is not None:
        console.print(f"{exc} Try again after {exc.reset_at.isoformat()}.", soft_wrap=True)
        return
    console.print(str(exc), soft_wrap=True)
    if _should_print_api_access_guidance(exc):
        console.print(
            "Check the X Developer Console for API access, billing/credits, "
            "and bearer token scopes.",
            soft_wrap=True,
        )


def _should_print_api_access_guidance(exc: XApiError) -> bool:
    if exc.status_code in {401, 402, 403}:
        return True

    message = str(exc).lower()
    return any(
        keyword in message
        for keyword in ("access", "billing", "credit", "scope", "permission")
    )


MENU_ITEMS = (
    ("WORKFLOW", "1", "Scan followed accounts", "Analyze your current following list"),
    ("WORKFLOW", "2", "Review and mark candidates", "No account is changed here"),
    ("WORKFLOW", "3", "Preview marked unfollows", "Dry run with no changes"),
    ("WORKFLOW", "4", "Execute marked unfollows", "Final confirmation required"),
    ("SETUP", "5", "Rules, limits, and budget", "Configure matching and API spend"),
    ("SETUP", "6", "Connect or change X account", "OAuth login and connection test"),
    ("MORE", "7", "Detailed status", "Configuration and local counters"),
    ("MORE", "8", "Export files", "Open the generated file list"),
    ("MORE", "9", "About", "Project and author information"),
)


def _menu_status(app_dir: Path) -> tuple[str, str, str, str]:
    connected = _has_verified_oauth_login(app_dir)
    context = Storage(app_dir).load_scan_context()
    records = Storage(app_dir).load_decisions()

    if connected and context is not None:
        account_line = (
            f"OAuth: connected | Last scan account: @{context['source_username']}"
        )
    elif connected:
        account_line = "OAuth: connected | Last scan account: none"
    else:
        account_line = "OAuth: not connected"

    if not records:
        scan_line = "Last scan: none"
        next_line = (
            "Next: [1] Scan followed accounts"
            if connected
            else "Next: [6] Connect your X account"
        )
        recommended_key = "1" if connected else "6"
        return account_line, scan_line, next_line, recommended_key

    candidates = [
        record
        for record in records
        if record.decision == "candidate" and _has_activity_evidence(record)
    ]
    pending_count = sum(1 for record in candidates if record.review == "pending")
    marked_count = sum(1 for record in candidates if record.review == "unfollow")
    scan_line = (
        f"Last scan: {len(records)} accounts | "
        f"{pending_count} to review | {marked_count} marked"
    )
    if pending_count:
        next_line = "Next: [2] Review candidates and mark actions"
        recommended_key = "2"
    elif marked_count:
        next_line = "Next: [3] Preview, then [4] execute marked unfollows"
        recommended_key = "3"
    else:
        next_line = "Next: [1] Start a new scan when needed"
        recommended_key = "1"
    return account_line, scan_line, next_line, recommended_key


def _print_menu(app_dir: Path, selected_index: int = 0) -> None:
    account_line, scan_line, next_line, _recommended_key = _menu_status(app_dir)
    _print_banner()
    console.print()

    status = Table.grid(expand=False, padding=(0, 1))
    status.add_column(style="bold")
    status.add_column()
    status.add_row("SESSION", account_line)
    status.add_row("RESULTS", scan_line)
    status.add_row("NEXT", next_line)
    console.print(status)
    console.print()

    menu = Table.grid(expand=False, padding=(0, 1))
    menu.add_column(width=2, no_wrap=True)
    menu.add_column(width=4, no_wrap=True)
    menu.add_column(min_width=29, no_wrap=True)
    menu.add_column(style="dim")
    previous_section = None
    for index, (section, key, label, description) in enumerate(MENU_ITEMS):
        if section != previous_section:
            menu.add_row("", "", Text(section, style="bold bright_cyan"), "")
            previous_section = section

        selected = index == selected_index
        row_style = "reverse bold" if selected else ""
        menu.add_row(
            ">" if selected else " ",
            Text(f"[{key}]"),
            label,
            description,
            style=row_style,
        )
    console.print(menu)
    console.print()
    console.print(
        "UP/DOWN or j/k  Navigate    ENTER  Select    1-9  Open directly    q  Quit",
        style="dim",
        markup=False,
    )


def _show_exports(app_dir: Path | None) -> None:
    export_dir = _resolve_app_dir(app_dir) / "exports"
    files = sorted(path for path in export_dir.glob("*") if path.is_file())
    if not files:
        console.print(f"No exports yet: {export_dir}")
        return
    console.print("Export files:")
    for path in files:
        console.print(f"- {path}", soft_wrap=True)


def _has_saved_oauth_login(app_dir: Path) -> bool:
    try:
        TokenStore(app_dir).load_oauth_credentials()
    except MissingTokenError:
        return False
    return True


def _has_verified_oauth_login(app_dir: Path) -> bool:
    try:
        return TokenStore(app_dir).load_oauth_credentials().verified
    except MissingTokenError:
        return False


def _connect_account(app_dir: Path | None, *, check_without_prompt: bool) -> None:
    setup(app_dir, None, False)
    check(app_dir, yes=check_without_prompt)


def _run_first_time_onboarding(app_dir: Path) -> None:
    _print_banner()
    console.print()
    console.print("First-time setup: no X OAuth login was found.", soft_wrap=True)
    console.print(
        "You will enter the OAuth 2.0 Client ID, authorize X in your browser, "
        "and run one read-only connection check (estimated cost about $0.01).",
        soft_wrap=True,
    )
    if not typer.confirm("Start guided setup now?", default=True):
        console.print("Setup skipped. Choose menu option 6 when you are ready.")
        return
    try:
        _connect_account(app_dir, check_without_prompt=True)
    except typer.Exit:
        console.print(
            "X authorization was saved, but the connection test failed. "
            "The test will be offered again on the next start.",
            soft_wrap=True,
        )


def _run_pending_connection_check(app_dir: Path) -> None:
    _print_banner()
    console.print()
    console.print(
        "X authorization is saved, but the connection test is still pending.",
        soft_wrap=True,
    )
    try:
        check(app_dir, yes=False)
    except typer.Exit:
        console.print(
            "The connection is still unverified. Retry with menu option 6.",
            soft_wrap=True,
        )


def _run_menu_action(choice: str) -> None:
    actions = {
        "1": lambda: scan(None, None, False),
        "scan": lambda: scan(None, None, False),
        "2": lambda: review(None),
        "r": lambda: review(None),
        "3": lambda: unfollow(None, True, False),
        "d": lambda: unfollow(None, True, False),
        "4": lambda: unfollow(None, False, False),
        "u": lambda: unfollow(None, False, False),
        "5": lambda: config_edit(None),
        "c": lambda: config_edit(None),
        "6": lambda: _connect_account(None, check_without_prompt=False),
        "connect": lambda: _connect_account(None, check_without_prompt=False),
        "7": lambda: status(None),
        "status": lambda: status(None),
        "8": lambda: _show_exports(None),
        "e": lambda: _show_exports(None),
        "9": about,
        "a": about,
        "about": about,
    }
    actions[choice]()


def _menu_index_for_key(key: str) -> int:
    return next(
        index
        for index, (_section, item_key, _label, _description) in enumerate(MENU_ITEMS)
        if item_key == key
    )


def _read_tui_menu_choice(selected_index: int) -> tuple[int, str | None]:
    pressed = readchar.readkey()
    if pressed in {readchar.key.UP, "k", "K"}:
        return (selected_index - 1) % len(MENU_ITEMS), None
    if pressed in {readchar.key.DOWN, "j", "J"}:
        return (selected_index + 1) % len(MENU_ITEMS), None
    if pressed in {readchar.key.ENTER, "\n", "\r"}:
        return selected_index, MENU_ITEMS[selected_index][1]
    if pressed in {str(number) for number in range(1, 10)}:
        return _menu_index_for_key(pressed), pressed
    if pressed in {"q", "Q", readchar.key.CTRL_C}:
        return selected_index, "q"
    return selected_index, None


def _wait_for_tui_return() -> None:
    console.print()
    console.print("Press ENTER to return to the menu", style="dim")
    while readchar.readkey() not in {readchar.key.ENTER, "\n", "\r"}:
        pass


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """Show a calm numbered menu when no subcommand is provided."""
    global _interactive_tui

    if ctx.invoked_subcommand is not None:
        return

    resolved_app_dir = _resolve_app_dir(None)
    use_tui = console.is_terminal
    screen = console.screen(hide_cursor=False) if use_tui else nullcontext()
    _interactive_tui = use_tui
    try:
        with screen:
            if use_tui:
                console.clear()
            if not _has_saved_oauth_login(resolved_app_dir):
                _run_first_time_onboarding(resolved_app_dir)
            elif not _has_verified_oauth_login(resolved_app_dir):
                _run_pending_connection_check(resolved_app_dir)

            recommended_key = _menu_status(resolved_app_dir)[3]
            selected_index = _menu_index_for_key(recommended_key)
            while True:
                if use_tui:
                    console.clear()
                _print_menu(resolved_app_dir, selected_index)
                if use_tui:
                    selected_index, choice = _read_tui_menu_choice(selected_index)
                    if choice is None:
                        continue
                else:
                    choice = typer.prompt(
                        "Select",
                        default="",
                        show_default=False,
                    ).strip().lower()
                if choice in {"q", "quit", "exit"}:
                    raise typer.Exit(0)
                valid_choices = {
                    "1",
                    "s",
                    "2",
                    "c",
                    "3",
                    "scan",
                    "4",
                    "r",
                    "5",
                    "d",
                    "6",
                    "u",
                    "7",
                    "status",
                    "8",
                    "e",
                    "9",
                    "a",
                    "about",
                }
                if choice not in valid_choices:
                    console.print("Please enter a menu number or q.")
                else:
                    if use_tui:
                        console.clear()
                    try:
                        _run_menu_action(choice)
                    except typer.Exit:
                        pass

                if use_tui:
                    _wait_for_tui_return()
                else:
                    typer.prompt(
                        "Press Enter to return to the menu",
                        default="",
                        show_default=False,
                    )
                recommended_key = _menu_status(resolved_app_dir)[3]
                selected_index = _menu_index_for_key(recommended_key)
    finally:
        _interactive_tui = False


@app.command()
def about() -> None:
    """Show what x-unfollow does and does not do."""
    _print_banner()
    console.print()
    console.print(ABOUT)


@config_app.command("init")
def config_init(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
) -> None:
    """Create a default config.toml if one does not already exist."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    path = _config_path(resolved_app_dir)
    if path.exists():
        console.print(f"Config already exists: {path}")
        return

    write_default_config(path)
    console.print(f"Wrote config: {path}")


@config_app.command("edit")
def config_edit(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
) -> None:
    """Edit rules and safety limits with guided prompts."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    path = _config_path(resolved_app_dir)
    current = _load_config_or_exit(path)

    console.print("Press Enter to keep the value shown in brackets.")
    own_days = _prompt_positive_int(
        "Own-post inactivity threshold (days)",
        current.rules.own_post_threshold_days,
    )
    reply_days = _prompt_positive_int(
        "Reply inactivity threshold (days)",
        current.rules.reply_threshold_days,
    )
    combination = _prompt_combination(current.rules.combination)
    count_retweets = typer.confirm(
        "Count reposts as activity?",
        default=current.rules.count_retweets_as_activity,
    )
    count_quotes = typer.confirm(
        "Count quote posts as own posts?",
        default=current.rules.count_quote_posts_as_own_posts,
    )
    max_accounts = _prompt_positive_int(
        "Maximum accounts per scan",
        current.api.max_accounts_per_scan,
    )
    posts_per_page = _prompt_int_range(
        "Posts fetched per account and page",
        current.api.page_size_tweets,
        5,
        100,
    )
    max_pages = _prompt_positive_int(
        "Maximum post pages per account",
        current.api.max_tweet_pages_per_user,
    )
    max_scan_cost = _prompt_positive_float(
        "Hard maximum estimated scan cost in USD",
        current.api.max_scan_cost_usd,
    )
    max_unfollows = _prompt_positive_int(
        "Maximum unfollows per run",
        current.safety.max_unfollows_per_run,
    )

    updated = AppConfig(
        rules=RuleConfig(
            own_post_threshold_days=own_days,
            reply_threshold_days=reply_days,
            combination=combination,
            count_retweets_as_activity=count_retweets,
            count_quote_posts_as_own_posts=count_quotes,
        ),
        safety=SafetyConfig(
            require_review_before_unfollow=True,
            max_unfollows_per_run=max_unfollows,
        ),
        api=ApiConfig(
            page_size_following=current.api.page_size_following,
            page_size_tweets=posts_per_page,
            max_tweet_pages_per_user=max_pages,
            max_accounts_per_scan=max_accounts,
            max_scan_cost_usd=max_scan_cost,
        ),
    )
    write_config(path, updated)
    console.print(f"Config saved: {path}")


@app.command()
def setup(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
    client_id: str | None = typer.Option(
        None,
        "--client-id",
        help="OAuth 2.0 Client ID from the X Developer Console.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Print the authorization URL instead of opening it automatically.",
    ),
) -> None:
    """Connect an X account with OAuth 2.0 PKCE."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    oauth_client_id = client_id
    if oauth_client_id is None:
        oauth_client_id = typer.prompt(
            "OAuth 2.0 Client ID",
            default="",
            show_default=False,
        )
    if not oauth_client_id or not oauth_client_id.strip():
        console.print("No Client ID provided. Find it under App > Keys and tokens.")
        raise typer.Exit(1)

    console.print(f"Required callback URI: {DEFAULT_REDIRECT_URI}", soft_wrap=True)
    console.print(
        "Required scopes: " + ", ".join(DEFAULT_SCOPES),
        soft_wrap=True,
    )
    console.print("Waiting up to 3 minutes for X authorization.", soft_wrap=True)

    def show_authorization_url(url: str) -> None:
        console.print(f"Authorization URL: {url}", soft_wrap=True)

    try:
        with XOAuth2PKCE(oauth_client_id.strip()) as oauth:
            oauth_token = oauth.authorize(
                open_browser=not no_browser,
                authorization_url_handler=show_authorization_url,
            )
        _validate_oauth_scopes(oauth_token)
    except (OAuthError, ValueError) as exc:
        _print_cli_error(exc)
        raise typer.Exit(1) from exc

    TokenStore(resolved_app_dir).save_oauth_credentials(
        OAuthCredentials(
            client_id=oauth_client_id.strip(),
            token=oauth_token,
            verified=False,
        )
    )
    console.print("X account connected. Tokens were stored locally.", soft_wrap=True)


@app.command()
def status(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
) -> None:
    """Show local configuration and review counts."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    path = _config_path(resolved_app_dir)
    config = _load_config_or_exit(path)
    records = Storage(resolved_app_dir).load_decisions()
    candidate_count = sum(1 for record in records if record.decision == "candidate")
    marked_count = len(_eligible_unfollow_records(records))
    token_store = TokenStore(resolved_app_dir)
    try:
        credentials = token_store.load_oauth_credentials()
        connection_status = (
            "OAuth 2.0 login verified"
            if credentials.verified
            else "OAuth 2.0 login saved, connection test pending"
        )
    except MissingTokenError:
        try:
            token_store.load_bearer_token()
            connection_status = "manual access token saved"
        except MissingTokenError:
            connection_status = "not connected"

    console.print("x-unfollow status", soft_wrap=True)
    console.print(f"Config path: {path}", soft_wrap=True)
    console.print(f"X connection: {connection_status}", soft_wrap=True)
    console.print(f"Combination mode: {config.rules.combination.value}", soft_wrap=True)
    console.print(
        f"Own-post threshold: {config.rules.own_post_threshold_days} days",
        soft_wrap=True,
    )
    console.print(
        f"Reply threshold: {config.rules.reply_threshold_days} days",
        soft_wrap=True,
    )
    console.print(
        f"Maximum accounts per scan: {config.api.max_accounts_per_scan}",
        soft_wrap=True,
    )
    console.print(
        f"Hard scan budget: ${config.api.max_scan_cost_usd:.2f}",
        soft_wrap=True,
    )
    console.print(
        f"Maximum unfollows per run: {config.safety.max_unfollows_per_run}",
        soft_wrap=True,
    )
    console.print(f"Candidate count: {candidate_count}", soft_wrap=True)
    console.print(f"Marked unfollow count: {marked_count}", soft_wrap=True)


@app.command()
def check(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the estimated-cost confirmation.",
    ),
) -> None:
    """Verify the connected X account with one read-only API request."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    console.print("Estimated API read cost: about $0.01.", soft_wrap=True)
    if not yes and not typer.confirm("Run the read-only connection check?", default=False):
        console.print("Cancelled.")
        return

    try:
        token = _load_access_token(resolved_app_dir)
        with XApiClient(token) as api:
            me = api.get_me()
    except MissingTokenError as exc:
        _print_cli_error(exc)
        raise typer.Exit(1) from exc
    except OAuthError as exc:
        _print_cli_error(exc)
        raise typer.Exit(1) from exc
    except XApiError as exc:
        _print_api_error(exc)
        raise typer.Exit(1) from exc

    token_store = TokenStore(resolved_app_dir)
    try:
        credentials = token_store.load_oauth_credentials()
    except MissingTokenError:
        pass
    else:
        token_store.save_oauth_credentials(replace(credentials, verified=True))

    console.print(f"Connected as @{me.username} ({me.name}).", soft_wrap=True)


@app.command()
def scan(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Maximum accounts to scan in this run (overrides config).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the estimated-cost confirmation.",
    ),
) -> None:
    """Scan followed accounts and export candidate decisions."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    config = _load_config_or_exit(_config_path(resolved_app_dir))
    effective_limit = limit or config.api.max_accounts_per_scan
    estimated_cost = _estimated_scan_cost_usd(config, limit)
    console.print(
        f"Scanning up to {effective_limit} account(s). "
        f"Estimated worst-case API read cost: ${estimated_cost:.2f}.",
        soft_wrap=True,
    )
    if estimated_cost > config.api.max_scan_cost_usd:
        console.print(
            f"Blocked by hard scan budget ${config.api.max_scan_cost_usd:.2f}. "
            "Lower --limit or raise the budget with `x-unfollow config edit`.",
            soft_wrap=True,
        )
        raise typer.Exit(2)
    if not yes and not typer.confirm("Start this paid API scan?", default=False):
        console.print("Cancelled.")
        return

    def print_progress(
        stage: str,
        current: int,
        total: int,
        user,
    ) -> None:
        if _interactive_tui:
            _render_tui_header("SCAN IN PROGRESS")
            if stage == "following_loaded":
                console.print(f"Loaded {total} followed account(s).", soft_wrap=True)
                console.print("Preparing latest activity lookup...", soft_wrap=True)
            elif stage == "latest_activity":
                console.print(
                    f"Fetching latest activity for {total} account(s)...",
                    soft_wrap=True,
                )
            elif stage == "account" and user is not None:
                percent = round((current / total) * 100) if total else 100
                console.print(f"Progress: {current}/{total} ({percent}%)")
                console.print(f"Current account: @{user.username}", soft_wrap=True)
            return

        if stage == "following_loaded":
            console.print(f"Loaded {total} followed account(s).", soft_wrap=True)
        elif stage == "latest_activity":
            console.print(
                f"Fetching latest activity for {total} account(s)...",
                soft_wrap=True,
            )
        elif stage == "account" and user is not None:
            console.print(
                f"[{current}/{total}] Scanning @{user.username}...",
                soft_wrap=True,
            )

    if _interactive_tui:
        _render_tui_header("SCAN IN PROGRESS")
    console.print("Loading followed accounts...", soft_wrap=True)
    try:
        token = _load_access_token(resolved_app_dir)
        with XApiClient(token) as api:
            me = api.get_me()
            records = scan_accounts(
                api,
                me.id,
                config,
                limit=limit,
                progress=print_progress,
            )
    except MissingTokenError as exc:
        _print_cli_error(exc)
        raise typer.Exit(1) from exc
    except OAuthError as exc:
        _print_cli_error(exc)
        raise typer.Exit(1) from exc
    except XApiError as exc:
        _print_api_error(exc)
        raise typer.Exit(1) from exc

    storage = Storage(resolved_app_dir)
    storage.save_decisions(records)
    storage.save_scan_context(me.id, me.username)
    export_path = storage.export_candidates_csv(records)
    candidate_count = sum(1 for record in records if record.decision == "candidate")

    if _interactive_tui:
        _render_tui_header("SCAN COMPLETE")
    console.print(f"Scanned {len(records)} account(s).", soft_wrap=True)
    console.print(f"Candidates: {candidate_count}", soft_wrap=True)
    console.print(f"Export: {export_path}", soft_wrap=True)


@app.command()
def review(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
) -> None:
    """Review pending candidate accounts before unfollowing."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    storage = Storage(resolved_app_dir)
    records = storage.load_decisions()

    excluded_count = sum(
        1
        for record in records
        if record.decision == "candidate" and not _has_activity_evidence(record)
    )
    if excluded_count:
        console.print(
            f"Safety notice: {excluded_count} candidate(s) have incomplete "
            "activity evidence and were excluded. Run a new scan.",
            soft_wrap=True,
        )

    pending_indexes = [
        index
        for index, record in enumerate(records)
        if record.decision == "candidate"
        and record.account_status == "ok"
        and record.review == "pending"
        and _has_activity_evidence(record)
    ]
    if not pending_indexes:
        console.print("No pending candidates to review.")
        return

    for position, index in enumerate(pending_indexes, start=1):
        record = records[index]
        if _interactive_tui:
            _render_tui_header(
                f"REVIEW CANDIDATES  {position}/{len(pending_indexes)}"
            )
        while True:
            console.print(f"@{record.user.username}")
            console.print(f"Name: {record.user.name}", soft_wrap=True)
            console.print(f"Reason: {record.reason}", soft_wrap=True)
            console.print(
                "Last own post: "
                + _format_activity(
                    record.last_own_post_at,
                    record.days_since_own_post,
                ),
                soft_wrap=True,
            )
            console.print(
                "Last reply: "
                + _format_activity(
                    record.last_reply_at,
                    record.days_since_reply,
                ),
                soft_wrap=True,
            )
            choice = typer.prompt(
                "[u] mark for unfollow  [k] keep  [s] skip  [o] open URL  [q] quit",
                default="s",
                show_default=False,
            ).strip().lower()

            if choice == "q":
                return
            if choice == "o":
                console.print(f"https://x.com/{record.user.username}")
                continue
            if choice not in {"u", "k", "s"}:
                console.print("Please choose u, k, s, o, or q.")
                continue

            try:
                records[index] = apply_review_choice(record, choice)
            except ValueError as exc:
                console.print(str(exc), soft_wrap=True)
                continue
            storage.save_decisions(records)
            if choice == "u":
                console.print(
                    f"Marked @{record.user.username} for unfollow. "
                    "Use menu option 3 to preview or option 4 to execute.",
                    soft_wrap=True,
                )
            break

    marked_count = len(_eligible_unfollow_records(records))
    if _interactive_tui:
        _render_tui_header("REVIEW COMPLETE")
    console.print(
        f"Review complete. {marked_count} account(s) marked for unfollow. "
        "Nothing has been unfollowed yet.",
        soft_wrap=True,
    )


@app.command()
def unfollow(
    app_dir: Path | None = typer.Option(
        None,
        "--app-dir",
        help="Directory for config, data, tokens, and exports.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview reviewed unfollows without calling the X API.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip final confirmation once real API execution is wired.",
    ),
) -> None:
    """Execute reviewed unfollows."""
    resolved_app_dir = _resolve_app_dir(app_dir)
    config = _load_config_or_exit(_config_path(resolved_app_dir))
    records = Storage(resolved_app_dir).load_decisions()
    all_eligible_records = _eligible_unfollow_records(records)
    eligible_records = all_eligible_records[: config.safety.max_unfollows_per_run]

    if not eligible_records:
        excluded_count = sum(
            1
            for record in records
            if record.review == "unfollow" and not _has_activity_evidence(record)
        )
        if excluded_count:
            console.print(
                f"Blocked {excluded_count} marked account(s) because their "
                "activity evidence is incomplete. Run a new scan.",
                soft_wrap=True,
            )
        console.print("No reviewed accounts marked for unfollow.")
        return

    _print_unfollow_targets(eligible_records)
    deferred_count = len(all_eligible_records) - len(eligible_records)
    if deferred_count:
        console.print(
            f"{deferred_count} additional marked account(s) are deferred by the "
            "per-run safety limit.",
            soft_wrap=True,
        )

    if dry_run:
        result = execute_unfollows(
            None,
            "dry-run",
            records,
            config.safety,
            dry_run=True,
        )
        console.print(
            f"Dry run: {result.attempted_count} reviewed account(s) would be unfollowed."
        )
        return

    if not yes:
        confirmed = typer.confirm(
            f"Unfollow {len(eligible_records)} reviewed account(s)?",
            default=False,
        )
        if not confirmed:
            console.print("Cancelled.")
            return

    try:
        token = _load_access_token(resolved_app_dir)
        with XApiClient(token) as api:
            me = api.get_me()
            scan_context = Storage(resolved_app_dir).load_scan_context()
            if scan_context is None:
                console.print(
                    "Missing scan account information. Run `x-unfollow scan` again.",
                    soft_wrap=True,
                )
                raise typer.Exit(1)
            if scan_context["source_user_id"] != me.id:
                console.print(
                    "Account mismatch: candidates were scanned for "
                    f"@{scan_context['source_username']}, but the current login is "
                    f"@{me.username}. Run setup and scan for the intended account.",
                    soft_wrap=True,
                )
                raise typer.Exit(1)
            result = execute_unfollows(
                api,
                me.id,
                records,
                config.safety,
                dry_run=False,
            )
    except MissingTokenError as exc:
        _print_cli_error(exc)
        raise typer.Exit(1) from exc
    except OAuthError as exc:
        _print_cli_error(exc)
        raise typer.Exit(1) from exc
    except XApiError as exc:
        _print_api_error(exc)
        raise typer.Exit(1) from exc

    console.print("Unfollow complete.", soft_wrap=True)
    console.print(f"Attempted: {result.attempted_count}", soft_wrap=True)
    console.print(f"Success: {result.success_count}", soft_wrap=True)
    console.print(f"Failed: {result.failed_count}", soft_wrap=True)

    successful_ids = set(getattr(result, "successful_user_ids", ()))
    failures = dict(getattr(result, "failures", ()))
    if successful_ids or failures:
        storage = Storage(resolved_app_dir)
        updated_records = [
            replace(record, review="unfollowed")
            if record.user.id in successful_ids
            else record
            for record in records
        ]
        storage.save_decisions(updated_records)
        attempted_ids = successful_ids | set(failures)
        timestamp = datetime.now(timezone.utc)
        storage.append_unfollow_audit(
            [
                {
                    "timestamp": timestamp,
                    "user_id": record.user.id,
                    "username": record.user.username,
                    "success": record.user.id in successful_ids,
                    "error": failures.get(record.user.id),
                }
                for record in records
                if record.user.id in attempted_ids
            ]
        )
    for user_id, message in failures.items():
        username = next(
            (
                record.user.username
                for record in records
                if record.user.id == user_id
            ),
            user_id,
        )
        console.print(f"Failed @{username}: {message}", soft_wrap=True)


def main() -> None:
    app()
