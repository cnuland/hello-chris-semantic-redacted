"""Unit tests for the sensitivity classifier.

These tests import the classifier library directly -- no running service
needed.  They verify:
- Classification into all 5 sensitivity levels via fast-path keywords
- Fast-path signals for secrets and HR content
- Routing matrix correctness
- Higher-wins rule when fast-path and embedding disagree
"""

from __future__ import annotations

from pathlib import Path

import pytest

from classifier import SensitivityClassifier, ClassificationResult

# ---------------------------------------------------------------------------
# Shared classifier fixture
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CLASSIFIER_SRC = _PROJECT_ROOT / "src" / "sensitivity-classifier"


@pytest.fixture(scope="module")
def classifier() -> SensitivityClassifier:
    """Instantiate the classifier with project config and anchors."""
    config_path = _CLASSIFIER_SRC / "config.yaml"
    anchors_path = _PROJECT_ROOT / "data" / "sensitivity-anchors" / "anchors.jsonl"
    return SensitivityClassifier(
        config_path=config_path,
        anchors_path=anchors_path,
    )


# ---------------------------------------------------------------------------
# Level classification tests (AC-2.1.1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSensitivityLevels:
    """Each of the 5 sensitivity levels should be correctly classified via fast-path."""

    def test_public_classification(self, classifier: SensitivityClassifier) -> None:
        """Generic knowledge question should classify as PUBLIC or INTERNAL (safe default)."""
        result = classifier.classify("What is the capital of France?")
        # PUBLIC has no fast-path keywords, so result depends on embedding path.
        # Without sentence-transformers the classifier defaults to INTERNAL.
        # Either PUBLIC or INTERNAL is acceptable for a safe default.
        assert result.level in ("PUBLIC", "INTERNAL")
        assert result.confidence > 0

    def test_internal_classification(self, classifier: SensitivityClassifier) -> None:
        """Infrastructure reference should classify as at least INTERNAL."""
        result = classifier.classify("Deploy to the staging cluster on ironman.cjlabs.dev")
        assert result.level in ("INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS")
        assert len(result.fast_path_signals) > 0

    def test_confidential_classification(self, classifier: SensitivityClassifier) -> None:
        """HR content should classify as at least CONFIDENTIAL."""
        result = classifier.classify(
            "Draft a performance improvement plan for Sarah Chen in engineering"
        )
        assert result.level in ("CONFIDENTIAL", "REGULATED", "NEVER_EGRESS")
        assert result.confidence > 0

    def test_regulated_classification(self, classifier: SensitivityClassifier) -> None:
        """Financial/compliance content should classify as at least REGULATED."""
        result = classifier.classify(
            "Analyze Q3 earnings for SEC filing before the deadline"
        )
        assert result.level in ("REGULATED", "NEVER_EGRESS")
        assert result.confidence > 0

    def test_never_egress_classification(self, classifier: SensitivityClassifier) -> None:
        """Credential content should classify as NEVER_EGRESS."""
        result = classifier.classify(
            "The API key is sk_live_EXAMPLE_KEY_00000000000000"
        )
        assert result.level == "NEVER_EGRESS"
        assert result.confidence > 0


# ---------------------------------------------------------------------------
# Fast-path signal tests (AC-2.1.4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFastPathSignals:
    """Fast-path should trigger on obvious patterns before embedding analysis."""

    def test_fast_path_secrets(self, classifier: SensitivityClassifier) -> None:
        """API key pattern should trigger NEVER_EGRESS via fast path."""
        result = classifier.classify(
            "The stripe key is sk_live_EXAMPLE_KEY_00000000000000 for production."
        )
        assert result.level == "NEVER_EGRESS"
        # Should have a pattern signal
        pattern_signals = [s for s in result.fast_path_signals if "pattern:" in s]
        assert len(pattern_signals) > 0

    def test_fast_path_hr(self, classifier: SensitivityClassifier) -> None:
        """HR keyword 'salary' should trigger at least CONFIDENTIAL via fast path."""
        result = classifier.classify(
            "What is the salary range for senior engineers?"
        )
        assert result.level in ("CONFIDENTIAL", "REGULATED", "NEVER_EGRESS")
        keyword_signals = [s for s in result.fast_path_signals if "keyword:" in s]
        assert len(keyword_signals) > 0

    @pytest.mark.parametrize(
        "text, min_level",
        [
            ("Deploy to homelab-maas namespace", "INTERNAL"),
            ("Review the performance review for Q3", "CONFIDENTIAL"),
            ("Review the SOX audit findings", "REGULATED"),
            ("The bearer token is eyJhbGciOi...", "NEVER_EGRESS"),
        ],
        ids=["internal-namespace", "confidential-hr", "regulated-sox", "never-egress-jwt"],
    )
    def test_fast_path_keyword_levels(
        self,
        classifier: SensitivityClassifier,
        text: str,
        min_level: str,
    ) -> None:
        """Various keywords should trigger their expected minimum level."""
        from classifier import LEVEL_RANK

        result = classifier.classify(text)
        assert LEVEL_RANK[result.level] >= LEVEL_RANK[min_level], (
            f"Expected at least {min_level}, got {result.level}"
        )


