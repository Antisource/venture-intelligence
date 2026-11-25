"""Typed capability registry loaded from ``config/capabilities.yaml``.

The registry is the single source of truth for what the product can do. Each
entry carries a plain-language blurb, the lens it belongs to by default, a
teaser shown to lower lenses, and the CLI command and API route that serve it.

Structural rules (unique ids, people-level data never defaulting to L1, slice
ids on planned entries) are enforced when the registry is parsed. Rules that
need the running interfaces (routes exist in the FastAPI app, commands exist
in the argparse parser) are checked by :func:`validate_registry`, which takes
both as arguments so this module never imports the API or the CLI.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "capabilities.yaml"


class Lens(str, Enum):
    """Product lenses, ordered from least to most detail. Views, not security."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"

    @property
    def rank(self) -> int:
        return int(self.value[1])

    @property
    def label(self) -> str:
        return LENS_TITLES[self]


LENS_TITLES: dict[Lens, str] = {
    Lens.L1: "Explorer",
    Lens.L2: "Analyst",
    Lens.L3: "Partner",
    Lens.L4: "Technical VC",
}


class CapabilityStatus(str, Enum):
    AVAILABLE = "available"
    PLANNED = "planned"


class ApiRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    route: str = Field(pattern=r"^/")


class Capability(BaseModel):
    """One user-visible capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    blurb: str = Field(min_length=1)
    default_lens: Lens
    teaser: str = Field(min_length=1)
    cli: str | None = None
    api: ApiRoute | None = None
    people_data: bool
    status: CapabilityStatus
    slice: str | None = Field(default=None, pattern=r"^S[1-5]$")

    @model_validator(mode="after")
    def _check_rules(self) -> Capability:
        if self.people_data and self.default_lens is Lens.L1:
            raise ValueError(
                f"capability {self.id!r} carries people-level data and cannot default to L1"
            )
        if self.status is CapabilityStatus.PLANNED:
            if self.slice is None:
                raise ValueError(f"planned capability {self.id!r} must name its slice (S1-S5)")
            if self.cli is not None or self.api is not None:
                raise ValueError(
                    f"planned capability {self.id!r} must not advertise a CLI command or "
                    "API route before it exists"
                )
        elif self.slice is not None:
            raise ValueError(f"available capability {self.id!r} must not name a slice")
        return self

    @property
    def available(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE


class CapabilityRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    capabilities: list[Capability]

    @model_validator(mode="after")
    def _unique_ids(self) -> CapabilityRegistry:
        counts = Counter(item.id for item in self.capabilities)
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate capability ids: {', '.join(duplicates)}")
        return self

    def get(self, capability_id: str) -> Capability | None:
        return next((item for item in self.capabilities if item.id == capability_id), None)

    def available(self) -> list[Capability]:
        return [item for item in self.capabilities if item.available]


class RegistryConsistencyError(ValueError):
    """The registry names a route or command that the interfaces do not provide."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("capability registry is inconsistent: " + "; ".join(problems))


def parse_registry(document: Any) -> CapabilityRegistry:
    """Validate a decoded YAML document as a registry."""
    if not isinstance(document, dict) or not isinstance(document.get("capabilities"), list):
        raise ValueError("capabilities.yaml must contain a top-level 'capabilities' list")
    return CapabilityRegistry.model_validate(document)


def load_registry(path: str | Path | None = None) -> CapabilityRegistry:
    with Path(path or DEFAULT_REGISTRY_PATH).open(encoding="utf-8") as handle:
        return parse_registry(yaml.safe_load(handle))


@lru_cache(maxsize=1)
def get_capability_registry() -> CapabilityRegistry:
    return load_registry()


# ── Consistency with the running interfaces ──────────────────────────────────


def api_routes(app: Any) -> set[tuple[str, str]]:
    """Return ``(METHOD, path)`` for every operation in a FastAPI app's OpenAPI schema.

    The OpenAPI document is the published contract (ADR-0007), so a registry
    route must be an operation there, not merely a matchable path.
    """
    paths = app.openapi().get("paths", {})
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() in _HTTP_METHODS
    }


_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next(
        (action for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )


def cli_command_problem(parser: argparse.ArgumentParser, command: str) -> str | None:
    """Explain why ``command`` is not a runnable command of ``parser``, or ``None``.

    A command is a path of sub-command names followed by ``<placeholders>`` for
    positional arguments and ``--options`` (optionally with a value) that the
    final parser defines.
    """
    tokens = command.split()
    if not tokens:
        return "empty command"
    current = parser
    index = 0
    while index < len(tokens):
        subparsers = _subparsers(current)
        if subparsers is None:
            break
        token = tokens[index]
        if token not in subparsers.choices:
            return f"unknown command {' '.join(tokens[: index + 1])!r}"
        current = subparsers.choices[token]
        index += 1
    if _subparsers(current) is not None and _subparsers(current).required:
        return f"command {command!r} is incomplete; it needs a sub-command"
    positionals = [action for action in current._actions if not action.option_strings]
    options = {option: action for action in current._actions for option in action.option_strings}
    placeholders = 0
    expects_value = False
    for token in tokens[index:]:
        if token.startswith("--"):
            action = options.get(token)
            if action is None:
                return f"unknown option {token!r} for {' '.join(tokens[:index])!r}"
            expects_value = action.nargs != 0
        elif token.startswith("<") and token.endswith(">"):
            placeholders += 1
            expects_value = False
        elif expects_value:
            expects_value = False
        else:
            return f"unexpected token {token!r} in {command!r}"
    if placeholders > len(positionals):
        return f"command {command!r} names more arguments than it accepts"
    return None


def consistency_problems(
    registry: CapabilityRegistry,
    *,
    routes: Iterable[tuple[str, str]] | None = None,
    parser: argparse.ArgumentParser | None = None,
) -> list[str]:
    """List every available capability whose route or command does not exist."""
    known_routes = set(routes) if routes is not None else None
    problems: list[str] = []
    for item in registry.available():
        if item.api is not None and known_routes is not None:
            if (item.api.method, item.api.route) not in known_routes:
                problems.append(
                    f"{item.id}: API route {item.api.method} {item.api.route} does not exist"
                )
        if item.cli is not None and parser is not None:
            problem = cli_command_problem(parser, item.cli)
            if problem:
                problems.append(f"{item.id}: {problem}")
    return problems


def validate_registry(
    registry: CapabilityRegistry,
    *,
    routes: Iterable[tuple[str, str]] | None = None,
    parser: argparse.ArgumentParser | None = None,
) -> CapabilityRegistry:
    problems = consistency_problems(registry, routes=routes, parser=parser)
    if problems:
        raise RegistryConsistencyError(problems)
    return registry


__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "LENS_TITLES",
    "ApiRoute",
    "Capability",
    "CapabilityRegistry",
    "CapabilityStatus",
    "Lens",
    "RegistryConsistencyError",
    "api_routes",
    "cli_command_problem",
    "consistency_problems",
    "get_capability_registry",
    "load_registry",
    "parse_registry",
    "validate_registry",
]
