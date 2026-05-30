"""NeMo Egress Guard -- final egress checkpoint for redacted content.

Uses the real nemoguardrails framework with Colang flows to verify that
redacted text is safe to send to external SaaS models.  This service sits
between the redaction step and the SaaS call in the REDACT_THEN_SAAS flow.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from config import Settings

# ------------------------------------------------------------------ #
# Structured JSON logging
# ------------------------------------------------------------------ #


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "service": "nemo-egress-guard",
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            log_entry.update(record.extra)  # type: ignore[arg-type]
        return json.dumps(log_entry, default=str)


def _configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


# ------------------------------------------------------------------ #
# Pydantic models
# ------------------------------------------------------------------ #


class EgressGuardRequest(BaseModel):
    redacted_text: str
    sensitivity_level: str = Field(default="INTERNAL")
    entity_types_redacted: list[str] = Field(default_factory=list)
    mapping_id: str = Field(default="")


class EgressGuardResponse(BaseModel):
    approved: bool
    action: str
    reason: str
    rails_triggered: list[str]
    latency_ms: float


class HealthResponse(BaseModel):
    status: str
    nemo_available: bool
    rails_loaded: list[str]
    llm_backend: str
    llm_reachable: bool | None = None


# ------------------------------------------------------------------ #
# Egress verification logic (standalone, no nemoguardrails import needed)
# ------------------------------------------------------------------ #

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

BLOCKED_LEVELS = {"NEVER_EGRESS", "REGULATED"}


def _check_secrets(text: str) -> list[str]:
    """Return list of matched secret pattern names."""
    matched = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            matched.append(pattern.pattern[:40])
    return matched


def _check_pii(text: str) -> list[str]:
    """Return list of detected PII types."""
    found = []
    for label, pattern in PII_PATTERNS:
        if pattern.search(text):
            found.append(label)
    return found


def _check_placeholder_integrity(text: str) -> bool:
    """Verify placeholder format consistency."""
    placeholders = PLACEHOLDER_PATTERN.findall(text)
    if not placeholders:
        return True

    for ph in placeholders:
        inner = ph[1:-1]
        parts = inner.rsplit("_", 1)
        if len(parts) != 2:
            return False
        try:
            int(parts[1])
        except ValueError:
            return False
    return True


def evaluate_egress(
    redacted_text: str,
    sensitivity_level: str,
    entity_types_redacted: list[str],
) -> tuple[bool, str, str, list[str]]:
    """Run all egress rails and return (approved, action, reason, triggered)."""
    triggered: list[str] = []

    if sensitivity_level.upper() in BLOCKED_LEVELS:
        triggered.append("egress_sensitivity_check")
        return (
            False,
            "BLOCK",
            f"Content classified {sensitivity_level} -- egress prohibited",
            triggered,
        )

    secrets = _check_secrets(redacted_text)
    if secrets:
        triggered.append("egress_secret_scan")
        return (
            False,
            "BLOCK",
            f"Secret/credential material detected ({len(secrets)} pattern(s))",
            triggered,
        )

    pii_types = _check_pii(redacted_text)
    if pii_types:
        triggered.append("egress_pii_verification")
        return (
            False,
            "BLOCK",
            f"Residual PII detected: {', '.join(pii_types)}",
            triggered,
        )

    if not _check_placeholder_integrity(redacted_text):
        triggered.append("egress_placeholder_integrity")
        return (
            False,
            "BLOCK",
            "Placeholder integrity check failed -- redaction may be incomplete",
            triggered,
        )

    return (True, "ALLOW", "All egress rails passed", triggered)


# ------------------------------------------------------------------ #
# NeMo Guardrails integration (optional -- graceful degradation)
# ------------------------------------------------------------------ #

_nemo_rails = None
_nemo_available = False


def _try_load_nemo(config_path: str, qwen_endpoint: str) -> None:
    """Attempt to load NeMo Guardrails. Falls back to standalone rails."""
    global _nemo_rails, _nemo_available
    try:
        from nemoguardrails import RailsConfig, LLMRails

        os.environ.setdefault("OPENAI_BASE_URL", qwen_endpoint)
        os.environ.setdefault("OPENAI_API_KEY", "not-needed")

        config = RailsConfig.from_path(config_path)
        _nemo_rails = LLMRails(config)
        _nemo_available = True
        logging.getLogger("egress_guard.app").info(
            "NeMo Guardrails loaded successfully from %s", config_path,
        )
    except ImportError:
        logging.getLogger("egress_guard.app").warning(
            "nemoguardrails package not installed -- using standalone egress rails"
        )
    except Exception as exc:
        logging.getLogger("egress_guard.app").warning(
            "Failed to load NeMo config from %s: %s -- using standalone egress rails",
            config_path, exc,
        )


# ------------------------------------------------------------------ #
# Application factory
# ------------------------------------------------------------------ #


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("egress_guard.app")

    app = FastAPI(
        title="NeMo Egress Guard",
        description="Final egress checkpoint: verifies redacted content before SaaS call",
        version="0.1.0",
    )

    RAIL_NAMES = [
        "egress_sensitivity_check",
        "egress_secret_scan",
        "egress_pii_verification",
        "egress_placeholder_integrity",
    ]

    @app.on_event("startup")
    async def startup() -> None:
        logger.info("Loading NeMo Guardrails configuration...")
        _try_load_nemo(settings.nemo_config_path, settings.qwen_endpoint)
        logger.info(
            "Egress guard ready (nemo=%s, llm=%s)",
            _nemo_available, settings.qwen_endpoint,
        )

    @app.post("/guard/egress", response_model=EgressGuardResponse)
    async def guard_egress(
        body: EgressGuardRequest, request: Request
    ) -> EgressGuardResponse:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()

        approved, action, reason, triggered = evaluate_egress(
            body.redacted_text,
            body.sensitivity_level,
            body.entity_types_redacted,
        )

        if _nemo_available and _nemo_rails is not None and approved:
            try:
                nemo_resp = await _nemo_rails.generate_async(
                    messages=[{"role": "user", "content": body.redacted_text}],
                    options={
                        "context": {
                            "sensitivity_level": body.sensitivity_level,
                            "entity_types_redacted": body.entity_types_redacted,
                        },
                    },
                )
                nemo_content = nemo_resp.get("content", "") if isinstance(nemo_resp, dict) else str(nemo_resp)
                if "BLOCKED" in nemo_content.upper():
                    approved = False
                    action = "BLOCK"
                    reason = f"NeMo LLM rail: {nemo_content}"
                    triggered.append("nemo_llm_evaluation")
            except Exception as exc:
                logger.warning(
                    "NeMo LLM evaluation failed: %s -- standalone rails result stands",
                    exc,
                )

        elapsed_ms = (time.monotonic() - start) * 1000

        logger.info(
            "Egress guard evaluation complete",
            extra={
                "extra": {
                    "event": "guard_egress",
                    "request_id": request_id,
                    "approved": approved,
                    "action": action,
                    "rails_triggered": triggered,
                    "sensitivity_level": body.sensitivity_level,
                    "entity_count": len(body.entity_types_redacted),
                    "latency_ms": round(elapsed_ms, 2),
                }
            },
        )

        return EgressGuardResponse(
            approved=approved,
            action=action,
            reason=reason,
            rails_triggered=triggered,
            latency_ms=round(elapsed_ms, 1),
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            nemo_available=_nemo_available,
            rails_loaded=RAIL_NAMES + (["nemo_llm_evaluation"] if _nemo_available else []),
            llm_backend=settings.qwen_endpoint,
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
