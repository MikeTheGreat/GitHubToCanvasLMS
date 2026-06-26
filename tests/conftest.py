import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def _block_outbound_http(monkeypatch):
    """Prevent any real HTTP requests from leaking out of tests."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    monkeypatch.setattr("requests.put", MagicMock(return_value=mock_response))
