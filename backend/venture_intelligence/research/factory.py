"""Shared bounded research construction for API and CLI callers."""

from __future__ import annotations

from venture_intelligence.config import Settings, settings
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.research.agent import ResearchAgent
from venture_intelligence.research.sources import (
    ArxivSearchProvider,
    BingSearchProvider,
    BraveSearchProvider,
    DuckDuckGoSearchProvider,
    GoogleScholarSearchProvider,
    PublicProgramAnnouncementProvider,
    YahooSearchProvider,
)
from venture_intelligence.research.watchlist import SourceSettingsStore


def build_research_agent(
    *,
    program_name: str | None = None,
    program_batch: str | None = None,
    runtime_settings: Settings = settings,
) -> ResearchAgent:
    crawler = StealthCrawler(runtime_settings)
    terms = tuple(item for item in (program_name, program_batch) if item)
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
    if overrides.enabled("public_program_osint", True):
        providers.append(PublicProgramAnnouncementProvider(crawler, program_terms=terms))
    if runtime_settings.research_arxiv_enabled:
        providers.append(ArxivSearchProvider())
    if runtime_settings.research_scholar_enabled:
        providers.append(GoogleScholarSearchProvider(crawler))
    return ResearchAgent(
        providers,
        runtime_settings=runtime_settings,
        knowledge_compiler=KnowledgeCompiler(runtime_settings.knowledge_dir),
    )


__all__ = ["build_research_agent"]
