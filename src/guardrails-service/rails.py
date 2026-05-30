"""Rail implementations for the guardrails service.

Each rail is an independent, testable class with an ``evaluate`` method that
returns a ``RailResult``.  Rails are pure functions over their input context --
no LLM calls, no network I/O (except ``PiiDetectionRail`` which *optionally*
delegates to the redaction service).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from config import Settings

logger = logging.getLogger("guardrails.rails")


# ------------------------------------------------------------------ #
# Shared result type
# ------------------------------------------------------------------ #


@dataclass
class RailResult:
    """Outcome of a single rail evaluation."""

    triggered: bool
    rail_name: str
    action: str  # ALLOW | BLOCK_SAAS | REQUIRE_REDACTION
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ #
# Input rails
# ------------------------------------------------------------------ #


class SensitivityRail:
    """Block requests whose sensitivity level forbids SaaS egress."""

    RAIL_NAME = "sensitivity_check"

    def __init__(self, settings: Settings) -> None:
        self._blocked = set(settings.blocked_sensitivity_levels)
        self._fallback = settings.local_model_fallback

    def evaluate(
        self,
        sensitivity_level: str | None,
        intended_route: str | None = None,
    ) -> RailResult:
        level = (sensitivity_level or "").upper()
        if level in self._blocked:
            return RailResult(
                triggered=True,
                rail_name=self.RAIL_NAME,
                action="BLOCK_SAAS",
                reason=(
                    f"Content classified {level} -- cannot route to SaaS model"
                ),
                details={
                    "sensitivity_level": level,
                    "suggested_route": self._fallback,
                },
            )
        return RailResult(
            triggered=False,
            rail_name=self.RAIL_NAME,
            action="ALLOW",
            reason="Sensitivity level permits SaaS routing",
            details={"sensitivity_level": level},
        )


class SecretDetectionRail:
    """Regex-based credential / secret detection."""

    RAIL_NAME = "secret_detection"

    def __init__(self, settings: Settings) -> None:
        self._patterns = settings.SECRET_PATTERNS

    def evaluate(self, text: str) -> RailResult:
        matched: list[str] = []
        for pattern in self._patterns:
            if pattern.search(text):
                matched.append(pattern.pattern)

        if matched:
            return RailResult(
                triggered=True,
                rail_name=self.RAIL_NAME,
                action="BLOCK_SAAS",
                reason=(
                    "Credential or secret material detected -- content MUST NOT "
                    "leave the cluster"
                ),
                details={"patterns_matched": len(matched)},
            )
        return RailResult(
            triggered=False,
            rail_name=self.RAIL_NAME,
            action="ALLOW",
            reason="No secret patterns detected",
        )


class PiiDetectionRail:
    """Basic PII pattern matching (local regex).

    Optionally calls the redaction service ``/scan`` endpoint when the URL is
    configured and reachable.  Falls back to local patterns on timeout / error.
    """

    RAIL_NAME = "pii_detection"

    def __init__(self, settings: Settings) -> None:
        self._patterns = settings.PII_PATTERNS
        self._scan_url = f"{settings.redaction_service_url}/scan"
        self._timeout = settings.redaction_scan_timeout_s

    async def evaluate(self, text: str) -> RailResult:
        # Try remote scan first
        pii_types = await self._remote_scan(text)

        # Fall back to local regex if remote unavailable
        if pii_types is None:
            pii_types = self._local_scan(text)

        if pii_types:
            return RailResult(
                triggered=True,
                rail_name=self.RAIL_NAME,
                action="REQUIRE_REDACTION",
                reason=(
                    f"PII detected ({', '.join(pii_types)}) -- requires "
                    "redaction before SaaS routing"
                ),
                details={"pii_types": pii_types},
            )
        return RailResult(
            triggered=False,
            rail_name=self.RAIL_NAME,
            action="ALLOW",
            reason="No PII patterns detected",
        )

    async def _remote_scan(self, text: str) -> list[str] | None:
        """Call the redaction service /scan endpoint.  Returns None on failure."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self._scan_url,
                    json={"text": text, "check_types": ["PII", "SECRETS"]},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("clean", True):
                    return [
                        f.get("entity_type", "UNKNOWN")
                        for f in data.get("findings", [])
                    ]
                return []
        except Exception:
            logger.debug(
                "Redaction service /scan unreachable -- falling back to local PII regex"
            )
            return None

    def _local_scan(self, text: str) -> list[str]:
        """Regex-only PII check (fallback)."""
        found: list[str] = []
        for label, pattern in self._patterns:
            if pattern.search(text):
                found.append(label)
        return found


