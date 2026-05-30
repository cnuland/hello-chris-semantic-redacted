"""Redaction service: Presidio + GLiNER PII detection and pseudonymization."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from pseudonymizer import DetectedEntity, PseudonymMapper
from recognizers import get_all_custom_recognizers

# ---------------------------------------------------------------------------
# Structured JSON logging (no PII values -- types and counts only)
# ---------------------------------------------------------------------------

class _JSONFormatter(logging.Formatter):
    """Minimal structured JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def _setup_logging(level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logging.getLogger("redaction-service")


# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r") as fh:
            return yaml.safe_load(fh) or {}
    return {}


CONFIG = _load_config()
MAPPING_TTL = int(os.environ.get("MAPPING_TTL", 300))

logger = _setup_logging(CONFIG.get("server", {}).get("log_level", "info"))

# ---------------------------------------------------------------------------
# GLiNER (optional)
# ---------------------------------------------------------------------------

_gliner_model: Any = None
_gliner_available = False

try:
    from gliner import GLiNER  # type: ignore[import-untyped]

    _gliner_cfg = CONFIG.get("gliner", {})
    _model_name = _gliner_cfg.get("model", "urchade/gliner_multi_pii-v1")
    logger.info("Loading GLiNER model: %s", _model_name)
    _gliner_model = GLiNER.from_pretrained(_model_name)
    _gliner_available = True
    logger.info("GLiNER model loaded successfully")
except ImportError:
    logger.warning("GLiNER not installed -- falling back to regex-only project codename detection")
except Exception:
    logger.exception("GLiNER model failed to load -- falling back to regex-only detection")

# ---------------------------------------------------------------------------
# Presidio setup
# ---------------------------------------------------------------------------

from presidio_analyzer import AnalyzerEngine  # noqa: E402
from presidio_analyzer.nlp_engine import NlpEngineProvider  # noqa: E402

_spacy_model = CONFIG.get("presidio", {}).get("analyzer", {}).get("spacy_model", "en_core_web_sm")
_nlp_provider = NlpEngineProvider(nlp_configuration={
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": _spacy_model}],
})
_analyzer = AnalyzerEngine(nlp_engine=_nlp_provider.create_engine())

_custom_recognizers = get_all_custom_recognizers()
for rec in _custom_recognizers:
    _analyzer.registry.add_recognizer(rec)

logger.info(
    "Presidio analyzer ready: %d recognizers loaded (%d custom)",
    len(_analyzer.registry.recognizers),
    len(_custom_recognizers),
)

# ---------------------------------------------------------------------------
# Pseudonymizer
# ---------------------------------------------------------------------------

_mapper = PseudonymMapper(ttl_seconds=MAPPING_TTL)

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class RedactRequest(BaseModel):
    text: str
    sensitivity_level: str = "CONFIDENTIAL"
    entities_to_detect: list[str] | None = None
    language: str = "en"


class EntityInfo(BaseModel):
    type: str
    original: str
    placeholder: str
    start: int
    end: int
    score: float


class RedactResponse(BaseModel):
    redacted_text: str
    mapping_id: str
    entities: list[EntityInfo]
    entity_count: int
    redaction_applied: bool


class RestoreRequest(BaseModel):
    text: str
    mapping_id: str


class RestoreResponse(BaseModel):
    restored_text: str
    placeholders_restored: int
    mapping_deleted: bool


class ScanRequest(BaseModel):
    text: str
    check_types: list[str] = Field(default_factory=lambda: ["PII", "SECRETS", "INTERNAL_REFS"])


class ScanFinding(BaseModel):
    type: str
    value: str
    start: int
    end: int
    score: float


class ScanResponse(BaseModel):
    clean: bool
    findings: list[ScanFinding]
    scan_timestamp: str


class HealthResponse(BaseModel):
    status: str
    presidio_version: str
    recognizers_loaded: int
    custom_recognizers: int
    gliner_model_loaded: bool


RedactResponse.model_rebuild()
ScanResponse.model_rebuild()


# ---------------------------------------------------------------------------
# Helper: run detection pipeline
# ---------------------------------------------------------------------------


def _detect_entities(
    text: str,
    language: str,
    entities_to_detect: list[str] | None,
) -> list[DetectedEntity]:
    """Run the full detection pipeline (Presidio + GLiNER) and return merged entities."""

    score_threshold = CONFIG.get("presidio", {}).get("analyzer", {}).get("score_threshold", 0.5)

    # 1. Presidio built-in + custom recognizers
    presidio_results = _analyzer.analyze(
        text=text,
        language=language,
        entities=entities_to_detect,
        score_threshold=score_threshold,
    )

    entities: list[DetectedEntity] = [
        DetectedEntity(
            entity_type=r.entity_type,
            start=r.start,
            end=r.end,
            score=r.score,
            original=text[r.start : r.end],
        )
        for r in presidio_results
    ]

    # 2. GLiNER secondary detection (if available)
    if _gliner_available and _gliner_model is not None:
        gliner_cfg = CONFIG.get("gliner", {})
        labels = gliner_cfg.get("labels", [])
        threshold = gliner_cfg.get("threshold", 0.5)

        try:
            gl_entities = _gliner_model.predict_entities(
                text, labels=labels, threshold=threshold
            )
            for gle in gl_entities:
                ent = DetectedEntity(
                    entity_type=_gliner_label_to_type(gle.get("label", "")),
                    start=gle.get("start", 0),
                    end=gle.get("end", 0),
                    score=gle.get("score", 0.0),
                    original=gle.get("text", text[gle.get("start", 0) : gle.get("end", 0)]),
                )
                entities.append(ent)
        except Exception:
            logger.exception("GLiNER prediction failed -- using Presidio results only")

    # 3. Merge & dedup: overlapping spans keep highest confidence
    entities = _dedup_entities(entities)

    return entities


_GLINER_LABEL_MAP: dict[str, str] = {
    "project codename": "PROJECT_CODENAME",
    "internal tool name": "INTERNAL_TOOL",
    "team name": "TEAM_NAME",
    "building name": "BUILDING_NAME",
    "internal product name": "INTERNAL_PRODUCT",
}


def _gliner_label_to_type(label: str) -> str:
    return _GLINER_LABEL_MAP.get(label.lower(), label.upper().replace(" ", "_"))


_PREFERRED_ENTITY_TYPES = {"PHONE_NUMBER", "CREDIT_CARD", "EMAIL_ADDRESS", "PERSON"}


def _dedup_entities(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Remove overlapping entities, keeping the highest-scoring one.

    When scores tie, prefer entities whose type is in _PREFERRED_ENTITY_TYPES
    (e.g. PHONE_NUMBER over UK_NHS for the same span).
    """
    if not entities:
        return []

    def _sort_key(e: DetectedEntity) -> tuple:
        preferred = 0 if e.entity_type in _PREFERRED_ENTITY_TYPES else 1
        return (e.start, -e.score, preferred)

    sorted_ents = sorted(entities, key=_sort_key)
    deduped: list[DetectedEntity] = [sorted_ents[0]]

    for ent in sorted_ents[1:]:
        prev = deduped[-1]
        if ent.start < prev.end:
            if ent.score > prev.score:
                deduped[-1] = ent
            elif ent.score == prev.score and ent.entity_type in _PREFERRED_ENTITY_TYPES and prev.entity_type not in _PREFERRED_ENTITY_TYPES:
                deduped[-1] = ent
        else:
            deduped.append(ent)

    return deduped


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Redaction Service",
    description="Presidio + GLiNER PII detection, pseudonymization, and restoration",
    version="0.1.0",
)


@app.post("/redact", response_model=RedactResponse)
async def redact(req: RedactRequest) -> RedactResponse:
    """Detect sensitive spans and replace with deterministic placeholders."""
    start_t = time.monotonic()

    entities = _detect_entities(req.text, req.language, req.entities_to_detect)

    if not entities:
        logger.info("No entities detected in request")
        return RedactResponse(
            redacted_text=req.text,
            mapping_id="",
            entities=[],
            entity_count=0,
            redaction_applied=False,
        )

    mapping_id, entry = _mapper.create_mapping(entities)
    redacted_text = _mapper.apply_redaction(req.text, entry)

    entity_infos: list[EntityInfo] = []
    for ent in entities:
        placeholder = entry.forward.get(ent.original, "")
        entity_infos.append(
            EntityInfo(
                type=ent.entity_type,
                original=ent.original,
                placeholder=placeholder,
                start=ent.start,
                end=ent.end,
                score=round(ent.score, 4),
            )
        )

    elapsed_ms = (time.monotonic() - start_t) * 1000
    logger.info(
        "Redaction complete: %d entities detected in %.1fms (types: %s)",
        len(entities),
        elapsed_ms,
        ", ".join(sorted({e.entity_type for e in entities})),
    )

    return RedactResponse(
        redacted_text=redacted_text,
        mapping_id=mapping_id,
        entities=entity_infos,
        entity_count=len(entity_infos),
        redaction_applied=True,
    )


@app.post("/restore", response_model=RestoreResponse)
async def restore(req: RestoreRequest) -> RestoreResponse:
    """Replace placeholders with original values and delete the mapping."""
    try:
        restored_text, count, deleted = _mapper.restore_text(req.text, req.mapping_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    logger.info(
        "Restoration complete: %d placeholders restored (mapping %s deleted)",
        count,
        req.mapping_id,
    )

    return RestoreResponse(
        restored_text=restored_text,
        placeholders_restored=count,
        mapping_deleted=deleted,
    )


@app.post("/scan", response_model=ScanResponse)
async def scan(req: ScanRequest) -> ScanResponse:
    """Scan text for residual sensitive content (output rail)."""
    entities = _detect_entities(req.text, "en", entities_to_detect=None)

    findings: list[ScanFinding] = []
    for ent in entities:
        # Map check_types to entity categories
        category = _entity_to_check_type(ent.entity_type)
        if category in req.check_types:
            findings.append(
                ScanFinding(
                    type=ent.entity_type,
                    value=ent.original,
                    start=ent.start,
                    end=ent.end,
                    score=round(ent.score, 4),
                )
            )

    logger.info(
        "Scan complete: %d findings (clean=%s)",
        len(findings),
        len(findings) == 0,
    )

    return ScanResponse(
        clean=len(findings) == 0,
        findings=findings,
        scan_timestamp=datetime.now(timezone.utc).isoformat(),
    )


_CHECK_TYPE_MAP: dict[str, str] = {
    "PERSON": "PII",
    "EMAIL_ADDRESS": "PII",
    "PHONE_NUMBER": "PII",
    "US_SSN": "PII",
    "CREDIT_CARD": "PII",
    "IP_ADDRESS": "SECRETS",
    "URL": "INTERNAL_REFS",
    "CLUSTER_NAME": "INTERNAL_REFS",
    "K8S_NAMESPACE": "INTERNAL_REFS",
    "EMPLOYEE_ID": "PII",
    "INTERNAL_URL": "INTERNAL_REFS",
    "PROJECT_CODENAME": "INTERNAL_REFS",
    "LOCATION": "CONTEXTUAL",
    "ORGANIZATION": "CONTEXTUAL",
    "DATE_TIME": "CONTEXTUAL",
    "NRP": "CONTEXTUAL",
    "US_ITIN": "PII",
}


def _entity_to_check_type(entity_type: str) -> str:
    return _CHECK_TYPE_MAP.get(entity_type, "CONTEXTUAL")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service health and recognizer inventory."""
    try:
        import importlib.metadata

        presidio_ver = importlib.metadata.version("presidio-analyzer")
    except Exception:
        presidio_ver = "unknown"

    total = len(_analyzer.registry.recognizers)
    custom = len(_custom_recognizers)

    return HealthResponse(
        status="healthy",
        presidio_version=presidio_ver,
        recognizers_loaded=total,
        custom_recognizers=custom,
        gliner_model_loaded=_gliner_available,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    server_cfg = CONFIG.get("server", {})
    uvicorn.run(
        "app:app",
        host=server_cfg.get("host", "0.0.0.0"),
        port=server_cfg.get("port", 8000),
        workers=server_cfg.get("workers", 2),
        log_level=server_cfg.get("log_level", "info"),
    )