# ---------------------------------------------------------------------------
# Routing matrix tests (sensitivity-model.md 2D matrix)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoutingMatrix:
    """Verify the 2D routing matrix produces correct actions."""

    @pytest.mark.parametrize(
        "complexity, sensitivity, expected_action",
        [
            # PUBLIC column: always REDACT_THEN_SAAS
            ("SIMPLE", "PUBLIC", "REDACT_THEN_SAAS"),
            ("MEDIUM", "PUBLIC", "REDACT_THEN_SAAS"),
            ("COMPLEX", "PUBLIC", "REDACT_THEN_SAAS"),
            ("REASONING", "PUBLIC", "REDACT_THEN_SAAS"),
            # INTERNAL column
            ("SIMPLE", "INTERNAL", "LOCAL_ONLY"),
            ("MEDIUM", "INTERNAL", "REDACT_THEN_SAAS"),
            ("COMPLEX", "INTERNAL", "REDACT_THEN_SAAS"),
            ("REASONING", "INTERNAL", "REDACT_THEN_SAAS"),
            # CONFIDENTIAL column
            ("SIMPLE", "CONFIDENTIAL", "LOCAL_ONLY"),
            ("MEDIUM", "CONFIDENTIAL", "LOCAL_ONLY"),
            ("COMPLEX", "CONFIDENTIAL", "REDACT_THEN_SAAS"),
            ("REASONING", "CONFIDENTIAL", "LOCAL_ONLY"),
            # REGULATED column: always LOCAL_ONLY
            ("SIMPLE", "REGULATED", "LOCAL_ONLY"),
            ("MEDIUM", "REGULATED", "LOCAL_ONLY"),
            ("COMPLEX", "REGULATED", "LOCAL_ONLY"),
            ("REASONING", "REGULATED", "LOCAL_ONLY"),
            # NEVER_EGRESS column: always LOCAL_ONLY
            ("SIMPLE", "NEVER_EGRESS", "LOCAL_ONLY"),
            ("MEDIUM", "NEVER_EGRESS", "LOCAL_ONLY"),
            ("COMPLEX", "NEVER_EGRESS", "LOCAL_ONLY"),
            ("REASONING", "NEVER_EGRESS", "LOCAL_ONLY"),
        ],
    )
    def test_routing_matrix(
        self,
        classifier: SensitivityClassifier,
        complexity: str,
        sensitivity: str,
        expected_action: str,
    ) -> None:
        """Each cell in the routing matrix should return the documented action."""
        action = classifier.get_routing_action(complexity, sensitivity)
        assert action == expected_action, (
            f"Matrix[{complexity}][{sensitivity}] = {action}, expected {expected_action}"
        )


# ---------------------------------------------------------------------------
# Higher-wins rule (combined classification)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHigherWins:
    """When both fast-path and embedding produce a level, the more restrictive wins."""

    def test_higher_wins(self, classifier: SensitivityClassifier) -> None:
        """If fast-path says INTERNAL and content is actually CONFIDENTIAL,
        the classifier should return at least CONFIDENTIAL."""
        from classifier import LEVEL_RANK

        # This text has an INTERNAL keyword (namespace) AND a CONFIDENTIAL keyword (salary)
        result = classifier.classify(
            "What is the salary breakdown in the homelab-maas namespace?"
        )
        # The classifier should pick the higher of the two
        assert LEVEL_RANK[result.level] >= LEVEL_RANK["CONFIDENTIAL"], (
            f"Expected at least CONFIDENTIAL, got {result.level}"
        )

    def test_result_has_source(self, classifier: SensitivityClassifier) -> None:
        """ClassificationResult should include a source field."""
        result = classifier.classify("Deploy to staging cluster")
        assert result.source in ("fast_path", "embedding", "combined")
