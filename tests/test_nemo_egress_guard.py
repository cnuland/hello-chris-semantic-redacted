"""Integration tests for the NeMo egress guard service.

Tests import the FastAPI app factory directly and use Starlette's
TestClient, so they work without a running service.  NeMo Guardrails
framework loading is optional -- the service gracefully degrades to
standalone egress rails when nemoguardrails is not installed.

NOTE: This test file manages its own sys.path to avoid collisions with
the guardrails-service modules (both have app.py and config.py).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Import the egress guard modules without polluting global sys.path
# ---------------------------------------------------------------------------

_EGRESS_GUARD_SRC = Path(__file__).resolve().parent.parent / "src" / "nemo-egress-guard"

def _import_egress_module(name: str):
    """Import a module from the nemo-egress-guard src directory."""
    old_path = sys.path.copy()
    try:
        sys.path.insert(0, str(_EGRESS_GUARD_SRC))
        if name in sys.modules:
            del sys.modules[name]
        return importlib.import_module(name)
    finally:
        sys.path[:] = old_path

_egress_config = _import_egress_module("config")
_egress_app = _import_egress_module("app")

EgressSettings = _egress_config.Settings
egress_create_app = _egress_app.create_app


@pytest.fixture(scope="module")
def settings() -> EgressSettings:
    return EgressSettings(
        redaction_service_url="http://localhost:19999",
        qwen_endpoint="http://localhost:19998",
        nemo_config_path="/nonexistent",
    )


@pytest.fixture(scope="module")
def client(settings) -> TestClient:
    app = egress_create_app(settings)
    return TestClient(app)


@pytest.mark.integration
class TestEgressGuardApproved:
    """Clean redacted text should be approved."""

    def test_clean_redacted_text_approved(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": (
                    "Analyze the architecture of <PROJECT_1> deployed on <CLUSTER_1>. "
                    "The lead engineer <PERSON_1> designed 5 microservices. "
                    "Contact: <EMAIL_1>."
                ),
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": ["PROJECT", "CLUSTER_NAME", "PERSON", "EMAIL_ADDRESS"],
                "mapping_id": "test-123",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True
        assert data["action"] == "ALLOW"
        assert data["rails_triggered"] == []

    def test_public_text_no_placeholders(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "Explain the CAP theorem in distributed systems.",
                "sensitivity_level": "PUBLIC",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True


@pytest.mark.integration
class TestEgressGuardBlocked:
    """Content with residual PII, secrets, or blocked sensitivity should be blocked."""

    def test_residual_email_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": (
                    "Contact <PERSON_1> at sarah.chen@company.com for details."
                ),
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": ["PERSON"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert data["action"] == "BLOCK"
        assert "egress_pii_verification" in data["rails_triggered"]

    def test_residual_ssn_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "Employee SSN is 123-45-6789.",
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert "egress_pii_verification" in data["rails_triggered"]

    def test_api_key_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "Use this key: sk_live_EXAMPLE_KEY_00000000000000",
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert "egress_secret_scan" in data["rails_triggered"]

    def test_aws_key_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "AWS key: AKIAIOSFODNN7EXAMPLE",
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False

    def test_jwt_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": (
                    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0"
                ),
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False

    def test_never_egress_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "This is perfectly clean text with no PII.",
                "sensitivity_level": "NEVER_EGRESS",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert data["action"] == "BLOCK"
        assert "egress_sensitivity_check" in data["rails_triggered"]
        assert "NEVER_EGRESS" in data["reason"]

    def test_regulated_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "Clean text for SEC filing analysis.",
                "sensitivity_level": "REGULATED",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert "egress_sensitivity_check" in data["rails_triggered"]

    def test_private_key_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "Key material:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK...",
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False


@pytest.mark.integration
class TestEgressGuardHealth:
    """Health endpoint returns service status."""

    def test_health_returns_rails(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "egress_sensitivity_check" in data["rails_loaded"]
        assert "egress_secret_scan" in data["rails_loaded"]
        assert "egress_pii_verification" in data["rails_loaded"]
        assert "egress_placeholder_integrity" in data["rails_loaded"]
        assert isinstance(data["llm_backend"], str)


@pytest.mark.integration
class TestEgressGuardLatency:
    """Verify response includes latency measurement."""

    def test_latency_present(self, client: TestClient) -> None:
        resp = client.post(
            "/guard/egress",
            json={
                "redacted_text": "Simple clean text.",
                "sensitivity_level": "PUBLIC",
                "entity_types_redacted": [],
            },
        )
        data = resp.json()
        assert "latency_ms" in data
        assert isinstance(data["latency_ms"], (int, float))
        assert data["latency_ms"] >= 0
