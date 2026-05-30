"""Evaluation module for sensitivity classification models.

Computes accuracy, per-class F1, confusion matrix, and latency
for both base and fine-tuned embedding models against anchor data.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sentence_transformers import SentenceTransformer, util as st_util

logger = logging.getLogger(__name__)

TIERS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]
TIER_TO_IDX = {t: i for i, t in enumerate(TIERS)}


@dataclass
class EvalResult:
    accuracy: float
    per_class_f1: dict[str, float]
    confusion_matrix: list[list[int]]
    total_examples: int
    correct: int
    avg_latency_ms: float
    predictions: list[dict] = field(default_factory=list)


def load_eval_data(path: Path) -> list[dict]:
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                obj = json.loads(line)
                if "text" in obj and ("tier" in obj or "label" in obj):
                    label_key = "tier" if "tier" in obj else "label"
                    examples.append({"text": obj["text"], "tier": obj[label_key]})
    return examples


def classify_with_model(
    model: SentenceTransformer,
    anchor_texts: dict[str, list[str]],
    anchor_embeddings: dict[str, object],
    text: str,
    top_k: int = 3,
) -> tuple[str, float, dict[str, float]]:
    query_emb = model.encode(text, convert_to_tensor=True)
    scores: dict[str, float] = {}

    for tier in TIERS:
        embs = anchor_embeddings.get(tier)
        if embs is None or len(anchor_texts.get(tier, [])) == 0:
            scores[tier] = 0.0
            continue
        cos = st_util.cos_sim(query_emb, embs)[0].tolist()
        top = sorted(cos, reverse=True)[:top_k]
        scores[tier] = sum(top) / len(top) if top else 0.0

    best = max(scores, key=lambda t: scores[t])
    return best, scores[best], scores


def evaluate(
    model_path: str,
    eval_path: Path,
    anchors_path: Path,
    top_k: int = 3,
) -> EvalResult:
    logger.info("Loading model from %s", model_path)
    model = SentenceTransformer(model_path)

    anchors = load_eval_data(anchors_path)
    anchor_texts: dict[str, list[str]] = {t: [] for t in TIERS}
    for a in anchors:
        if a["tier"] in anchor_texts:
            anchor_texts[a["tier"]].append(a["text"])

    anchor_embeddings = {}
    for tier in TIERS:
        if anchor_texts[tier]:
            anchor_embeddings[tier] = model.encode(anchor_texts[tier], convert_to_tensor=True)

    eval_data = load_eval_data(eval_path)
    logger.info("Evaluating %d examples against %d anchors", len(eval_data), len(anchors))

    confusion = [[0] * len(TIERS) for _ in TIERS]
    predictions = []
    latencies = []
    correct = 0

    for ex in eval_data:
        true_tier = ex["tier"]
        if true_tier not in TIER_TO_IDX:
            continue

        t0 = time.perf_counter()
        pred_tier, confidence, scores = classify_with_model(
            model, anchor_texts, anchor_embeddings, ex["text"], top_k
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)

        true_idx = TIER_TO_IDX[true_tier]
        pred_idx = TIER_TO_IDX.get(pred_tier, 0)
        confusion[true_idx][pred_idx] += 1

        is_correct = pred_tier == true_tier
        if is_correct:
            correct += 1

        predictions.append({
            "text": ex["text"][:80],
            "true": true_tier,
            "pred": pred_tier,
            "confidence": round(confidence, 4),
            "correct": is_correct,
        })

    total = len(predictions)
    accuracy = correct / total if total > 0 else 0.0

    per_class_f1 = _compute_per_class_f1(confusion)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    result = EvalResult(
        accuracy=round(accuracy, 4),
        per_class_f1=per_class_f1,
        confusion_matrix=confusion,
        total_examples=total,
        correct=correct,
        avg_latency_ms=round(avg_latency, 2),
        predictions=predictions,
    )

    logger.info(
        "Evaluation complete: accuracy=%.2f%% (%d/%d), avg_latency=%.1fms",
        accuracy * 100, correct, total, avg_latency,
    )
    return result


def _compute_per_class_f1(confusion: list[list[int]]) -> dict[str, float]:
    f1s = {}
    for i, tier in enumerate(TIERS):
        tp = confusion[i][i]
        fp = sum(confusion[j][i] for j in range(len(TIERS))) - tp
        fn = sum(confusion[i]) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1s[tier] = round(f1, 4)
    return f1s


def format_report(result: EvalResult) -> str:
    lines = [
        "# Sensitivity Model Evaluation Report",
        "",
        f"**Accuracy:** {result.accuracy * 100:.1f}% ({result.correct}/{result.total_examples})",
        f"**Avg Latency:** {result.avg_latency_ms:.1f}ms per query",
        "",
        "## Per-Class F1 Scores",
        "",
        "| Tier | F1 |",
        "|------|-----|",
    ]
    for tier in TIERS:
        f1 = result.per_class_f1.get(tier, 0.0)
        lines.append(f"| {tier} | {f1:.4f} |")

    lines += [
        "",
        "## Confusion Matrix",
        "",
        "| True \\ Pred | " + " | ".join(TIERS) + " |",
        "| --- | " + " | ".join(["---"] * len(TIERS)) + " |",
    ]
    for i, tier in enumerate(TIERS):
        row = " | ".join(str(result.confusion_matrix[i][j]) for j in range(len(TIERS)))
        lines.append(f"| {tier} | {row} |")

    lines += [
        "",
        "## Misclassified Examples",
        "",
    ]
    misses = [p for p in result.predictions if not p["correct"]]
    if misses:
        lines.append("| Text | True | Pred | Conf |")
        lines.append("| --- | --- | --- | --- |")
        for m in misses[:20]:
            lines.append(f"| {m['text']} | {m['true']} | {m['pred']} | {m['confidence']} |")
    else:
        lines.append("No misclassified examples.")

    return "\n".join(lines)
