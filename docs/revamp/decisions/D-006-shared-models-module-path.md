# D-006: Shared model submodules keep the historical `__module__`

- **Date:** 2026-11-20
- **Area:** Shared models package module path
- **Differs from owner instruction:** No (implements S0)

1. **Chosen:** each module in `backend/venture_intelligence/shared/models/`
   sets `__name__ = "venture_intelligence.shared.models"` after its imports,
   so every class keeps the `__module__` it had in the former single-file
   `shared/models.py`. The package `__init__.py` re-exports every name, with
   an `__all__` that keeps `import *` to the former module's public names
   (`tests/test_shared_models_star_import.py`).
2. **Owner instruction:** split the hotspot file with zero behaviour change.
3. **Why:** `SourceAccessMode` also exists in `batch_discovery.models`, and
   FastAPI names the colliding OpenAPI component from `__module__`
   (`venture_intelligence__shared__models__SourceAccessMode`). Pydantic
   builds core-schema refs from `__module__` at class creation, so assigning
   `__module__` afterwards would not preserve them. The models modules do
   not log, so the overridden `__name__` affects no logger names.
   **Trade-off:** `__module__`-based introspection points at the package,
   not the defining submodule. Renaming the colliding enum would change the public API
   schema and is deferred to a deliberate versioned API change.
4. **Parity:** `frontend/src/api/openapi.json` is byte-identical; model
   JSON schemas, enum members and `__module__` values are unchanged.
