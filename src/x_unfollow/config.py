from __future__ import annotations

import os
from pathlib import Path

import tomllib
from typing import Any

try:
    import tomli_w
except ModuleNotFoundError:
    tomli_w = None

from x_unfollow.models import (
    ApiConfig,
    AppConfig,
    CombinationMode,
    RuleConfig,
    SafetyConfig,
)


ENV_OVERRIDES = {
    ("rules", "own_post_threshold_days"): "X_UNFOLLOW_OWN_POST_THRESHOLD_DAYS",
    ("rules", "reply_threshold_days"): "X_UNFOLLOW_REPLY_THRESHOLD_DAYS",
    ("rules", "combination"): "X_UNFOLLOW_COMBINATION",
    ("rules", "count_retweets_as_activity"): "X_UNFOLLOW_COUNT_RETWEETS_AS_ACTIVITY",
    (
        "rules",
        "count_quote_posts_as_own_posts",
    ): "X_UNFOLLOW_COUNT_QUOTE_POSTS_AS_OWN_POSTS",
    (
        "safety",
        "require_review_before_unfollow",
    ): "X_UNFOLLOW_REQUIRE_REVIEW_BEFORE_UNFOLLOW",
    ("safety", "max_unfollows_per_run"): "X_UNFOLLOW_MAX_UNFOLLOWS_PER_RUN",
    ("api", "page_size_following"): "X_UNFOLLOW_PAGE_SIZE_FOLLOWING",
    ("api", "page_size_tweets"): "X_UNFOLLOW_PAGE_SIZE_TWEETS",
    ("api", "max_tweet_pages_per_user"): "X_UNFOLLOW_MAX_TWEET_PAGES_PER_USER",
    ("api", "max_accounts_per_scan"): "X_UNFOLLOW_MAX_ACCOUNTS_PER_SCAN",
    ("api", "max_scan_cost_usd"): "X_UNFOLLOW_MAX_SCAN_COST_USD",
}


def default_config_dict() -> dict:
    return {
        "rules": {
            "own_post_threshold_days": 180,
            "reply_threshold_days": 180,
            "combination": "and",
            "count_retweets_as_activity": False,
            "count_quote_posts_as_own_posts": True,
        },
        "safety": {
            "require_review_before_unfollow": True,
            "max_unfollows_per_run": 50,
        },
        "api": {
            "page_size_following": 1000,
            "page_size_tweets": 5,
            "max_tweet_pages_per_user": 1,
            "max_accounts_per_scan": 10,
            "max_scan_cost_usd": 0.50,
        },
}


def default_app_dir() -> Path:
    override = os.environ.get("X_UNFOLLOW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".x-unfollow"


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _dumps_toml(data: dict[str, dict[str, Any]]) -> str:
    if tomli_w is not None:
        return tomli_w.dumps(data)

    sections = []
    for section, values in data.items():
        lines = [f"[{section}]"]
        lines.extend(f"{key} = {_format_toml_value(value)}" for key, value in values.items())
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"Expected boolean value, got {value!r}")


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dumps_toml(default_config_dict()), encoding="utf-8")


def write_config(path: Path, config: AppConfig) -> None:
    data = {
        "rules": {
            "own_post_threshold_days": config.rules.own_post_threshold_days,
            "reply_threshold_days": config.rules.reply_threshold_days,
            "combination": config.rules.combination.value,
            "count_retweets_as_activity": config.rules.count_retweets_as_activity,
            "count_quote_posts_as_own_posts": (
                config.rules.count_quote_posts_as_own_posts
            ),
        },
        "safety": {
            "require_review_before_unfollow": (
                config.safety.require_review_before_unfollow
            ),
            "max_unfollows_per_run": config.safety.max_unfollows_per_run,
        },
        "api": {
            "page_size_following": config.api.page_size_following,
            "page_size_tweets": config.api.page_size_tweets,
            "max_tweet_pages_per_user": config.api.max_tweet_pages_per_user,
            "max_accounts_per_scan": config.api.max_accounts_per_scan,
            "max_scan_cost_usd": config.api.max_scan_cost_usd,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dumps_toml(data), encoding="utf-8")


def _merged_config_data(path: Path) -> dict[str, dict[str, Any]]:
    data = default_config_dict()
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        for section, values in raw.items():
            if isinstance(values, dict):
                data.setdefault(section, {}).update(values)

    for (section, key), env_name in ENV_OVERRIDES.items():
        if env_name in os.environ:
            data.setdefault(section, {})[key] = os.environ[env_name]

    return data


def load_config(path: Path | None = None) -> AppConfig:
    path = path or default_app_dir() / "config.toml"
    raw = _merged_config_data(path)
    rules_raw = raw.get("rules", {})
    safety_raw = raw.get("safety", {})
    api_raw = raw.get("api", {})

    return AppConfig(
        rules=RuleConfig(
            own_post_threshold_days=int(
                rules_raw.get("own_post_threshold_days", 180)
            ),
            reply_threshold_days=int(rules_raw.get("reply_threshold_days", 180)),
            combination=CombinationMode(rules_raw.get("combination", "and")),
            count_retweets_as_activity=_parse_bool(
                rules_raw.get("count_retweets_as_activity", False)
            ),
            count_quote_posts_as_own_posts=_parse_bool(
                rules_raw.get("count_quote_posts_as_own_posts", True)
            ),
        ),
        safety=SafetyConfig(
            require_review_before_unfollow=_parse_bool(
                safety_raw.get("require_review_before_unfollow", True)
            ),
            max_unfollows_per_run=int(safety_raw.get("max_unfollows_per_run", 50)),
        ),
        api=ApiConfig(
            page_size_following=int(api_raw.get("page_size_following", 1000)),
            page_size_tweets=int(api_raw.get("page_size_tweets", 5)),
            max_tweet_pages_per_user=int(
                api_raw.get("max_tweet_pages_per_user", 1)
            ),
            max_accounts_per_scan=int(api_raw.get("max_accounts_per_scan", 10)),
            max_scan_cost_usd=float(api_raw.get("max_scan_cost_usd", 0.50)),
        ),
    )
