"""Guardrails service -- lightweight privacy guard for semantic routing.

Implements the NeMo Guardrails API contract (``/guard/input``,
``/guard/retrieval``, ``/guard/output``) using regex/pattern-based rails
rather than the full NeMo framework.  No LLM calls, pure pattern matching.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from config import Settings
from rails import (
    OutputScanRail,
    PiiDetectionRail,
    ReconstructionDetectionRail,
    RetrievalFilterRail,
    SecretDetectionRail,
    SensitivityRail,
)

# ------------------------------------------------------------------ #
# Structured JSON logging
# ------------------------------------------------------------------ #


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "service": "guardrails-service",
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
# Pydantic request / response models
# ------------------------------------------------------------------ #


class Message(BaseModel):
    role: str
    content: str


class GuardInputRequest(BaseModel):
    messages: list[Message]
    sensitivity_level: str | None = None
    intended_route: str | None = None


class GuardInputResponse(BaseModel):
    allowed: bool
    action: str
    reason: str
    rails_triggered: list[str]
    suggested_route: str | None = None


class ChunkModel(BaseModel):
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GuardRetrievalRequest(BaseModel):
    chunks: list[ChunkModel]
    intended_route: str | None = None


class RemovalReason(BaseModel):
    chunk_index: int
    reason: str


class GuardRetrievalResponse(BaseModel):
    filtered_chunks: list[ChunkModel]
    removed_count: int
    removal_reasons: list[RemovalReason]


class GuardOutputRequest(BaseModel):
    response_text: str
    original_sensitivity: str | None = None
    model_source: str | None = None
    redacted_entities: list[str] | None = None


class Finding(BaseModel):
    type: str
    detail: str


class GuardOutputResponse(BaseModel):
    clean: bool
    findings: list[Finding]
    action: str


class HealthResponse(BaseModel):
    status: str
    rails_loaded: list[str]
    llm_backend: str
    llm_reachable: bool | None = None


# ------------------------------------------------------------------ #
# Application factory
# ------------------------------------------------------------------ #


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the FastAPI application with all rails wired up."""

    settings = settings or Settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("guardrails.app")

    app = FastAPI(
        title="Guardrails Service",
        description="Lightweight privacy guardrails for semantic routing",
        version="0.1.0",
    )

    # Instantiate rails once at startup
    sensitivity_rail = SensitivityRail(settings)
    secret_rail = SecretDetectionRail(settings)
    pii_rail = PiiDetectionRail(settings)
    retrieval_rail = RetrievalFilterRail(settings)
    output_scan_rail = OutputScanRail(settings)
    reconstruction_rail = ReconstructionDetectionRail()

    ALL_RAIL_NAMES = [
        "input_sensitivity",
        "input_secrets",
        "input_pii",
        "retrieval_filter",
        "output_scan",
        "output_reconstruction",
    ]

    # -------------------------------------------------------------- #
    # POST /guard/input
    # -------------------------------------------------------------- #

    @app.post("/guard/input", response_model=GuardInputResponse)
    async def guard_input(
        body: GuardInputRequest, request: Request
    ) -> GuardInputResponse:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()

        # Combine all message content for scanning
        full_text = " ".join(m.content for m in body.messages)

        triggered: list[str] = []
        action = "ALLOW"
        reason = "All input rails passed"
        suggested_route: str | None = None

        # 1. Sensitivity check
        sens_result = sensitivity_rail.evaluate(
            body.sensitivity_level, body.intended_route
        )
        if sens_result.triggered:
            triggered.append(sens_result.rail_name)
            action = sens_result.action
            reason = sens_result.reason
            suggested_route = sens_result.details.get("suggested_route")

        # 2. Secret detection
        secret_result = secret_rail.evaluate(full_text)
        if secret_result.triggered:
            triggered.append(secret_result.rail_name)
            # Secrets always block -- overrides weaker actions
            action = "BLOCK_SAAS"
            reason = secret_result.reason
            suggested_route = suggested_route or settings.local_model_fallback

        # 3. PII detection
        pii_result = await pii_rail.evaluate(full_text)
        if pii_result.triggered:
            triggered.append(pii_result.rail_name)
            # PII doesn't block outright if we can redact -- but escalate
            # if nothing else already blocked
            if action == "ALLOW":
                action = "REQUIRE_REDACTION"
                reason = pii_result.reason

        allowed = action == "ALLOW"
        elapsed_ms = (time.monotonic() - start) * 1000

        logger.info(
            "Input rail evaluation complete",
            extra={
                "extra": {
                    "event": "guard_input",
                    "request_id": request_id,
                    "allowed": allowed,
                    "action": action,
                    "rails_triggered": triggered,
                    "sensitivity_level": body.sensitivity_level,
                    "intended_route": body.intended_route,
                    "latency_ms": round(elapsed_ms, 2),
                }
            },
        )

        return GuardInputResponse(
            allowed=allowed,
            action=action,
            reason=reason,
            rails_triggered=triggered,
            suggested_route=suggested_route,
        )

    # -------------------------------------------------------------- #
    # POST /guard/retrieval
    # -------------------------------------------------------------- #

    @app.post("/guard/retrieval", response_model=GuardRetrievalResponse)
    async def guard_retrieval(
        body: GuardRetrievalRequest, request: Request
    ) -> GuardRetrievalResponse:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()

        chunks_as_dicts = [c.model_dump() for c in body.chunks]
        filtered, removed_count, reasons = retrieval_rail.evaluate(
            chunks_as_dicts, body.intended_route
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        logger.info(
            "Retrieval rail evaluation complete",
            extra={
                "extra": {
                    "event": "guard_retrieval",
                    "request_id": request_id,
                    "total_chunks": len(body.chunks),
                    "removed_count": removed_count,
                    "intended_route": body.intended_route,
                    "latency_ms": round(elapsed_ms, 2),
                }
            },
        )

        return GuardRetrievalResponse(
            filtered_chunks=[ChunkModel(**c) for c in filtered],
            removed_count=removed_count,
            removal_reasons=[RemovalReason(**r) for r in reasons],
        )

    # -------------------------------------------------------------- #
    # POST /guard/output
    # -------------------------------------------------------------- #

    @app.post("/guard/output", response_model=GuardOutputResponse)
    async def guard_output(
        body: GuardOutputRequest, request: Request
    ) -> GuardOutputResponse:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()

        findings: list[Finding] = []
        action = "ALLOW"

        # 1. Output scan (PII + secrets in response)
        scan_result = output_scan_rail.evaluate(body.response_text)
        if scan_result.triggered:
            for f in scan_result.details.get("findings", []):
                findings.append(
                    Finding(
                        type=f.get("type", "UNKNOWN"),
                        detail=f.get("entity_type", f.get("pattern", "")),
                    )
                )
            action = scan_result.action

        # 2. Reconstruction detection
        recon_result = reconstruction_rail.evaluate(
            body.response_text, body.redacted_entities
        )
        if recon_result.triggered:
            findings.append(
                Finding(
                    type="RECONSTRUCTION",
                    detail=(
                        f"{recon_result.details.get('reconstructed_count', 0)} "
                        "entity/entities reconstructed"
                    ),
                )
            )
            # Reconstruction is serious -- escalate to RE_REDACT or BLOCK
            if action == "ALLOW":
                action = recon_result.action

        clean = len(findings) == 0
        elapsed_ms = (time.monotonic() - start) * 1000

        logger.info(
            "Output rail evaluation complete",
            extra={
                "extra": {
                    "event": "guard_output",
                    "request_id": request_id,
                    "clean": clean,
                    "action": action,
                    "findings_count": len(findings),
                    "model_source": body.model_source,
                    "latency_ms": round(elapsed_ms, 2),
                }
            },
        )

        return GuardOutputResponse(
            clean=clean,
            findings=findings,
            action=action,
        )

    # -------------------------------------------------------------- #
    # GET /health
    # -------------------------------------------------------------- #

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            rails_loaded=ALL_RAIL_NAMES,
            llm_backend=settings.qwen_endpoint,
            llm_reachable=None,  # LLM rails deferred -- no connectivity check
        )

    return app


# ------------------------------------------------------------------ #
# Entrypoint
# ------------------------------------------------------------------ #

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
