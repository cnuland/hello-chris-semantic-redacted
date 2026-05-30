"""
Sensitivity classifier for privacy-preserving semantic routing.

Classifies incoming prompts into sensitivity levels (PUBLIC, INTERNAL,
CONFIDENTIAL, REGULATED, NEVER_EGRESS) using a two-phase approach:
  1. Fast-path: keyword/regex pattern matching for obvious signals
  2. Slow-path: embedding similarity against curated anchor prompts

The classifier is consumed as a library by the demo runner and redaction
service. It is NOT a standalone HTTP service.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

try:
    from sentence_transformers import SentenceTransformer, util as st_util

    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    _HAS_SENTENCE_TRANSFORMERS = False

logger = logging.getLogger(__name__)

# Ordered from least to most restrictive
SENSITIVITY_LEVELS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]
LEVEL_RANK = {level: idx for idx, level in enumerate(SENSITIVITY_LEVELS)}

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_DEFAULT_ANCHORS_PATH = (
    Path(__file__).parent.parent.parent / "data" / "sensitivity-anchors" / "anchors.jsonl"
)


@dataclass
class ClassificationResult:
    """Result of a sensitivity classification."""

    level: str
    confidence: float
    fast_path_signals: list[str] = field(default_factory=list)
    embedding_scores: dict[str, float] = field(default_factory=dict)
    source: str = "fast_path"  # "fast_path" | "embedding" | "combined"


class SensitivityClassifier:
    """Classifies text into sensitivity levels using keyword and embedding methods."""

    def __init__(
        self,
        anchors_path: Optional[str | Path] = None,
        config_path: Optional[str | Path] = None,
    ) -> None:
        self._config = self._load_config(config_path or _DEFAULT_CONFIG_PATH)
        self._anchors = self._load_anchors(anchors_path or _DEFAULT_ANCHORS_PATH)
        self._model = None
        self._anchor_embeddings: dict[str, list] = {}

        # Compile fast-path patterns from config
        self._compiled_patterns = self._compile_patterns()

        # Pre-compute keyword sets (lowered) from config
        self._keyword_sets = self._build_keyword_sets()

        # Build routing matrix from config
        self._routing_matrix = self._config.get("routing_matrix", {})

        # Lazy-load the embedding model on first use
        model_cfg = self._config.get("model", {})
        self._model_path = model_cfg.get("path")
        self._model_name = model_cfg.get("name", "all-MiniLM-L6-v2")
        self._top_k = model_cfg.get("top_k", 3)
        self._confidence_threshold = model_cfg.get(
            "confidence_threshold", 0.6
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, text: str) -> ClassificationResult:
        """Classify *text* and return the sensitivity level with diagnostics."""
        # Phase 1: fast-path keyword / pattern matching
        fp_level, fp_confidence, fp_signals = self._fast_path(text)

        # Phase 2: embedding similarity (if sentence-transformers is available)
        emb_level: Optional[str] = None
        emb_confidence: float = 0.0
        emb_scores: dict[str, float] = {}

        if _HAS_SENTENCE_TRANSFORMERS and self._anchors:
            emb_level, emb_confidence, emb_scores = self._embedding_path(text)

        # Combine: take the HIGHER (more restrictive) level
        if fp_level and emb_level:
            if LEVEL_RANK.get(emb_level, 0) > LEVEL_RANK.get(fp_level, 0):
                final_level = emb_level
                final_confidence = emb_confidence
                source = "combined"
            elif LEVEL_RANK.get(fp_level, 0) > LEVEL_RANK.get(emb_level, 0):
                final_level = fp_level
                final_confidence = fp_confidence
                source = "combined"
            else:
                # Same level from both paths
                final_level = fp_level
                final_confidence = max(fp_confidence, emb_confidence)
                source = "combined"
        elif fp_level:
            final_level = fp_level
            final_confidence = fp_confidence
            source = "fast_path"
        elif emb_level:
            final_level = emb_level
            final_confidence = emb_confidence
            source = "embedding"
        else:
            # Fallback: nothing matched -- default to INTERNAL (fail-safe)
            final_level = "INTERNAL"
            final_confidence = 0.5
            source = "fast_path"

        return ClassificationResult(
            level=final_level,
            confidence=final_confidence,
            fast_path_signals=fp_signals,
            embedding_scores=emb_scores,
            source=source,
        )

    # ------------------------------------------------------------------
    # Fast-path implementation
    # ------------------------------------------------------------------

    def _fast_path(self, text: str) -> tuple[Optional[str], float, list[str]]:
        """Run keyword/pattern matching. Returns (level, confidence, signals)."""
        text_lower = text.lower()
        signals: list[str] = []
        detected_level: Optional[str] = None

        # Check regex patterns (ordered from most restrictive to least)
        for level in reversed(SENSITIVITY_LEVELS):
            patterns = self._compiled_patterns.get(level, [])
            for pattern_name, regex in patterns:
                if regex.search(text):
                    signal = f"pattern:{pattern_name}"
                    signals.append(signal)
                    if detected_level is None or LEVEL_RANK[level] > LEVEL_RANK.get(
                        detected_level, -1
                    ):
                        detected_level = level

        # Check keyword sets (ordered from most restrictive to least)
        for level in reversed(SENSITIVITY_LEVELS):
            keywords = self._keyword_sets.get(level, set())
            for kw in keywords:
                if kw in text_lower:
                    signal = f"keyword:{kw}"
                    if signal not in signals:
                        signals.append(signal)
                    if detected_level is None or LEVEL_RANK[level] > LEVEL_RANK.get(
                        detected_level, -1
                    ):
                        detected_level = level

        confidence = 0.95 if signals else 0.0
        return detected_level, confidence, signals

    # ------------------------------------------------------------------
    # Embedding path implementation
    # ------------------------------------------------------------------

    def _embedding_path(self, text: str) -> tuple[Optional[str], float, dict[str, float]]:
        """Run embedding similarity against anchors. Returns (level, confidence, scores_per_level)."""
        if not _HAS_SENTENCE_TRANSFORMERS:
            return None, 0.0, {}

        self._ensure_model_loaded()

        # Encode input text
        query_embedding = self._model.encode(text, convert_to_tensor=True)

        # Score against each level
        scores_per_level: dict[str, float] = {}
        for level in SENSITIVITY_LEVELS:
            level_embeddings = self._anchor_embeddings.get(level)
            if level_embeddings is None or len(level_embeddings) == 0:
                scores_per_level[level] = 0.0
                continue

            # Compute cosine similarity to all anchors in this level
            cos_scores = st_util.cos_sim(query_embedding, level_embeddings)[0]
            cos_scores_list = cos_scores.tolist()

            # Top-K averaging
            sorted_scores = sorted(cos_scores_list, reverse=True)
            top_k = sorted_scores[: self._top_k]
            avg_score = sum(top_k) / len(top_k) if top_k else 0.0
            scores_per_level[level] = round(avg_score, 4)

        # Find highest-scoring level
        best_level = max(scores_per_level, key=lambda l: scores_per_level[l])
        best_score = scores_per_level[best_level]

        if best_score < self._confidence_threshold:
            # Below threshold: default to INTERNAL
            return "INTERNAL", best_score, scores_per_level

        return best_level, best_score, scores_per_level

    def _ensure_model_loaded(self) -> None:
        """Lazy-load the sentence-transformers model and pre-compute anchor embeddings."""
        if self._model is not None:
            return

        load_from = self._model_path or self._model_name
        logger.info("Loading sentence-transformers model: %s", load_from)
        self._model = SentenceTransformer(load_from)

        # Pre-compute anchor embeddings grouped by level
        for level in SENSITIVITY_LEVELS:
            texts = [a["text"] for a in self._anchors if a["label"] == level]
            if texts:
                self._anchor_embeddings[level] = self._model.encode(
                    texts, convert_to_tensor=True
                )
            else:
                self._anchor_embeddings[level] = []

    # ------------------------------------------------------------------
    # Routing matrix
    # ------------------------------------------------------------------

    def get_routing_action(
        self, complexity_tier: str, sensitivity_level: str
    ) -> str:
        """Look up routing action from the 2D complexity x sensitivity matrix.

        Args:
            complexity_tier: One of SIMPLE, MEDIUM, COMPLEX, REASONING
            sensitivity_level: One of PUBLIC, INTERNAL, CONFIDENTIAL, REGULATED, NEVER_EGRESS

        Returns:
            One of DIRECT_SAAS, REDACT_THEN_SAAS, LOCAL_ONLY
        """
        tier_upper = complexity_tier.upper()
        level_upper = sensitivity_level.upper()

        row = self._routing_matrix.get(tier_upper, {})
        action = row.get(level_upper, "LOCAL_ONLY")
        return action

    # ------------------------------------------------------------------
    # Config & anchor loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_config(path: str | Path) -> dict:
        path = Path(path)
        if not path.exists():
            logger.warning("Config file not found at %s, using defaults", path)
            return {}
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _load_anchors(path: str | Path) -> list[dict]:
        path = Path(path)
        if not path.exists():
            logger.warning("Anchors file not found at %s", path)
            return []
        anchors: list[dict] = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    anchors.append(json.loads(line))
        logger.info("Loaded %d anchor prompts from %s", len(anchors), path)
        return anchors

    def _compile_patterns(self) -> dict[str, list[tuple[str, re.Pattern]]]:
        """Compile regex patterns from config into a dict keyed by sensitivity level."""
        compiled: dict[str, list[tuple[str, re.Pattern]]] = {}
        patterns_cfg = self._config.get("patterns", {})
        for level, pattern_list in patterns_cfg.items():
            level_upper = level.upper()
            compiled[level_upper] = []
            for entry in pattern_list:
                name = entry.get("name", "unnamed")
                regex_str = entry.get("regex", "")
                try:
                    compiled[level_upper].append(
                        (name, re.compile(regex_str, re.IGNORECASE))
                    )
                except re.error as e:
                    logger.error("Invalid regex for %s/%s: %s", level, name, e)
        return compiled

    def _build_keyword_sets(self) -> dict[str, set[str]]:
        """Build lowercase keyword sets from config, keyed by sensitivity level."""
        keyword_sets: dict[str, set[str]] = {}
        keywords_cfg = self._config.get("keywords", {})
        for level, kw_list in keywords_cfg.items():
            level_upper = level.upper()
            keyword_sets[level_upper] = {kw.lower() for kw in kw_list}
        return keyword_sets
