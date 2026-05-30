"""FastAPI wrapper for the sensitivity classifier."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

from classifier import ClassificationResult, SensitivityClassifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sensitivity Classifier", version="1.0.0")

_classifier: SensitivityClassifier | None = None


def _get_classifier() -> SensitivityClassifier:
    global _classifier
    if _classifier is None:
        config_path = Path("/app/config.yaml")
        anchors_path = Path("/app/anchors.jsonl")
        _classifier = SensitivityClassifier(
            config_path=config_path,
            anchors_path=anchors_path,
        )
        _classifier._ensure_model_loaded()
        logger.info("Classifier ready: model=%s", _classifier._model_path or _classifier._model_name)
    return _classifier


class ClassifyRequest(BaseModel):
    text: str
    complexity_tier: str = Field(default="MEDIUM", description="SIMPLE | MEDIUM | COMPLEX | REASONING")


class ClassifyResponse(BaseModel):
    sensitivity_level: str
    confidence: float
    routing_action: str
    source: str
    fast_path_signals: list[str]
    embedding_scores: dict[str, float]
    latency_ms: float


@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest) -> ClassifyResponse:
    classifier = _get_classifier()
    t0 = time.perf_counter()
    result: ClassificationResult = classifier.classify(req.text)
    action = classifier.get_routing_action(req.complexity_tier, result.level)
    latency = (time.perf_counter() - t0) * 1000

    logger.info(
        '{"event":"classify","level":"%s","confidence":%.3f,"action":"%s","source":"%s","latency_ms":%.1f}',
        result.level, result.confidence, action, result.source, latency,
    )

    return ClassifyResponse(
        sensitivity_level=result.level,
        confidence=result.confidence,
        routing_action=action,
        source=result.source,
        fast_path_signals=result.fast_path_signals,
        embedding_scores=result.embedding_scores,
        latency_ms=round(latency, 1),
    )


@app.get("/health")
def health() -> dict:
    classifier = _get_classifier()
    return {
        "status": "healthy",
        "model": classifier._model_path or classifier._model_name,
        "anchors_loaded": len(classifier._anchors),
        "levels": len(classifier._anchor_embeddings),
    }


@app.on_event("startup")
async def startup():
    logger.info("Pre-loading classifier model...")
    _get_classifier()
    logger.info("Classifier startup complete")
