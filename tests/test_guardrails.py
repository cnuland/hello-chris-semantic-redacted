"""Integration tests for the guardrails service.

These tests import the FastAPI app factory directly and use Starlette's
TestClient, so they work without a running guardrails service.  However,
they test the full guardrails logic stack (rails, config, patterns) as an
integration unit.

For tests that need the redaction service's /scan endpoint, the PII rail
will fall back to local regex scanning (which is the expected behavior when
the redaction service is unreachable).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import create_app
from config import Settings


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Create settings pointing at unreachable services (forces local fallback)."""
    return Settings(
        redaction_service_url="http://localhost:19999",  # intentionally unreachable
        qwen_endpoint="http://localhost:19998",
    )


@pytest.fixture(scope="module")
def client(settings: Settings) -> TestClient:
    """TestClient for the guardrails app."""
    app = create_app(settings)
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /guard/input — sensitivity rail (AC-1.2.1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGuardInput:
    """Input rail: block sensitive content from SaaS routing."""

    def test_input_public_allowed(self, client: TestClient) -> None:
        """PUBLIC sensitivity + SaaS route should be allowed."""
        resp = client.post(
            "/guard/input",
            json={
                "messages": [{"role": "user", "content": "What is the CAP theorem?"}],
                "sensitivity_level": "PUBLIC",
                "intended_route": "gemini-3.1-pro-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert data["action"] == "ALLOW"

    def test_input_confidential_blocked(self, client: TestClient) -> None:
        """CONFIDENTIAL sensitivity + SaaS route should be blocked."""
        resp = client.post(
            "/guard/input",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "What is Sarah Chen's current salary?",
                    }
                ],
                "sensitivity_level": "CONFIDENTIAL",
                "intended_route": "gemini-3.1-pro-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is False
        assert data["action"] == "BLOCK_SAAS"
        assert "sensitivity_check" in data["rails_triggered"]

    @pytest.mark.parametrize(
        "level",
        ["CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"],
        ids=["confidential", "regulated", "never-egress"],
    )
    def test_input_blocked_levels(self, client: TestClient, level: str) -> None:
        """All blocked sensitivity levels should produce BLOCK_SAAS."""
        resp = client.post(
            "/guard/input",
            json={
                "messages": [{"role": "user", "content": "Some content here."}],
                "sensitivity_level": level,
                "intended_route": "gpt-4o",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is False
        assert data["action"] == "BLOCK_SAAS"

    @pytest.mark.security
    def test_input_secret_blocked(self, client: TestClient) -> None:
        """Text containing an API key should be blocked regardless of sensitivity_level."""
        resp = client.post(
            "/guard/input",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Use the key sk_live_EXAMPLE_KEY_00000000000000 to authenticate.",
                    }
                ],
                "sensitivity_level": "PUBLIC",
                "intended_route": "gemini-3.1-flash-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is False
        assert data["action"] == "BLOCK_SAAS"
        assert "secret_detection" in data["rails_triggered"]


# ---------------------------------------------------------------------------
# POST /guard/retrieval — sensitivity filter (AC-1.2.3)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGuardRetrieval:
    """Retrieval rail: filter RAG chunks by sensitivity."""

    def test_retrieval_filter(self, client: TestClient) -> None:
        """CONFIDENTIAL/REGULATED chunks should be removed from SaaS-bound prompts."""
        resp = client.post(
            "/guard/retrieval",
            json={
                "chunks": [
                    {
                        "text": "Q3 revenue was $4.2M, up 15% YoY.",
                        "metadata": {"sensitivity": "REGULATED"},
                    },
                    {
                        "text": "The team uses Kubernetes for orchestration.",
                        "metadata": {"sensitivity": "PUBLIC"},
                    },
                    {
                        "text": "Employee salary data for Q3.",
                        "metadata": {"sensitivity": "CONFIDENTIAL"},
                    },
                ],
                "intended_route": "gemini-3.1-flash-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed_count"] == 2
        # Only the PUBLIC chunk should remain
        assert len(data["filtered_chunks"]) == 1
        assert data["filtered_chunks"][0]["text"] == "The team uses Kubernetes for orchestration."

    def test_retrieval_public_kept(self, client: TestClient) -> None:
        """PUBLIC and INTERNAL chunks should be kept for SaaS routing."""
        resp = client.post(
            "/guard/retrieval",
            json={
                "chunks": [
                    {
                        "text": "Kubernetes is an open-source platform.",
                        "metadata": {"sensitivity": "PUBLIC"},
                    },
                    {
                        "text": "Our cluster uses OVN-Kubernetes SDN.",
                        "metadata": {"sensitivity": "INTERNAL"},
                    },
                ],
                "intended_route": "gemini-3.1-pro-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed_count"] == 0
        assert len(data["filtered_chunks"]) == 2

    def test_retrieval_local_model_keeps_all(self, client: TestClient) -> None:
        """When routing to a local model, no chunks should be removed."""
        resp = client.post(
            "/guard/retrieval",
            json={
                "chunks": [
                    {
                        "text": "Confidential financial data.",
                        "metadata": {"sensitivity": "REGULATED"},
                    },
                    {
                        "text": "Secret credentials.",
                        "metadata": {"sensitivity": "NEVER_EGRESS"},
                    },
                ],
                "intended_route": "qwen3.6-35b-a3b",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed_count"] == 0
        assert len(data["filtered_chunks"]) == 2


# ---------------------------------------------------------------------------
# POST /guard/output — output scanning (AC-1.2.4)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGuardOutput:
    """Output rail: scan SaaS response for residual PII or secrets."""

    def test_output_clean(self, client: TestClient) -> None:
        """Clean response with no PII should be allowed."""
        resp = client.post(
            "/guard/output",
            json={
                "response_text": (
                    "Based on the analysis, the CAP theorem states that a "
                    "distributed system cannot simultaneously provide all three "
                    "guarantees: consistency, availability, and partition tolerance."
                ),
                "original_sensitivity": "PUBLIC",
                "model_source": "gemini-3.1-pro-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["clean"] is True
        assert data["action"] == "ALLOW"
        assert data["findings"] == []

    def test_output_pii_detected(self, client: TestClient) -> None:
        """Response containing PII (email) should produce findings."""
        resp = client.post(
            "/guard/output",
            json={
                "response_text": (
                    "You can reach the team lead at john.doe@company.com "
                    "or call +1-555-0199."
                ),
                "original_sensitivity": "INTERNAL",
                "model_source": "gemini-3.1-pro-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["clean"] is False
        assert len(data["findings"]) > 0
        finding_types = {f["type"] for f in data["findings"]}
        assert "PII" in finding_types

    @pytest.mark.security
    def test_output_secret_blocked(self, client: TestClient) -> None:
        """Response containing a secret should be blocked."""
        resp = client.post(
            "/guard/output",
            json={
                "response_text": (
                    "To authenticate, use the token: sk_live_EXAMPLE_KEY_00000000000000"
                ),
                "original_sensitivity": "PUBLIC",
                "model_source": "gemini-3.1-flash-preview",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["clean"] is False
        assert data["action"] == "BLOCK"

    def test_output_reconstruction_detected(self, client: TestClient) -> None:
        """If the SaaS model reconstructed a redacted entity, it should be caught."""
        resp = client.post(
            "/guard/output",
            json={
                "response_text": (
                    "Based on the analysis of Sarah Chen's metrics, "
                    "I recommend a 10% raise."
                ),
                "original_sensitivity": "CONFIDENTIAL",
                "model_source": "gemini-3.1-pro-preview",
                "redacted_entities": ["Sarah Chen"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["clean"] is False
        finding_types = {f["type"] for f in data["findings"]}
        assert "RECONSTRUCTION" in finding_types


# ---------------------------------------------------------------------------
# GET /health (AC-1.2.5)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHealthEndpoint:
    """Verify the guardrails health endpoint."""

    def test_health_endpoint(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert isinstance(data["rails_loaded"], list)
        assert len(data["rails_loaded"]) >= 4
        # Should include core rail names
        rail_names = set(data["rails_loaded"])
        assert "input_sensitivity" in rail_names
        assert "input_secrets" in rail_names
        assert "retrieval_filter" in rail_names
        assert "output_scan" in rail_names
        # Backend info present
        assert "llm_backend" in data
