"""Runtime settings for Venture Intelligence.

Program definitions deliberately do not live in this module. They are loaded
from ``config/programs.yaml`` by :mod:`venture_intelligence.programs`, keeping
accelerator-specific data out of application and connector code.
"""

import os

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from venture_intelligence.shared.models import SourceAccessMode


class Settings(BaseSettings):
    """Environment-tunable settings with conservative public-web defaults."""

    model_config = SettingsConfigDict(
        env_file=os.environ.get("VENTURE_INTELLIGENCE_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: str = "data/intelligence.db"
    # PostgreSQL URL (ADR-0001). Unset means the SQLite ``db_path`` store is
    # the only datastore; the Postgres platform is opt-in until domains port.
    database_url: SecretStr | None = None
    programs_config_path: str = "config/programs.yaml"
    intelligence_watchlist_path: str = "config/intelligence_watchlists.yaml"
    knowledge_dir: str = "knowledge"

    # Public-web collection. These controls bound request volume and latency;
    # connectors never attempt to bypass authentication, CAPTCHAs, or paywalls.
    connector_poll_interval: int = 1800
    connector_timeout_seconds: float = 10.0
    gather_timeout_seconds: float = 12.0
    max_searches_per_program: int = 4
    max_response_bytes: int = 1_000_000
    user_agent: str = "venture-intelligence/0.1 (public-web-monitor)"
    crawler_render_enabled: bool = False
    crawler_engine: str = "playwright"
    crawler_failure_threshold: int = 3
    crawler_circuit_cooldown_seconds: float = 60.0
    restricted_social_access_mode: SourceAccessMode = SourceAccessMode.SEARCH_DISCOVERY

    # Bounded free/public research-agent budgets. No provider credentials are
    # needed; the default search adapter uses public HTML discovery only.
    research_max_queries: int = 20
    research_max_depth: int = 3
    research_max_results_per_query: int = 5
    research_max_runtime_seconds: float = 30.0
    research_max_total_requests: int = 30
    research_max_requests_per_provider: int = 12
    research_max_retry_requests: int = 8
    research_max_requests_per_domain: int = 20
    research_cache_ttl_seconds: float = 300.0
    research_arxiv_enabled: bool = True
    research_scholar_enabled: bool = False
    search_duckduckgo_enabled: bool = True
    search_bing_enabled: bool = True
    search_yahoo_enabled: bool = True
    search_brave_enabled: bool = True
    techcrunch_enabled: bool = True
    newsletter_watchlist_enabled: bool = True
    vc_watchlist_enabled: bool = True
    source_settings_path: str = "data/source_settings.json"
    source_state_path: str = "data/source_state.json"
    source_quality_report_path: str = "data/source_quality/latest.json"
    operations_state_path: str = "data/operations/state.json"
    operations_lock_path: str = "data/operations/cycle.lock"
    operations_lock_stale_seconds: float = 21_600.0
    scheduler_interval_hours: float = 12.0
    scheduler_enabled: bool = True

    # Optional integrations.
    github_token: SecretStr | None = None
    product_hunt_api_enabled: bool = False
    product_hunt_access_token: SecretStr | None = None
    product_hunt_known_positive_post_id: str = "139993"
    discord_api_enabled: bool = False
    discord_bot_token: SecretStr | None = None
    discord_authorized_guild_ids: str = ""
    discord_authorized_channel_ids: str = ""
    firecrawl_api_url: str | None = None
    firecrawl_api_key: str | None = None
    ollama_enabled: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:3b"
    ollama_timeout_seconds: float = 20.0
    ollama_input_chars: int = 4_000
    ollama_output_tokens: int = 400

    # Optional conversational analyst provider. ``local`` is the safe default;
    # ``qwen`` uses the configured local Ollama runtime when explicitly enabled.
    analyst_enabled: bool = True
    analyst_provider: str = "auto"

    browser_timeout_ms: int = 30_000

    name_match_threshold: float = 0.82
    alert_min_confidence: float = 0.65
    alert_top_n_rank: int = 100
    slack_webhook_url: str | None = None
    email_config_path: str = "data/email_notifications.json"
    email_smtp_host: str | None = None
    email_smtp_port: int = 587
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_smtp_from: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @field_validator("connector_poll_interval")
    @classmethod
    def minimum_poll_interval(cls, value: int) -> int:
        if value < 60:
            raise ValueError("CONNECTOR_POLL_INTERVAL must be at least 60 seconds")
        return value

    @field_validator("connector_timeout_seconds", "gather_timeout_seconds")
    @classmethod
    def positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeouts must be positive")
        return value

    @field_validator(
        "research_max_queries",
        "research_max_results_per_query",
        "research_max_total_requests",
        "research_max_requests_per_provider",
        "research_max_requests_per_domain",
    )
    @classmethod
    def positive_research_counts(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("research query/result limits must be positive")
        return value

    @field_validator("research_max_retry_requests")
    @classmethod
    def nonnegative_retry_requests(cls, value: int) -> int:
        if value < 0:
            raise ValueError("research retry request limit must be non-negative")
        return value

    @field_validator("research_max_depth")
    @classmethod
    def nonnegative_research_depth(cls, value: int) -> int:
        if value < 0:
            raise ValueError("research depth must be non-negative")
        return value

    @field_validator("research_max_runtime_seconds", "research_cache_ttl_seconds")
    @classmethod
    def positive_research_timeouts(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("research runtime and cache TTL must be positive")
        return value

    @field_validator("scheduler_interval_hours")
    @classmethod
    def bounded_scheduler_interval(cls, value: float) -> float:
        if value <= 0 or value > 168:
            raise ValueError("scheduler interval must be between 0 and 168 hours")
        return value

    @field_validator("operations_lock_stale_seconds")
    @classmethod
    def bounded_lock_staleness(cls, value: float) -> float:
        if value < 300 or value > 604_800:
            raise ValueError("OPERATIONS_LOCK_STALE_SECONDS must be between 300 and 604800")
        return value



settings = Settings()
