"""Configuration for the NeMo egress guard service."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """NeMo egress guard configuration loaded from environment variables."""

    model_config = {"env_prefix": "EGRESS_GUARD_"}

    service_name: str = "nemo-egress-guard"
    host: str = "0.0.0.0"
    port: int = 8003
    log_level: str = "INFO"

    qwen_endpoint: str = Field(
        default="http://llama-server-qwen36.homelab-maas.svc.cluster.local:8080/v1",
        description="Qwen 3.6 OpenAI-compatible endpoint for NeMo rail evaluation",
    )
    redaction_service_url: str = Field(
        default="http://redaction-service.semantic-redacted.svc:8000",
        description="Redaction service URL for /scan verification calls",
    )
    nemo_config_path: str = Field(
        default="/app/config",
        description="Path to NeMo Guardrails Colang configuration directory",
    )
    redaction_scan_timeout_s: float = Field(
        default=5.0,
        description="Timeout for redaction service /scan calls",
    )

    blocked_sensitivity_levels: list[str] = Field(
        default=["NEVER_EGRESS", "REGULATED"],
        description="Sensitivity levels that must never egress even after redaction",
    )
