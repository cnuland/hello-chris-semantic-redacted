"""Configuration for the guardrails service.

Pydantic settings model with regex patterns for secret/PII detection,
sensitivity level policies, and service endpoint URLs.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Guardrails service configuration loaded from environment variables."""

    model_config = {"env_prefix": "GUARDRAILS_"}

    # --- service identity ---
    service_name: str = "guardrails-service"
    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"

    # --- upstream services ---
    redaction_service_url: str = Field(
        default="http://redaction-service.semantic-redacted.svc:8000",
        description="Base URL of the redaction service (used for /scan calls)",
    )
    qwen_endpoint: str = Field(
        default="http://ollama-qwen36.homelab-maas.svc.cluster.local:11434/v1",
        description="Qwen 3.6 endpoint for future LLM-based rails",
    )

    # --- sensitivity policy ---
    blocked_sensitivity_levels: list[str] = Field(
        default=["CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"],
        description="Sensitivity levels that MUST NOT egress to SaaS models",
    )
    local_model_fallback: str = Field(
        default="qwen3.6-35b-a3b",
        description="Model name to suggest when blocking SaaS routing",
    )

    # --- SaaS model detection ---
    saas_prefixes: list[str] = Field(
        default=["gemini", "gpt", "claude", "openai"],
        description="Model name prefixes that identify SaaS endpoints",
    )

    # --- timeouts ---
    redaction_scan_timeout_s: float = Field(
        default=5.0,
        description="Timeout in seconds for calls to the redaction service /scan endpoint",
    )

    # ------------------------------------------------------------------ #
    # Pattern libraries (class-level constants, not env-configurable)
    # ------------------------------------------------------------------ #

    SECRET_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        # Stripe keys (live and test)
        re.compile(r"(?:sk|pk|rk)[-_](?:live|test)[-_]\w{20,}", re.IGNORECASE),
        # GitHub personal access tokens (classic and fine-grained)
        re.compile(r"ghp_\w{36}"),
        re.compile(r"github_pat_\w{22}_\w{59}"),
        # GitHub OAuth / app tokens
        re.compile(r"gho_\w{36}"),
        re.compile(r"ghs_\w{36}"),
        # JWT tokens (header.payload with optional signature)
        re.compile(r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+"),
        # PEM private keys
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        # AWS access keys
        re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),
        # AWS secret keys (40 char base64ish after known delimiters)
        re.compile(
            r"(?:aws_secret_access_key|secret_access_key)\s*[:=]\s*[A-Za-z0-9/+=]{40}",
            re.IGNORECASE,
        ),
        # Generic api_key / token / secret / password assignments
        re.compile(
            r"(?:api[_\-]?key|api[_\-]?secret|token|secret|password|passwd|credentials)"
            r"\s*[:=]\s*[\"']?\S{8,}[\"']?",
            re.IGNORECASE,
        ),
        # Bearer tokens in headers
        re.compile(r"[Bb]earer\s+[A-Za-z0-9\-_\.]{20,}"),
        # Slack tokens
        re.compile(r"xox[bpoas]-[0-9]+-[0-9]+-\w+"),
        # Generic hex secrets (32+ hex chars after key-like label)
        re.compile(
            r"(?:secret|key|token)\s*[:=]\s*[0-9a-fA-F]{32,}",
            re.IGNORECASE,
        ),
    ]

    PII_PATTERNS: ClassVar[list[tuple[str, re.Pattern[str]]]] = [
        # Email addresses
        ("EMAIL", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
        # US Social Security Numbers
        ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
        # US phone numbers
        ("PHONE", re.compile(
            r"(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
        )),
        # Credit card numbers (Visa, MC, Amex, Discover)
        ("CREDIT_CARD", re.compile(
            r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
            r"[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"
        )),
        # IP addresses (v4)
        ("IP_ADDRESS", re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        )),
    ]
