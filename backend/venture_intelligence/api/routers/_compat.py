"""Compatibility facades for modules whose definitions moved into domain routers.

``venture_intelligence.api.app`` and ``venture_intelligence.api.routers.v1`` used
to define every handler, request model, and helper themselves.  Callers (and
tests) still import or monkeypatch those names on the old modules, so a facade
resolves any name it does not define itself from the domain modules, and
forwards assignments to every domain module that binds that name.  Patching
``venture_intelligence.api.app._grounded_analyst_context`` therefore patches the
function the moved code actually calls.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any


class _ForwardingModule(ModuleType):
    _forward_to: tuple[ModuleType, ...] = ()

    def _owners(self, name: str) -> list[ModuleType]:
        if name.startswith("__") or name in vars(self):
            return []
        return [module for module in self._forward_to if name in vars(module)]

    def __getattr__(self, name: str) -> Any:
        owners = self._owners(name)
        if not owners:
            raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")
        return vars(owners[0])[name]

    _deleted_owners: dict[str, list[ModuleType]]

    def __setattr__(self, name: str, value: Any) -> None:
        deleted = self._deleted_owners.pop(name, [])
        owners = self._owners(name) or deleted
        if not owners:
            super().__setattr__(name, value)
            return
        for module in owners:
            setattr(module, name, value)

    def __delattr__(self, name: str) -> None:
        owners = self._owners(name)
        if not owners:
            super().__delattr__(name)
            return
        for module in owners:
            delattr(module, name)
        self._deleted_owners[name] = owners

    def __dir__(self) -> list[str]:
        names = set(super().__dir__())
        for module in self._forward_to:
            names.update(name for name in vars(module) if not name.startswith("__"))
        return sorted(names)


def forward_missing_attributes(module_name: str, *targets: ModuleType) -> None:
    """Make ``module_name`` resolve and patch names defined in ``targets``."""
    module = sys.modules[module_name]
    module.__class__ = type(
        "_ForwardingModule",
        (_ForwardingModule,),
        {"_forward_to": tuple(targets), "_deleted_owners": {}, "__module__": __name__},
    )
