"""One construction boundary shared by API, CLI, cycles, and scheduler."""

from __future__ import annotations

from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.batch_discovery.service import BatchDiscoveryService
from venture_intelligence.config import Settings, settings
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.research.provider_policy import (
    NEWSLETTER_POLICY,
    TECHCRUNCH_POLICY,
)
from venture_intelligence.research.sources import (
    BingSearchProvider,
    BraveSearchProvider,
    DuckDuckGoSearchProvider,
    PublicWatchlistPageProvider,
    RssSearchProvider,
    YahooSearchProvider,
)
from venture_intelligence.research.watchlist import (
    IntelligenceWatchlist,
    SourceSettingsStore,
    SourceStateStore,
)
from venture_intelligence.storage.db import Database


def build_batch_discovery_service(
    program_id: str | None,
    *,
    database: Database | None = None,
    registry: ProgramRegistry | None = None,
    runtime_settings: Settings = settings,
) -> BatchDiscoveryService:
    """Build the bounded live service without embedding business logic in a caller."""
    selected_registry = registry or get_program_registry()
    if program_id is not None:
        program = selected_registry.get(program_id)
        if program is None or not program.active:
            raise LookupError(f"Unknown active program id: {program_id}")
    selected_database = database or Database(runtime_settings.db_path)
    crawler = StealthCrawler(runtime_settings)
    overrides = SourceSettingsStore(runtime_settings.source_settings_path)
    providers = []
    search_specs = (
        ("duckduckgo", runtime_settings.search_duckduckgo_enabled, DuckDuckGoSearchProvider),
        ("bing", runtime_settings.search_bing_enabled, BingSearchProvider),
        ("yahoo", runtime_settings.search_yahoo_enabled, YahooSearchProvider),
        ("brave", runtime_settings.search_brave_enabled, BraveSearchProvider),
    )
    for source_id, default_enabled, provider_type in search_specs:
        if overrides.enabled(source_id, default_enabled):
            providers.append(provider_type(crawler))

    watchlist = IntelligenceWatchlist.load(runtime_settings.intelligence_watchlist_path)
    state_store = SourceStateStore(runtime_settings.source_state_path)
    techcrunch_entries = [
        item
        for item in watchlist.active("publication")
        if (item.organization or "").casefold() == "techcrunch"
    ]
    if overrides.enabled("techcrunch", runtime_settings.techcrunch_enabled):
        feed_urls = [url for item in techcrunch_entries for url in item.feed_urls]
        if feed_urls:
            providers.append(
                RssSearchProvider(
                    feed_urls,
                    crawler,
                    provider_id="techcrunch",
                    policy=TECHCRUNCH_POLICY,
                    state_store=state_store,
                    source_metadata={
                        url: item.model_dump()
                        for item in techcrunch_entries
                        for url in item.feed_urls
                    },
                )
            )
    newsletter_entries = watchlist.active("newsletter")
    if overrides.enabled("newsletter_watchlist", runtime_settings.newsletter_watchlist_enabled):
        feed_urls = [url for item in newsletter_entries for url in item.feed_urls]
        if feed_urls:
            providers.append(
                RssSearchProvider(
                    feed_urls,
                    crawler,
                    provider_id="newsletter_watchlist",
                    policy=NEWSLETTER_POLICY,
                    state_store=state_store,
                    source_metadata={
                        url: item.model_dump()
                        for item in newsletter_entries
                        for url in item.feed_urls
                    },
                )
            )
    if overrides.enabled("vc_watchlist", runtime_settings.vc_watchlist_enabled):
        providers.append(
            PublicWatchlistPageProvider(
                watchlist.active("vc_firm", "vc_partner"),
                crawler,
                state_store=state_store,
            )
        )
    return BatchDiscoveryService(
        BatchDiscoveryRepository(selected_database),
        providers,
        crawler=crawler,
        registry=selected_registry,
        runtime_settings=runtime_settings,
    )


__all__ = ["build_batch_discovery_service"]
