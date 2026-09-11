"""Optional Firecrawl integration boundary.

The project does not require Firecrawl. This connector is intentionally absent
from the default factory unless a future, explicitly configured integration is
added with the provider's documented public-web policy.
"""

from venture_intelligence.connectors.base import BaseConnector
from venture_intelligence.shared.models import CanonicalEvent


class FirecrawlConnector(BaseConnector):
    source_name = "firecrawl"

    async def scan(self) -> list[CanonicalEvent]:
        return []

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        return []

    def can_investigate(self, event: CanonicalEvent) -> bool:
        return False

    async def close(self) -> None:
        return None
