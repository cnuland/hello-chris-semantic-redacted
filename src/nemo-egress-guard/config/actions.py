"""Custom NeMo Guardrails actions for egress verification.

These actions are called by Colang flows to perform concrete checks on
redacted text before it exits the cluster.
"""

from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger("egress_guard.actions")

REDACTION_SERVICE_URL = os.environ.get(
    "EGRESS_GUARD_REDACTION_SERVICE_URL",
    "http://redaction-service.semantic-redacted.svc:8000",
)
SCAN_TIMEOUT = float(os.environ.get("EGRESS_GUARD_REDACTION_SCAN_TIMEOUT_S", "5.0"))

SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:sk|pk|rk)[-_](?:live|test)[-_]\w{20,}", re.IGNORECASE),
    re.compile(r"ghp_\w{36}"),
    re.compile(r"github_pat_\w{22}_\w{59}"),
    re.compile(r"gho_\w{36}"),
    re.compile(r"ghs_\w{36}"),
    re.compile(r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),
    re.compile(
        r"(?:aws_secret_access_key|secret_access_key)\s*[:=]\s*[A-Za-z0-9/+=]{40}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:api[_\-]?key|api[_\-]?secret|token|secret|password|passwd|credentials)"
        r"\s*[:=]\s*[\"']?\S{8,}[\"']?",
        re.IGNORECASE,
    ),
    re.compile(r"[Bb]earer\s+[A-Za-z0-9\-_\.]{20,}"),
    re.compile(r"xox[bpoas]-[0-9]+-[0-9]+-\w+"),
    re.compile(
        r"(?:secret|key|token)\s*[:=]\s*[0-9a-fA-F]{32,}",
        re.IGNORECASE,
    ),
]

PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("CREDIT_CARD", re.compile(
        r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
        r"[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"
    )),
    ("IP_ADDRESS", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    )),
]

PLACEHOLDER_PATTERN = re.compile(r"<[A-Z_]+_\d+>")


async def check_residual_pii(text: str) -> bool:
    """Check for residual PII via redaction-service /scan, falling back to local regex."""
    try:
        async with httpx.AsyncClient(timeout=SCAN_TIMEOUT) as client:
            resp = await client.post(
                f"{REDACTION_SERVICE_URL}/scan",
                json={"text": text, "check_types": ["PII"]},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("clean", True):
                logger.warning("Residual PII found via redaction-service /scan")
                return True
            return False
    except Exception:
        logger.debug("Redaction service /scan unreachable, falling back to local regex")

    for label, pattern in PII_PATTERNS:
        if pattern.search(text):
            logger.warning("Residual PII (%s) found via local regex", label)
            return True
    return False


async def check_secrets(text: str) -> bool:
    """Check for secrets/credentials via regex patterns."""
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            logger.warning("Secret pattern detected in redacted text")
            return True
    return False


async def verify_placeholder_integrity(text: str) -> bool:
    """Verify placeholder substitution consistency.

    Checks that text contains valid placeholders and no obvious
    partial-redaction artifacts.
    """
    placeholders = PLACEHOLDER_PATTERN.findall(text)
    if not placeholders:
        return True

    seen_types: dict[str, set[int]] = {}
    for ph in placeholders:
        inner = ph[1:-1]
        parts = inner.rsplit("_", 1)
        if len(parts) != 2:
            logger.warning("Malformed placeholder: %s", ph)
            return False
        entity_type, idx_str = parts
        try:
            idx = int(idx_str)
        except ValueError:
            logger.warning("Non-numeric placeholder index: %s", ph)
            return False
        seen_types.setdefault(entity_type, set()).add(idx)

    for entity_type, indices in seen_types.items():
        expected = set(range(1, max(indices) + 1))
        if indices != expected:
            logger.warning(
                "Placeholder gap for %s: expected %s, got %s",
                entity_type, expected, indices,
            )
            return False

    return True
