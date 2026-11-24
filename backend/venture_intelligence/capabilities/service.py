"""Lens views over the capability registry.

A lens is a view, not a security boundary, with one hard rule: the L1
Explorer lens is never offered a capability that serves people-level data.
Lower lenses see teasers (title and one line) for what higher lenses add,
never the capability's CLI command or route.
"""

from __future__ import annotations

from pydantic import BaseModel

from venture_intelligence.capabilities.registry import (
    ApiRoute,
    Capability,
    CapabilityRegistry,
    CapabilityStatus,
    Lens,
    get_capability_registry,
)


class CapabilityView(BaseModel):
    """A capability the requested lens can use (or will, when planned)."""

    id: str
    title: str
    blurb: str
    default_lens: Lens
    cli: str | None
    api: ApiRoute | None
    people_data: bool
    status: CapabilityStatus
    slice: str | None = None


class CapabilityTeaser(BaseModel):
    """What a higher lens adds, shown without its command or route."""

    id: str
    title: str
    teaser: str
    unlock_lens: Lens
    status: CapabilityStatus
    slice: str | None = None


class LensCapabilities(BaseModel):
    lens: Lens
    lens_title: str
    capabilities: list[CapabilityView]
    teasers: list[CapabilityTeaser]


def _visible(item: Capability, lens: Lens) -> bool:
    if item.default_lens.rank > lens.rank:
        return False
    # Defence in depth: the registry already refuses people_data at L1.
    return not (lens is Lens.L1 and item.people_data)


def capabilities_for(
    lens: Lens | str,
    registry: CapabilityRegistry | None = None,
) -> LensCapabilities:
    """Return the capabilities visible to ``lens`` plus teasers for higher lenses."""
    selected = Lens(lens)
    source = registry or get_capability_registry()
    visible: list[CapabilityView] = []
    teasers: list[CapabilityTeaser] = []
    for item in source.capabilities:
        if _visible(item, selected):
            visible.append(
                CapabilityView(
                    id=item.id,
                    title=item.title,
                    blurb=item.blurb,
                    default_lens=item.default_lens,
                    cli=item.cli,
                    api=item.api,
                    people_data=item.people_data,
                    status=item.status,
                    slice=item.slice,
                )
            )
        else:
            teasers.append(
                CapabilityTeaser(
                    id=item.id,
                    title=item.title,
                    teaser=item.teaser,
                    unlock_lens=item.default_lens,
                    status=item.status,
                    slice=item.slice,
                )
            )
    return LensCapabilities(
        lens=selected,
        lens_title=selected.label,
        capabilities=visible,
        teasers=teasers,
    )


def help_hints(
    lens: Lens | str = Lens.L4,
    registry: CapabilityRegistry | None = None,
    *,
    prog: str = "vi",
    exclude: set[str] | None = None,
) -> list[str]:
    """CLI next-step hints for the available capabilities a lens can run."""
    view = capabilities_for(lens, registry)
    skipped = exclude or set()
    return [
        f"{prog} {item.cli}  # {item.blurb}"
        for item in view.capabilities
        if item.status is CapabilityStatus.AVAILABLE
        and item.cli is not None
        and item.id not in skipped
    ]


__all__ = [
    "CapabilityTeaser",
    "CapabilityView",
    "LensCapabilities",
    "capabilities_for",
    "help_hints",
]
