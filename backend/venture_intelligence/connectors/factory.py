"""Connector registration for monitoring and on-demand gathering."""

from venture_intelligence.config import Settings, settings
from venture_intelligence.connectors.directory import DirectoryConnector
from venture_intelligence.connectors.environmental import (
    BlogConnector,
    DiscordConnector,
    NewsletterConnector,
    SocialConnector,
    WebsiteConnector,
)
from venture_intelligence.connectors.public_sources import (
    ArxivConnector,
    GitHubConnector,
    HackerNewsConnector,
    LinkedInConnector,
    ProductHuntConnector,
    WellfoundConnector,
)
from venture_intelligence.connectors.triangulation import TriangulationConnector
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.programs import ProgramRegistry


def build_connectors(
    registry: ProgramRegistry,
    crawler: StealthCrawler | None = None,
    *,
    runtime_settings: Settings = settings,
    share_crawler: bool = True,
) -> list:
    """Create connectors with shared monitor or isolated gather lifecycles."""
    shared_crawler = crawler or (StealthCrawler(runtime_settings) if share_crawler else None)

    def crawler_for_connector() -> StealthCrawler:
        if crawler is not None or share_crawler:
            assert shared_crawler is not None
            return shared_crawler
        return StealthCrawler(runtime_settings)

    return [
        DirectoryConnector(registry, crawler_for_connector()),
        TriangulationConnector(registry, crawler_for_connector()),
        NewsletterConnector(registry, crawler_for_connector()),
        BlogConnector(registry, crawler_for_connector()),
        WebsiteConnector(registry, crawler_for_connector()),
        SocialConnector(
            registry,
            crawler_for_connector(),
            runtime_settings=runtime_settings,
        ),
        DiscordConnector(registry, crawler_for_connector()),
        LinkedInConnector(
            registry,
            crawler_for_connector(),
            runtime_settings=runtime_settings,
        ),
        GitHubConnector(registry, crawler_for_connector()),
        HackerNewsConnector(registry, crawler_for_connector()),
        ArxivConnector(registry, crawler_for_connector()),
        ProductHuntConnector(
            registry,
            crawler_for_connector(),
            runtime_settings=runtime_settings,
        ),
        WellfoundConnector(registry, crawler_for_connector()),
    ]
