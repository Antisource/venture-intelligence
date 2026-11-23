from unittest import mock

import pytest
from venture_intelligence.api import app as api_app
from venture_intelligence.api.routers import analyst, notifications


@pytest.mark.parametrize("domain", [analyst, notifications])
def test_mock_patch_through_app_facade_restores_domain_binding(domain):
    original = domain._grounded_analyst_context

    with mock.patch("venture_intelligence.api.app._grounded_analyst_context") as patched:
        assert domain._grounded_analyst_context is patched

    assert vars(domain)["_grounded_analyst_context"] is original
    assert api_app._grounded_analyst_context is original
    assert "_grounded_analyst_context" not in vars(api_app)