# ------------------------------------------------------------------ #
# Retrieval rail
# ------------------------------------------------------------------ #


class RetrievalFilterRail:
    """Remove RAG chunks whose sensitivity exceeds the SaaS threshold."""

    RAIL_NAME = "retrieval_sensitivity_filter"

    def __init__(self, settings: Settings) -> None:
        self._blocked = set(settings.blocked_sensitivity_levels)
        self._saas_prefixes = [p.lower() for p in settings.saas_prefixes]

    def evaluate(
        self,
        chunks: list[dict[str, Any]],
        intended_route: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
        """Filter chunks by sensitivity metadata.

        Returns
        -------
        filtered : list[dict]
            Chunks safe to include.
        removed_count : int
            Number of chunks removed.
        removal_reasons : list[dict]
            Per-chunk removal reason for audit.
        """
        # If routing to a local model, all chunks are fine
        if intended_route and not self._is_saas(intended_route):
            return chunks, 0, []

        filtered: list[dict[str, Any]] = []
        removal_reasons: list[dict[str, Any]] = []

        for idx, chunk in enumerate(chunks):
            level = (
                chunk.get("metadata", {}).get("sensitivity", "PUBLIC").upper()
            )
            if level in self._blocked:
                removal_reasons.append(
                    {
                        "chunk_index": idx,
                        "reason": (
                            f"{level} content cannot be attached to "
                            "SaaS-bound prompt"
                        ),
                    }
                )
            else:
                filtered.append(chunk)

        return filtered, len(removal_reasons), removal_reasons

    def _is_saas(self, model: str) -> bool:
        model_lower = model.lower()
        return any(model_lower.startswith(p) for p in self._saas_prefixes)


# ------------------------------------------------------------------ #
# Output rails
# ------------------------------------------------------------------ #


class OutputScanRail:
    """Scan SaaS model response text for residual PII or secrets."""

    RAIL_NAME = "output_scan"

    def __init__(self, settings: Settings) -> None:
        self._secret_patterns = settings.SECRET_PATTERNS
        self._pii_patterns = settings.PII_PATTERNS

    def evaluate(self, response_text: str) -> RailResult:
        findings: list[dict[str, str]] = []

        # Check secrets
        for pattern in self._secret_patterns:
            if pattern.search(response_text):
                findings.append(
                    {"type": "SECRET", "pattern": pattern.pattern}
                )

        # Check PII
        for label, pattern in self._pii_patterns:
            if pattern.search(response_text):
                findings.append({"type": "PII", "entity_type": label})

        if findings:
            action = "BLOCK" if any(f["type"] == "SECRET" for f in findings) else "RE_REDACT"
            return RailResult(
                triggered=True,
                rail_name=self.RAIL_NAME,
                action=action,
                reason=(
                    f"Output contains sensitive content: "
                    f"{len(findings)} finding(s)"
                ),
                details={"findings": findings},
            )
        return RailResult(
            triggered=False,
            rail_name=self.RAIL_NAME,
            action="ALLOW",
            reason="Output scan clean",
        )


class ReconstructionDetectionRail:
    """Detect whether the SaaS model reconstructed redacted entity names.

    Compares the response text against a list of original entity values that
    were redacted before sending to SaaS.  If any original value appears in the
    response, the model has reconstructed (or guessed) the redacted entity.
    """

    RAIL_NAME = "reconstruction_detection"

    def evaluate(
        self,
        response_text: str,
        redacted_entities: list[str] | None = None,
    ) -> RailResult:
        if not redacted_entities:
            return RailResult(
                triggered=False,
                rail_name=self.RAIL_NAME,
                action="ALLOW",
                reason="No redacted entities list provided -- skipping reconstruction check",
            )

        reconstructed: list[str] = []
        text_lower = response_text.lower()
        for entity in redacted_entities:
            if entity.lower() in text_lower:
                # Don't log the actual entity value (it's sensitive)
                reconstructed.append("***")

        if reconstructed:
            return RailResult(
                triggered=True,
                rail_name=self.RAIL_NAME,
                action="RE_REDACT",
                reason=(
                    f"SaaS model reconstructed {len(reconstructed)} redacted "
                    "entity/entities -- re-redaction required"
                ),
                details={"reconstructed_count": len(reconstructed)},
            )
        return RailResult(
            triggered=False,
            rail_name=self.RAIL_NAME,
            action="ALLOW",
            reason="No reconstructed entities detected in output",
        )
