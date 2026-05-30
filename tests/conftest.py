"""Shared pytest fixtures and configuration for the semantic-redacted test suite.

Fixtures provide:
- URL endpoints for each service (overridable via env vars)
- async httpx client for integration / e2e tests
- sys.path manipulation so unit tests can import service modules directly
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path setup: allow direct imports from src/ subdirectories
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"

# Redaction service modules (app, pseudonymizer, recognizers)
_redaction_src = _SRC / "redaction-service"
if str(_redaction_src) not in sys.path:
    sys.path.insert(0, str(_redaction_src))

# Guardrails service modules (app, config, rails)
_guardrails_src = _SRC / "guardrails-service"
if str(_guardrails_src) not in sys.path:
    sys.path.insert(0, str(_guardrails_src))

# Sensitivity classifier (importable as a package or direct module)
_classifier_src = _SRC / "sensitivity-classifier"
if str(_classifier_src) not in sys.path:
    sys.path.insert(0, str(_classifier_src))

# NeMo egress guard modules are NOT added globally because they have
# the same module names (app, config) as the guardrails service.
# The egress guard test file manages its own sys.path.

# Also add the parent so `from sensitivity_classifier import ...` works
_classifier_pkg_parent = _SRC
if str(_classifier_pkg_parent) not in sys.path:
    sys.path.insert(0, str(_classifier_pkg_parent))


# ---------------------------------------------------------------------------
# Custom pytest markers
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: unit tests (no services required)")
    config.addinivalue_line("markers", "integration: integration tests (live services)")
    config.addinivalue_line("markers", "e2e: end-to-end scenario tests (all services deployed)")
    config.addinivalue_line("markers", "security: security-focused tests")


# ---------------------------------------------------------------------------
# URL fixtures (env-var overridable for CI / in-cluster testing)
# ---------------------------------------------------------------------------


@pytest.fixture
def redaction_url() -> str:
    """Base URL for the redaction service."""
    return os.environ.get("REDACTION_SERVICE_URL", "http://localhost:8000")


@pytest.fixture
def guardrails_url() -> str:
    """Base URL for the guardrails service."""
    return os.environ.get("GUARDRAILS_SERVICE_URL", "http://localhost:8001")


@pytest.fixture
def egress_guard_url() -> str:
    """Base URL for the NeMo egress guard service."""
    return os.environ.get("EGRESS_GUARD_URL", "http://localhost:8003")


@pytest.fixture
def local_model_url() -> str:
    """Base URL for the local LLM (Ollama / Qwen)."""
    return os.environ.get("LOCAL_MODEL_URL", "http://localhost:11434")


# ---------------------------------------------------------------------------
# Async httpx client for integration / e2e tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def httpx_client():
    """Yield an async httpx client with a reasonable timeout."""
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client
