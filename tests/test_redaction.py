"""Unit tests for the redaction service.

These tests import the FastAPI app directly and use Starlette's TestClient,
so they work WITHOUT a running service.  They verify:
- Entity detection (built-in and custom recognizers)
- Deterministic pseudonymization
- Redact / restore round-trip
- Scan endpoint (clean vs dirty)
- Health endpoint
- Mapping lifecycle (not persisted after restore)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Import the redaction service's app explicitly from its directory.
import importlib
import sys
from pathlib import Path

_redaction_dir = str(Path(__file__).resolve().parent.parent / "src" / "redaction-service")
if _redaction_dir not in sys.path:
    sys.path.insert(0, _redaction_dir)

# Force-reload sub-modules the redaction app depends on so they pick up
# the latest source (avoids stale sys.modules entries when running the
# full test suite alongside guardrails tests).
for _mod_name in ("recognizers", "pseudonymizer", "redaction_app_module"):
    if _mod_name in sys.modules:
        del sys.modules[_mod_name]

# Force-load the redaction app module from the correct path.
_spec = importlib.util.spec_from_file_location(
    "redaction_app_module",
    Path(_redaction_dir) / "app.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules["redaction_app_module"] = _mod
redaction_app = _mod.app


# ---------------------------------------------------------------------------
# Shared client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(redaction_app)


# ---------------------------------------------------------------------------
# /redact endpoint — built-in Presidio recognizers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedactBuiltinEntities:
    """Verify built-in Presidio recognizer coverage (AC-1.1.5)."""

    def test_redact_person_name(self, client: TestClient) -> None:
        """PERSON entity: 'Sarah Chen' should be replaced with <PERSON_1>."""
        resp = client.post(
            "/redact",
            json={"text": "Please review Sarah Chen's performance."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redaction_applied"] is True
        assert "<PERSON_1>" in data["redacted_text"]
        assert "Sarah Chen" not in data["redacted_text"]

    def test_redact_email(self, client: TestClient) -> None:
        """EMAIL_ADDRESS entity: email should be pseudonymized."""
        resp = client.post(
            "/redact",
            json={"text": "Contact sarah@company.com for details."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redaction_applied"] is True
        assert "sarah@company.com" not in data["redacted_text"]
        # Verify an EMAIL placeholder exists
        entity_types = [e["type"] for e in data["entities"]]
        assert "EMAIL_ADDRESS" in entity_types

    def test_redact_phone(self, client: TestClient) -> None:
        """PHONE_NUMBER entity: phone number should be pseudonymized."""
        resp = client.post(
            "/redact",
            json={"text": "Call me at 212-555-0199 to discuss the deal."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redaction_applied"] is True
        entity_types = [e["type"] for e in data["entities"]]
        assert "PHONE_NUMBER" in entity_types
        assert "212-555-0199" not in data["redacted_text"]

    @pytest.mark.parametrize(
        "text, expected_types",
        [
            (
                "Sarah Chen (sarah@company.com) works on Project Phoenix.",
                {"PERSON", "EMAIL_ADDRESS"},
            ),
            (
                "John at 192.168.1.100 sent a payment with card 4111 1111 1111 1111.",
                {"PERSON", "IP_ADDRESS", "CREDIT_CARD"},
            ),
        ],
        ids=["person-email-phone", "person-ip-cc"],
    )
    def test_redact_multiple_entities(
        self, client: TestClient, text: str, expected_types: set[str]
    ) -> None:
        """Multiple entity types in one request should all be detected (AC-1.1.5)."""
        resp = client.post("/redact", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["redaction_applied"] is True
        detected = {e["type"] for e in data["entities"]}
        for et in expected_types:
            assert et in detected, f"Expected entity type {et} not found in {detected}"


# ---------------------------------------------------------------------------
# /redact endpoint — custom recognizers (AC-1.1.6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedactCustomEntities:
    """Verify custom recognizer coverage (AC-1.1.6)."""

    def test_custom_cluster_name(self, client: TestClient) -> None:
        """CLUSTER_NAME: '*.cjlabs.dev' domains should be detected."""
        resp = client.post(
            "/redact",
            json={"text": "Deploy to the cluster ironman.cjlabs.dev now."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redaction_applied"] is True
        assert "ironman.cjlabs.dev" not in data["redacted_text"]
        entity_types = [e["type"] for e in data["entities"]]
        assert "CLUSTER_NAME" in entity_types

    def test_custom_namespace(self, client: TestClient) -> None:
        """K8S_NAMESPACE: known namespace names should be detected."""
        resp = client.post(
            "/redact",
            json={
                "text": "Check pods in the homelab-maas namespace.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redaction_applied"] is True
        assert "homelab-maas" not in data["redacted_text"]
        entity_types = [e["type"] for e in data["entities"]]
        assert "K8S_NAMESPACE" in entity_types

    def test_custom_employee_id(self, client: TestClient) -> None:
        """EMPLOYEE_ID: 'EMP-12345' should be detected."""
        resp = client.post(
            "/redact",
            json={
                "text": "Employee EMP-12345 is scheduled for review.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redaction_applied"] is True
        assert "EMP-12345" not in data["redacted_text"]
        entity_types = [e["type"] for e in data["entities"]]
        assert "EMPLOYEE_ID" in entity_types


# ---------------------------------------------------------------------------
# Deterministic pseudonymization (AC-1.1.9)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeterministicMapping:
    """Same entity appearing multiple times must always get the same placeholder."""

    def test_redact_deterministic(self, client: TestClient) -> None:
        """Same name mentioned twice should produce the same placeholder."""
        resp = client.post(
            "/redact",
            json={
                "text": (
                    "Sarah Chen submitted the report. "
                    "Later, Sarah Chen presented the findings."
                ),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Both occurrences should be replaced
        assert "Sarah Chen" not in data["redacted_text"]
        # The placeholder should appear exactly twice
        assert data["redacted_text"].count("<PERSON_1>") == 2


# ---------------------------------------------------------------------------
# /restore round-trip (AC-1.1.1 + AC-1.1.2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRestoreRoundTrip:
    """Redact then restore should return the original text."""

    def test_restore_round_trip(self, client: TestClient) -> None:
        original = "Contact Sarah Chen at sarah@company.com for Project Phoenix details."
        # Step 1: redact
        redact_resp = client.post("/redact", json={"text": original})
        assert redact_resp.status_code == 200
        redact_data = redact_resp.json()
        assert redact_data["redaction_applied"] is True
        mapping_id = redact_data["mapping_id"]
        assert mapping_id  # non-empty

        # Step 2: restore
        restore_resp = client.post(
            "/restore",
            json={
                "text": redact_data["redacted_text"],
                "mapping_id": mapping_id,
            },
        )
        assert restore_resp.status_code == 200
        restore_data = restore_resp.json()
        assert restore_data["restored_text"] == original
        assert restore_data["mapping_deleted"] is True


# ---------------------------------------------------------------------------
# /scan endpoint (AC-1.1.3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScanEndpoint:
    """Verify the /scan endpoint for clean and dirty text."""

    def test_scan_clean_text(self, client: TestClient) -> None:
        """Generic question with no PII should be clean."""
        resp = client.post(
            "/scan",
            json={
                "text": "Explain distributed consensus algorithms.",
                "check_types": ["PII", "SECRETS", "INTERNAL_REFS"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["clean"] is True
        assert data["findings"] == []

    def test_scan_dirty_text(self, client: TestClient) -> None:
        """Text with PII should report findings."""
        resp = client.post(
            "/scan",
            json={
                "text": "Email john.doe@example.com or call +1-555-9876.",
                "check_types": ["PII"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["clean"] is False
        assert len(data["findings"]) > 0
        finding_types = {f["type"] for f in data["findings"]}
        assert "EMAIL_ADDRESS" in finding_types or "PHONE_NUMBER" in finding_types


# ---------------------------------------------------------------------------
# /health endpoint (AC-1.1.4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthEndpoint:
    """Verify the health endpoint returns recognizer inventory."""

    def test_health_endpoint(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["recognizers_loaded"] > 0
        assert data["custom_recognizers"] > 0
        # At least the 5 custom recognizers we registered
        assert data["custom_recognizers"] >= 5


# ---------------------------------------------------------------------------
# Mapping lifecycle (AC-1.1.8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMappingLifecycle:
    """After restore, the mapping should be deleted (AC-1.1.8)."""

    def test_mapping_not_persisted(self, client: TestClient) -> None:
        """Using the same mapping_id twice should fail with 404."""
        # Redact
        redact_resp = client.post(
            "/redact",
            json={"text": "Sarah Chen is the contact."},
        )
        mapping_id = redact_resp.json()["mapping_id"]

        # First restore succeeds
        restore_resp = client.post(
            "/restore",
            json={
                "text": "<PERSON_1> is the contact.",
                "mapping_id": mapping_id,
            },
        )
        assert restore_resp.status_code == 200

        # Second restore with same mapping_id should fail
        restore_resp2 = client.post(
            "/restore",
            json={
                "text": "<PERSON_1> is the contact.",
                "mapping_id": mapping_id,
            },
        )
        assert restore_resp2.status_code == 404
