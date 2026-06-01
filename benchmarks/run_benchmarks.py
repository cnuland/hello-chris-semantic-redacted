#!/usr/bin/env python3
"""Comprehensive benchmark suite for the privacy-preserving semantic routing pipeline.

Runs 7 benchmark categories against live cluster services and produces
JSON + markdown output suitable for publication.

Usage:
    python benchmarks/run_benchmarks.py --all
    python benchmarks/run_benchmarks.py --category classification
    python benchmarks/run_benchmarks.py --category redaction --output results/
    python benchmarks/run_benchmarks.py --all --warmup 3 --runs 10
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TEST_PROMPTS_DIR = DATA_DIR / "test-prompts"
BENCHMARK_CORPUS_DIR = DATA_DIR / "benchmark-corpus"
ANCHORS_PATH = DATA_DIR / "sensitivity-anchors" / "anchors.jsonl"

REDACTION_URL = os.environ.get("REDACTION_SERVICE_URL", "http://localhost:8000")
GUARDRAILS_URL = os.environ.get("GUARDRAILS_SERVICE_URL", "http://localhost:8001")
CLASSIFIER_URL = os.environ.get("CLASSIFIER_SERVICE_URL", "http://localhost:8002")
EGRESS_GUARD_URL = os.environ.get("EGRESS_GUARD_URL", "http://localhost:8003")
QWEN_URL = os.environ.get("LOCAL_MODEL_URL", "http://localhost:11434")

TIERS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]
TIER_TO_IDX = {t: i for i, t in enumerate(TIERS)}

CLIENT = httpx.Client(timeout=30.0)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict]:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def load_all_test_prompts() -> list[dict]:
    prompts = []
    for fp in sorted(TEST_PROMPTS_DIR.glob("*.jsonl")):
        for entry in load_jsonl(fp):
            label = entry.get("expected_label") or entry.get("label") or entry.get("tier")
            prompts.append({"text": entry["text"], "expected": label})
    return prompts


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_v[int(k)]
    return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def latency_stats(values: list[float]) -> dict:
    return {
        "mean_ms": round(mean(values), 2),
        "stdev_ms": round(stdev(values), 2),
        "p50_ms": round(percentile(values, 50), 2),
        "p95_ms": round(percentile(values, 95), 2),
        "p99_ms": round(percentile(values, 99), 2),
        "min_ms": round(min(values), 2) if values else 0,
        "max_ms": round(max(values), 2) if values else 0,
        "n": len(values),
    }


def compute_per_class_f1(confusion: list[list[int]]) -> dict[str, dict[str, float]]:
    metrics = {}
    for i, tier in enumerate(TIERS):
        tp = confusion[i][i]
        fp = sum(confusion[j][i] for j in range(len(TIERS))) - tp
        fn = sum(confusion[i]) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        metrics[tier] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": sum(confusion[i]),
        }
    return metrics


# ---------------------------------------------------------------------------
# Service health checks
# ---------------------------------------------------------------------------


def check_service(name: str, url: str) -> bool:
    try:
        resp = CLIENT.get(f"{url}/health", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def check_all_services() -> dict[str, bool]:
    services = {
        "classifier": CLASSIFIER_URL,
        "redaction": REDACTION_URL,
        "guardrails": GUARDRAILS_URL,
        "egress_guard": EGRESS_GUARD_URL,
    }
    status = {}
    for name, url in services.items():
        alive = check_service(name, url)
        status[name] = alive
        symbol = "OK" if alive else "UNREACHABLE"
        print(f"  {name:20s} {url:45s} [{symbol}]")
    return status


# ---------------------------------------------------------------------------
# Category 1: Classification Accuracy
# ---------------------------------------------------------------------------


def benchmark_classification(prompts: list[dict], warmup: int = 3, runs: int = 1) -> dict:
    print(f"\n{'='*60}")
    print("Category 1: Classification Accuracy")
    print(f"{'='*60}")
    print(f"  Prompts: {len(prompts)}, Warmup: {warmup}, Runs: {runs}")

    # Warmup
    for _ in range(warmup):
        try:
            CLIENT.post(f"{CLASSIFIER_URL}/classify", json={"text": "warmup test", "complexity_tier": "MEDIUM"})
        except Exception:
            pass

    confusion = [[0] * len(TIERS) for _ in TIERS]
    predictions = []
    latencies = []
    source_counts: dict[str, int] = Counter()
    fast_path_results = {"correct": 0, "total": 0}
    embedding_results = {"correct": 0, "total": 0}

    for prompt in prompts:
        run_latencies = []
        last_resp = None

        for run_idx in range(runs):
            t0 = time.perf_counter()
            try:
                resp = CLIENT.post(
                    f"{CLASSIFIER_URL}/classify",
                    json={"text": prompt["text"], "complexity_tier": "MEDIUM"},
                )
                resp.raise_for_status()
                data = resp.json()
                elapsed = (time.perf_counter() - t0) * 1000
                run_latencies.append(elapsed)
                last_resp = data
            except Exception as e:
                print(f"  ERROR classifying: {e}")
                continue

        if last_resp is None:
            continue

        latencies.extend(run_latencies)
        predicted = last_resp["sensitivity_level"]
        expected = prompt["expected"]
        source = last_resp.get("source", "unknown")
        source_counts[source] += 1

        true_idx = TIER_TO_IDX.get(expected, 0)
        pred_idx = TIER_TO_IDX.get(predicted, 0)
        confusion[true_idx][pred_idx] += 1
        is_correct = predicted == expected

        if source == "keyword":
            fast_path_results["total"] += 1
            if is_correct:
                fast_path_results["correct"] += 1
        elif source == "embedding":
            embedding_results["total"] += 1
            if is_correct:
                embedding_results["correct"] += 1

        predictions.append({
            "text": prompt["text"][:80],
            "expected": expected,
            "predicted": predicted,
            "correct": is_correct,
            "source": source,
            "confidence": last_resp.get("confidence", 0),
            "latency_ms": round(mean(run_latencies), 2),
        })

    total = len(predictions)
    correct = sum(1 for p in predictions if p["correct"])
    accuracy = correct / total if total > 0 else 0.0

    per_class = compute_per_class_f1(confusion)
    macro_f1 = mean([m["f1"] for m in per_class.values()])

    weighted_f1_num = sum(m["f1"] * m["support"] for m in per_class.values())
    weighted_f1 = weighted_f1_num / total if total > 0 else 0.0

    throughput = len(latencies) / (sum(latencies) / 1000) if latencies else 0

    result = {
        "accuracy": round(accuracy, 4),
        "correct": correct,
        "total": total,
        "macro_f1": round(macro_f1, 4),
        "weighted_f1": round(weighted_f1, 4),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "source_distribution": dict(source_counts),
        "fast_path_accuracy": round(
            fast_path_results["correct"] / fast_path_results["total"], 4
        ) if fast_path_results["total"] > 0 else None,
        "fast_path_total": fast_path_results["total"],
        "embedding_accuracy": round(
            embedding_results["correct"] / embedding_results["total"], 4
        ) if embedding_results["total"] > 0 else None,
        "embedding_total": embedding_results["total"],
        "latency": latency_stats(latencies),
        "throughput_rps": round(throughput, 1),
        "predictions": predictions,
    }

    print(f"  Accuracy: {accuracy*100:.1f}% ({correct}/{total})")
    print(f"  Macro F1: {macro_f1:.4f}  Weighted F1: {weighted_f1:.4f}")
    print(f"  Fast-path: {fast_path_results['correct']}/{fast_path_results['total']}"
          f"  Embedding: {embedding_results['correct']}/{embedding_results['total']}")
    print(f"  Latency p50={latency_stats(latencies)['p50_ms']}ms  p95={latency_stats(latencies)['p95_ms']}ms")
    return result


# ---------------------------------------------------------------------------
# Category 2: Redaction Coverage
# ---------------------------------------------------------------------------


def benchmark_redaction(warmup: int = 3, runs: int = 1) -> dict:
    print(f"\n{'='*60}")
    print("Category 2: Redaction Coverage")
    print(f"{'='*60}")

    corpus_path = BENCHMARK_CORPUS_DIR / "redaction-entities.jsonl"
    if not corpus_path.exists():
        print("  SKIP: redaction-entities.jsonl not found")
        return {"skipped": True, "reason": "corpus file not found"}

    corpus = load_jsonl(corpus_path)
    print(f"  Corpus: {len(corpus)} entries")

    for _ in range(warmup):
        try:
            CLIENT.post(f"{REDACTION_URL}/redact", json={"text": "warmup Sarah at test@test.com"})
        except Exception:
            pass

    per_type_tp: dict[str, int] = Counter()
    per_type_fn: dict[str, int] = Counter()
    per_type_fp: dict[str, int] = Counter()
    redact_latencies: list[float] = []
    restore_latencies: list[float] = []
    roundtrip_successes = 0
    roundtrip_total = 0
    results_detail: list[dict] = []

    for entry in corpus:
        text = entry["text"]
        expected_entities = entry.get("expected_entities", [])
        expected_types = {e["type"] for e in expected_entities}

        for _ in range(runs):
            t0 = time.perf_counter()
            try:
                resp = CLIENT.post(f"{REDACTION_URL}/redact", json={"text": text})
                resp.raise_for_status()
                data = resp.json()
                elapsed = (time.perf_counter() - t0) * 1000
                redact_latencies.append(elapsed)
            except Exception as e:
                print(f"  ERROR redacting: {e}")
                continue

            detected_types = {e["type"] for e in data.get("entities", [])}
            detected_values = {e.get("original", "").lower() for e in data.get("entities", [])}

            for exp in expected_entities:
                exp_type = exp["type"]
                exp_value = exp.get("value", "").lower()
                if exp_type in detected_types or exp_value in detected_values:
                    per_type_tp[exp_type] += 1
                else:
                    per_type_fn[exp_type] += 1

            for det_type in detected_types:
                if det_type not in expected_types:
                    per_type_fp[det_type] += 1

            # Roundtrip test (redact → restore)
            mapping_id = data.get("mapping_id", "")
            redacted_text = data.get("redacted_text", "")
            if mapping_id and data.get("redaction_applied"):
                roundtrip_total += 1
                t1 = time.perf_counter()
                try:
                    restore_resp = CLIENT.post(
                        f"{REDACTION_URL}/restore",
                        json={"text": redacted_text, "mapping_id": mapping_id},
                    )
                    restore_resp.raise_for_status()
                    restore_data = restore_resp.json()
                    restore_elapsed = (time.perf_counter() - t1) * 1000
                    restore_latencies.append(restore_elapsed)
                    if restore_data.get("restored_text", "") == text:
                        roundtrip_successes += 1
                except Exception:
                    pass

        results_detail.append({
            "text": text[:80],
            "expected_types": sorted(expected_types),
            "detected_types": sorted(detected_types) if 'detected_types' in dir() else [],
        })

    all_types = sorted(set(list(per_type_tp.keys()) + list(per_type_fn.keys()) + list(per_type_fp.keys())))
    per_type_metrics = {}
    for t in all_types:
        tp = per_type_tp[t]
        fn = per_type_fn[t]
        fp = per_type_fp[t]
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        per_type_metrics[t] = {
            "tp": tp, "fn": fn, "fp": fp,
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
        }

    total_tp = sum(per_type_tp.values())
    total_fn = sum(per_type_fn.values())
    total_fp = sum(per_type_fp.values())
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    overall_f1 = (2 * overall_precision * overall_recall / (overall_precision + overall_recall)
                  if (overall_precision + overall_recall) > 0 else 0.0)

    result = {
        "overall_recall": round(overall_recall, 4),
        "overall_precision": round(overall_precision, 4),
        "overall_f1": round(overall_f1, 4),
        "per_type": per_type_metrics,
        "roundtrip_fidelity": round(roundtrip_successes / roundtrip_total, 4) if roundtrip_total > 0 else None,
        "roundtrip_total": roundtrip_total,
        "redact_latency": latency_stats(redact_latencies),
        "restore_latency": latency_stats(restore_latencies),
        "corpus_size": len(corpus),
    }

    print(f"  Overall Recall: {overall_recall*100:.1f}%  Precision: {overall_precision*100:.1f}%  F1: {overall_f1:.4f}")
    print(f"  Roundtrip fidelity: {roundtrip_successes}/{roundtrip_total}")
    print(f"  Redact latency p50={latency_stats(redact_latencies)['p50_ms']}ms")
    for t, m in sorted(per_type_metrics.items()):
        print(f"    {t:25s} recall={m['recall']:.2f}  precision={m['precision']:.2f}  f1={m['f1']:.2f}")
    return result


# ---------------------------------------------------------------------------
# Category 3: Guardrails Detection
# ---------------------------------------------------------------------------


def benchmark_guardrails(warmup: int = 3) -> dict:
    print(f"\n{'='*60}")
    print("Category 3: Guardrails Detection")
    print(f"{'='*60}")

    corpus_path = BENCHMARK_CORPUS_DIR / "guardrails-cases.jsonl"
    if not corpus_path.exists():
        print("  SKIP: guardrails-cases.jsonl not found")
        return {"skipped": True, "reason": "corpus file not found"}

    corpus = load_jsonl(corpus_path)
    print(f"  Corpus: {len(corpus)} entries")

    for _ in range(warmup):
        try:
            CLIENT.post(f"{GUARDRAILS_URL}/guard/input", json={
                "messages": [{"role": "user", "content": "warmup"}],
                "sensitivity_level": "PUBLIC",
            })
        except Exception:
            pass

    correct = 0
    total = 0
    latencies: list[float] = []
    per_rail_tp: dict[str, int] = Counter()
    per_rail_fn: dict[str, int] = Counter()
    per_rail_fp: dict[str, int] = Counter()
    details: list[dict] = []

    for entry in corpus:
        text = entry["text"]
        expected_rails = set(entry.get("expected_rails", []))
        should_block = entry.get("should_block", False)
        sensitivity = entry.get("sensitivity_level", "PUBLIC")
        endpoint = entry.get("endpoint", "input")

        t0 = time.perf_counter()
        try:
            if endpoint == "output":
                resp = CLIENT.post(f"{GUARDRAILS_URL}/guard/output", json={
                    "response_text": text,
                    "original_sensitivity": sensitivity,
                    "model_source": "gemini",
                    "redacted_entities": entry.get("redacted_entities"),
                })
            else:
                resp = CLIENT.post(f"{GUARDRAILS_URL}/guard/input", json={
                    "messages": [{"role": "user", "content": text}],
                    "sensitivity_level": sensitivity,
                    "intended_route": "gemini",
                })
            resp.raise_for_status()
            data = resp.json()
            elapsed = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        if endpoint == "output":
            actual_blocked = not data.get("clean", True)
            actual_rails_list = []
            for f in data.get("findings", []):
                finding_type = f.get("type", "")
                if finding_type == "RECONSTRUCTION":
                    actual_rails_list.append("reconstruction_detection")
                elif finding_type in ("SECRET", "PII"):
                    actual_rails_list.append("output_scan")
            actual_rails = set(actual_rails_list)
        else:
            actual_blocked = not data.get("allowed", True)
            actual_rails = set(data.get("rails_triggered", []))

        total += 1
        if actual_blocked == should_block:
            correct += 1

        for rail in expected_rails:
            if rail in actual_rails:
                per_rail_tp[rail] += 1
            else:
                per_rail_fn[rail] += 1
        for rail in actual_rails:
            if rail not in expected_rails:
                per_rail_fp[rail] += 1

        details.append({
            "text": text[:60],
            "expected_block": should_block,
            "actual_block": actual_blocked,
            "expected_rails": sorted(expected_rails),
            "actual_rails": sorted(actual_rails),
            "correct": actual_blocked == should_block,
        })

    accuracy = correct / total if total > 0 else 0.0

    per_rail_metrics = {}
    all_rails = sorted(set(list(per_rail_tp.keys()) + list(per_rail_fn.keys())))
    for rail in all_rails:
        tp = per_rail_tp[rail]
        fn = per_rail_fn[rail]
        fp = per_rail_fp[rail]
        tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        fpr = fp / (fp + (total - tp - fn)) if (fp + (total - tp - fn)) > 0 else 0.0
        per_rail_metrics[rail] = {
            "true_positive_rate": round(tpr, 4),
            "false_positive_rate": round(fpr, 4),
            "tp": tp, "fn": fn, "fp": fp,
        }

    result = {
        "accuracy": round(accuracy, 4),
        "correct": correct,
        "total": total,
        "per_rail": per_rail_metrics,
        "latency": latency_stats(latencies),
        "details": details,
    }

    print(f"  Accuracy: {accuracy*100:.1f}% ({correct}/{total})")
    for rail, m in sorted(per_rail_metrics.items()):
        print(f"    {rail:30s} TPR={m['true_positive_rate']:.2f}  FPR={m['false_positive_rate']:.2f}")
    return result


# ---------------------------------------------------------------------------
# Category 4: Egress Guard
# ---------------------------------------------------------------------------


def benchmark_egress_guard(warmup: int = 3) -> dict:
    print(f"\n{'='*60}")
    print("Category 4: Egress Guard")
    print(f"{'='*60}")

    corpus_path = BENCHMARK_CORPUS_DIR / "egress-guard-cases.jsonl"
    if not corpus_path.exists():
        print("  SKIP: egress-guard-cases.jsonl not found")
        return {"skipped": True, "reason": "corpus file not found"}

    corpus = load_jsonl(corpus_path)
    print(f"  Corpus: {len(corpus)} entries")

    for _ in range(warmup):
        try:
            CLIENT.post(f"{EGRESS_GUARD_URL}/guard/egress", json={
                "redacted_text": "warmup <PERSON_1>",
                "sensitivity_level": "INTERNAL",
                "entity_types_redacted": ["PERSON"],
            })
        except Exception:
            pass

    correct = 0
    total = 0
    latencies: list[float] = []
    per_rail_tp: dict[str, int] = Counter()
    per_rail_fn: dict[str, int] = Counter()
    false_positives = 0
    details: list[dict] = []

    for entry in corpus:
        expected_approved = entry.get("expected_approved", True)
        expected_rail = entry.get("expected_rail")

        t0 = time.perf_counter()
        try:
            resp = CLIENT.post(f"{EGRESS_GUARD_URL}/guard/egress", json={
                "redacted_text": entry["redacted_text"],
                "sensitivity_level": entry.get("sensitivity_level", "INTERNAL"),
                "entity_types_redacted": entry.get("entity_types_redacted", []),
            })
            resp.raise_for_status()
            data = resp.json()
            elapsed = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        actual_approved = data.get("approved", True)
        actual_rails = data.get("rails_triggered", [])
        total += 1

        if actual_approved == expected_approved:
            correct += 1
        elif actual_approved and not expected_approved:
            if expected_rail:
                per_rail_fn[expected_rail] += 1
        elif not actual_approved and expected_approved:
            false_positives += 1

        if expected_rail:
            if expected_rail in actual_rails:
                per_rail_tp[expected_rail] += 1
            elif not actual_approved:
                pass
            else:
                per_rail_fn[expected_rail] += 1

        details.append({
            "text": entry["redacted_text"][:60],
            "expected_approved": expected_approved,
            "actual_approved": actual_approved,
            "expected_rail": expected_rail,
            "actual_rails": actual_rails,
            "correct": actual_approved == expected_approved,
        })

    accuracy = correct / total if total > 0 else 0.0
    fpr = false_positives / total if total > 0 else 0.0

    per_rail_metrics = {}
    all_rails = sorted(set(list(per_rail_tp.keys()) + list(per_rail_fn.keys())))
    for rail in all_rails:
        tp = per_rail_tp[rail]
        fn = per_rail_fn[rail]
        detection_rate = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        per_rail_metrics[rail] = {
            "detection_rate": round(detection_rate, 4),
            "tp": tp, "fn": fn,
        }

    result = {
        "accuracy": round(accuracy, 4),
        "correct": correct,
        "total": total,
        "false_positive_rate": round(fpr, 4),
        "per_rail": per_rail_metrics,
        "latency": latency_stats(latencies),
        "details": details,
    }

    print(f"  Accuracy: {accuracy*100:.1f}% ({correct}/{total})  FPR: {fpr*100:.1f}%")
    for rail, m in sorted(per_rail_metrics.items()):
        print(f"    {rail:35s} detection={m['detection_rate']:.2f}")
    return result


# ---------------------------------------------------------------------------
# Category 5: End-to-End Pipeline
# ---------------------------------------------------------------------------

E2E_SCENARIOS = [
    {
        "name": "REDACT_THEN_SAAS (PUBLIC)",
        "path": "REDACT_THEN_SAAS",
        "text": "What is the difference between REST and GraphQL?",
        "complexity": "SIMPLE",
    },
    {
        "name": "REDACT_THEN_SAAS (PUBLIC, COMPLEX)",
        "path": "REDACT_THEN_SAAS",
        "text": "Explain the CAP theorem and its implications for distributed databases",
        "complexity": "COMPLEX",
    },
    {
        "name": "REDACT_THEN_SAAS (PUBLIC, MEDIUM)",
        "path": "REDACT_THEN_SAAS",
        "text": "How does garbage collection work in modern JVMs?",
        "complexity": "MEDIUM",
    },
    {
        "name": "REDACT_THEN_SAAS (INTERNAL)",
        "path": "REDACT_THEN_SAAS",
        "text": "Analyze the architecture of Project Phoenix deployed on ironman.cjlabs.dev. The lead engineer Sarah Chen designed 5 microservices. Contact: sarah.chen@company.com",
        "complexity": "COMPLEX",
    },
    {
        "name": "REDACT_THEN_SAAS (INTERNAL, email)",
        "path": "REDACT_THEN_SAAS",
        "text": "Send the deployment logs from homelab-maas to admin@cjlabs.dev for review by John Martinez",
        "complexity": "MEDIUM",
    },
    {
        "name": "REDACT_THEN_SAAS (INTERNAL, infra)",
        "path": "REDACT_THEN_SAAS",
        "text": "Debug the ollama-qwen36 service on worker-02. Employee EMP-44821 reported latency issues at 192.168.1.50",
        "complexity": "MEDIUM",
    },
    {
        "name": "LOCAL_ONLY (CONFIDENTIAL)",
        "path": "LOCAL_ONLY",
        "text": "What is the salary range for senior engineers on the platform team?",
        "complexity": "SIMPLE",
    },
    {
        "name": "LOCAL_ONLY (NEVER_EGRESS)",
        "path": "LOCAL_ONLY",
        "text": "Here is the SSH private key for the production bastion host",
        "complexity": "SIMPLE",
    },
    {
        "name": "LOCAL_ONLY (REGULATED)",
        "path": "LOCAL_ONLY",
        "text": "Review the patient records for clinical trial participant 7829",
        "complexity": "MEDIUM",
    },
]


def benchmark_e2e() -> dict:
    print(f"\n{'='*60}")
    print("Category 5: End-to-End Pipeline")
    print(f"{'='*60}")

    results = []

    for scenario in E2E_SCENARIOS:
        steps: list[dict] = []
        total_start = time.perf_counter()

        # Step 1: Classify
        t0 = time.perf_counter()
        try:
            resp = CLIENT.post(f"{CLASSIFIER_URL}/classify", json={
                "text": scenario["text"],
                "complexity_tier": scenario["complexity"],
            })
            resp.raise_for_status()
            classify_data = resp.json()
            classify_ms = (time.perf_counter() - t0) * 1000
            steps.append({"step": "classify", "latency_ms": round(classify_ms, 2), "success": True})
        except Exception as e:
            steps.append({"step": "classify", "latency_ms": 0, "success": False, "error": str(e)})
            results.append({
                "scenario": scenario["name"],
                "path": scenario["path"],
                "steps": steps,
                "total_ms": 0,
                "success": False,
            })
            continue

        sensitivity = classify_data.get("sensitivity_level", "PUBLIC")
        routing_action = classify_data.get("routing_action", "REDACT_THEN_SAAS")

        if routing_action == "LOCAL_ONLY" or scenario["path"] == "LOCAL_ONLY":
            total_ms = (time.perf_counter() - total_start) * 1000
            results.append({
                "scenario": scenario["name"],
                "path": "LOCAL_ONLY",
                "sensitivity": sensitivity,
                "routing_action": routing_action,
                "steps": steps,
                "total_ms": round(total_ms, 2),
                "success": True,
            })
            print(f"  {scenario['name']:40s} LOCAL_ONLY  {total_ms:8.1f}ms")
            continue

        # Step 2: Input rails
        t0 = time.perf_counter()
        try:
            resp = CLIENT.post(f"{GUARDRAILS_URL}/guard/input", json={
                "messages": [{"role": "user", "content": scenario["text"]}],
                "sensitivity_level": sensitivity,
                "intended_route": "gemini",
            })
            resp.raise_for_status()
            guard_data = resp.json()
            guard_ms = (time.perf_counter() - t0) * 1000
            steps.append({"step": "input_rails", "latency_ms": round(guard_ms, 2), "success": True})
        except Exception as e:
            steps.append({"step": "input_rails", "latency_ms": 0, "success": False, "error": str(e)})

        # Step 3: Redact
        t0 = time.perf_counter()
        try:
            resp = CLIENT.post(f"{REDACTION_URL}/redact", json={"text": scenario["text"]})
            resp.raise_for_status()
            redact_data = resp.json()
            redact_ms = (time.perf_counter() - t0) * 1000
            steps.append({"step": "redact", "latency_ms": round(redact_ms, 2), "success": True})
        except Exception as e:
            steps.append({"step": "redact", "latency_ms": 0, "success": False, "error": str(e)})

        # Step 4: Egress guard
        t0 = time.perf_counter()
        try:
            entity_types = [e["type"] for e in redact_data.get("entities", [])]
            resp = CLIENT.post(f"{EGRESS_GUARD_URL}/guard/egress", json={
                "redacted_text": redact_data.get("redacted_text", ""),
                "sensitivity_level": sensitivity,
                "entity_types_redacted": entity_types,
                "mapping_id": redact_data.get("mapping_id", ""),
            })
            resp.raise_for_status()
            egress_data = resp.json()
            egress_ms = (time.perf_counter() - t0) * 1000
            steps.append({"step": "egress_guard", "latency_ms": round(egress_ms, 2), "success": True,
                          "approved": egress_data.get("approved", False)})
        except Exception as e:
            steps.append({"step": "egress_guard", "latency_ms": 0, "success": False, "error": str(e)})

        total_ms = (time.perf_counter() - total_start) * 1000
        results.append({
            "scenario": scenario["name"],
            "path": scenario["path"],
            "sensitivity": sensitivity,
            "routing_action": routing_action,
            "steps": steps,
            "total_ms": round(total_ms, 2),
            "success": True,
        })
        print(f"  {scenario['name']:40s} {scenario['path']:15s} {total_ms:8.1f}ms  ({len(steps)} steps)")

    return {"scenarios": results, "total_scenarios": len(results)}


# ---------------------------------------------------------------------------
# Category 6: Security Verification
# ---------------------------------------------------------------------------

SECURITY_TESTS = [
    {"pod": "guardrails-service", "expect_blocked": True},
    {"pod": "nemo-egress-guard", "expect_blocked": True},
    {"pod": "sensitivity-classifier", "expect_blocked": True},
    {"pod": "qdrant", "expect_blocked": True},
    {"pod": "redaction-service", "expect_blocked": False},
]

TARGET_URL = "https://generativelanguage.googleapis.com/v1/models"


def benchmark_security() -> dict:
    print(f"\n{'='*60}")
    print("Category 6: Security Verification (NetworkPolicy)")
    print(f"{'='*60}")

    results = []

    for test in SECURITY_TESTS:
        pod = test["pod"]
        expect_blocked = test["expect_blocked"]

        cmd = [
            "oc", "exec", "-n", "semantic-redacted",
            f"deploy/{pod}", "--",
            "curl", "--connect-timeout", "5", "-s", "-o", "/dev/null",
            "-w", "%{http_code}", TARGET_URL,
        ]

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            elapsed = (time.perf_counter() - t0) * 1000
            exit_code = proc.returncode
            http_code = proc.stdout.strip() if proc.stdout else ""
            blocked = exit_code != 0 or http_code == "000"
        except subprocess.TimeoutExpired:
            elapsed = (time.perf_counter() - t0) * 1000
            blocked = True
            exit_code = -1
            http_code = "TIMEOUT"
        except FileNotFoundError:
            print(f"  SKIP {pod}: 'oc' not found")
            results.append({
                "pod": pod,
                "expect_blocked": expect_blocked,
                "actual_blocked": None,
                "passed": None,
                "reason": "oc command not found",
            })
            continue

        passed = blocked == expect_blocked
        status = "PASS" if passed else "FAIL"
        action = "BLOCKED" if blocked else "ALLOWED"
        expected_action = "BLOCKED" if expect_blocked else "ALLOWED"

        results.append({
            "pod": pod,
            "expect_blocked": expect_blocked,
            "actual_blocked": blocked,
            "passed": passed,
            "exit_code": exit_code,
            "http_code": http_code,
            "latency_ms": round(elapsed, 2),
        })
        print(f"  {pod:25s} expected={expected_action:7s} actual={action:7s} [{status}] {elapsed:.0f}ms")

    passed_count = sum(1 for r in results if r.get("passed") is True)
    total_count = sum(1 for r in results if r.get("passed") is not None)

    return {
        "tests": results,
        "passed": passed_count,
        "total": total_count,
        "all_passed": passed_count == total_count,
    }


# ---------------------------------------------------------------------------
# Category 7: Fine-tuned Model Comparison
# ---------------------------------------------------------------------------


def benchmark_finetuned_comparison(prompts: list[dict]) -> dict:
    print(f"\n{'='*60}")
    print("Category 7: Fine-tuned Model Comparison")
    print(f"{'='*60}")

    sys.path.insert(0, str(PROJECT_ROOT / "src" / "training"))
    sys.path.insert(0, str(PROJECT_ROOT / "src" / "sensitivity-classifier"))

    try:
        from evaluator import evaluate, TIERS as EVAL_TIERS
    except ImportError:
        print("  SKIP: cannot import evaluator module")
        return {"skipped": True, "reason": "evaluator not importable"}

    eval_path = PROJECT_ROOT / "data" / "test-prompts"
    combined_eval = PROJECT_ROOT / "results" / "_combined_eval.jsonl"
    with open(combined_eval, "w") as out:
        for p in prompts:
            json.dump({"text": p["text"], "tier": p["expected"]}, out)
            out.write("\n")

    models = {
        "base": "all-MiniLM-L6-v2",
        "finetuned": "cnuland/semantic-routing-sensitivity",
    }

    comparison = {}
    for label, model_name in models.items():
        print(f"  Evaluating {label} model: {model_name}")
        try:
            result = evaluate(
                model_path=model_name,
                eval_path=combined_eval,
                anchors_path=ANCHORS_PATH,
                top_k=3,
            )
            comparison[label] = {
                "model": model_name,
                "accuracy": result.accuracy,
                "per_class_f1": result.per_class_f1,
                "confusion_matrix": result.confusion_matrix,
                "total_examples": result.total_examples,
                "correct": result.correct,
                "avg_latency_ms": result.avg_latency_ms,
            }
            print(f"    Accuracy: {result.accuracy*100:.1f}%  Avg latency: {result.avg_latency_ms:.1f}ms")
        except Exception as e:
            print(f"    ERROR: {e}")
            comparison[label] = {"model": model_name, "error": str(e)}

    # Compute deltas
    if "base" in comparison and "finetuned" in comparison:
        base = comparison.get("base", {})
        ft = comparison.get("finetuned", {})
        if "accuracy" in base and "accuracy" in ft:
            delta = {
                "accuracy_delta": round(ft["accuracy"] - base["accuracy"], 4),
                "per_class_f1_delta": {},
            }
            for tier in TIERS:
                base_f1 = base.get("per_class_f1", {}).get(tier, 0)
                ft_f1 = ft.get("per_class_f1", {}).get(tier, 0)
                delta["per_class_f1_delta"][tier] = round(ft_f1 - base_f1, 4)
            comparison["delta"] = delta

    # Cleanup temp file
    try:
        combined_eval.unlink()
    except Exception:
        pass

    return comparison


# ---------------------------------------------------------------------------
# Markdown report generation
# ---------------------------------------------------------------------------


def generate_markdown_report(results: dict) -> str:
    lines = [
        "# Privacy-Preserving Semantic Routing: Empirical Benchmark Report",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Cluster:** Homelab (`api.ironman.cjlabs.dev:6443`), namespace `semantic-redacted`",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
    ]

    # Summary table
    summary_rows = []
    if "classification" in results:
        c = results["classification"]
        summary_rows.append(f"| Classification | {c.get('accuracy', 0)*100:.1f}% | {c.get('macro_f1', 0):.4f} | {c.get('latency', {}).get('p50_ms', 0):.1f}ms |")
    if "redaction" in results:
        r = results["redaction"]
        summary_rows.append(f"| Redaction | {r.get('overall_recall', 0)*100:.1f}% recall | {r.get('overall_f1', 0):.4f} | {r.get('redact_latency', {}).get('p50_ms', 0):.1f}ms |")
    if "guardrails" in results:
        g = results["guardrails"]
        summary_rows.append(f"| Guardrails | {g.get('accuracy', 0)*100:.1f}% | — | {g.get('latency', {}).get('p50_ms', 0):.1f}ms |")
    if "egress_guard" in results:
        e = results["egress_guard"]
        summary_rows.append(f"| Egress Guard | {e.get('accuracy', 0)*100:.1f}% | — | {e.get('latency', {}).get('p50_ms', 0):.1f}ms |")

    if summary_rows:
        lines.extend([
            "| Component | Accuracy/Recall | F1 | Latency (p50) |",
            "| --- | --- | --- | --- |",
        ])
        lines.extend(summary_rows)
        lines.append("")

    # Category 1: Classification
    if "classification" in results:
        c = results["classification"]
        lines.extend([
            "---",
            "",
            "## 1. Sensitivity Classification",
            "",
            f"**Overall Accuracy:** {c.get('accuracy', 0)*100:.1f}% ({c.get('correct', 0)}/{c.get('total', 0)})",
            f"**Macro F1:** {c.get('macro_f1', 0):.4f}  |  **Weighted F1:** {c.get('weighted_f1', 0):.4f}",
            f"**Throughput:** {c.get('throughput_rps', 0):.0f} req/s",
            "",
            "### Source Distribution",
            "",
        ])
        for source, count in sorted(c.get("source_distribution", {}).items()):
            lines.append(f"- **{source}**: {count} prompts")

        if c.get("fast_path_accuracy") is not None:
            lines.append(f"- Fast-path accuracy: {c['fast_path_accuracy']*100:.1f}% ({c['fast_path_total']} prompts)")
        if c.get("embedding_accuracy") is not None:
            lines.append(f"- Embedding accuracy: {c['embedding_accuracy']*100:.1f}% ({c['embedding_total']} prompts)")

        lines.extend(["", "### Per-Class Metrics", "",
                       "| Level | Precision | Recall | F1 | Support |",
                       "| --- | --- | --- | --- | --- |"])
        for tier in TIERS:
            m = c.get("per_class", {}).get(tier, {})
            lines.append(f"| {tier} | {m.get('precision', 0):.4f} | {m.get('recall', 0):.4f} | {m.get('f1', 0):.4f} | {m.get('support', 0)} |")

        lines.extend(["", "### Confusion Matrix", "",
                       "| True \\ Predicted | " + " | ".join(TIERS) + " |",
                       "| --- | " + " | ".join(["---"] * len(TIERS)) + " |"])
        cm = c.get("confusion_matrix", [])
        for i, tier in enumerate(TIERS):
            if i < len(cm):
                row = " | ".join(str(cm[i][j]) for j in range(len(TIERS)))
                lines.append(f"| {tier} | {row} |")

        lines.extend(["", "### Latency", ""])
        lat = c.get("latency", {})
        lines.extend([
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Mean | {lat.get('mean_ms', 0):.2f}ms |",
            f"| Stdev | {lat.get('stdev_ms', 0):.2f}ms |",
            f"| P50 | {lat.get('p50_ms', 0):.2f}ms |",
            f"| P95 | {lat.get('p95_ms', 0):.2f}ms |",
            f"| P99 | {lat.get('p99_ms', 0):.2f}ms |",
            f"| Min | {lat.get('min_ms', 0):.2f}ms |",
            f"| Max | {lat.get('max_ms', 0):.2f}ms |",
            "",
        ])

        misses = [p for p in c.get("predictions", []) if not p.get("correct")]
        if misses:
            lines.extend(["### Misclassified Prompts", "",
                           "| Text | Expected | Predicted | Source | Confidence |",
                           "| --- | --- | --- | --- | --- |"])
            for m in misses[:25]:
                lines.append(f"| {m['text']} | {m['expected']} | {m['predicted']} | {m.get('source', '')} | {m.get('confidence', 0):.3f} |")
            lines.append("")

    # Category 2: Redaction
    if "redaction" in results and not results["redaction"].get("skipped"):
        r = results["redaction"]
        lines.extend([
            "---",
            "",
            "## 2. Redaction Coverage",
            "",
            f"**Overall Recall:** {r.get('overall_recall', 0)*100:.1f}%  |  **Precision:** {r.get('overall_precision', 0)*100:.1f}%  |  **F1:** {r.get('overall_f1', 0):.4f}",
            f"**Roundtrip Fidelity:** {r.get('roundtrip_fidelity', 0)*100:.1f}% ({r.get('roundtrip_total', 0)} tests)",
            "",
            "### Per-Entity Metrics",
            "",
            "| Entity Type | TP | FN | FP | Recall | Precision | F1 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ])
        for t, m in sorted(r.get("per_type", {}).items()):
            lines.append(f"| {t} | {m['tp']} | {m['fn']} | {m['fp']} | {m['recall']:.4f} | {m['precision']:.4f} | {m['f1']:.4f} |")

        lines.extend(["", "### Latency", ""])
        rlat = r.get("redact_latency", {})
        lines.extend([
            f"| Operation | P50 | P95 | P99 |",
            f"| --- | --- | --- | --- |",
            f"| Redact | {rlat.get('p50_ms', 0):.1f}ms | {rlat.get('p95_ms', 0):.1f}ms | {rlat.get('p99_ms', 0):.1f}ms |",
        ])
        restore_lat = r.get("restore_latency", {})
        if restore_lat.get("n", 0) > 0:
            lines.append(f"| Restore | {restore_lat.get('p50_ms', 0):.1f}ms | {restore_lat.get('p95_ms', 0):.1f}ms | {restore_lat.get('p99_ms', 0):.1f}ms |")
        lines.append("")

    # Category 3: Guardrails
    if "guardrails" in results and not results["guardrails"].get("skipped"):
        g = results["guardrails"]
        lines.extend([
            "---",
            "",
            "## 3. Guardrails Detection",
            "",
            f"**Accuracy:** {g.get('accuracy', 0)*100:.1f}% ({g.get('correct', 0)}/{g.get('total', 0)})",
            "",
            "### Per-Rail Metrics",
            "",
            "| Rail | TPR | FPR | TP | FN | FP |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for rail, m in sorted(g.get("per_rail", {}).items()):
            lines.append(f"| {rail} | {m.get('true_positive_rate', 0):.4f} | {m.get('false_positive_rate', 0):.4f} | {m['tp']} | {m['fn']} | {m['fp']} |")

        glat = g.get("latency", {})
        lines.extend([
            "",
            f"**Latency:** p50={glat.get('p50_ms', 0):.1f}ms  p95={glat.get('p95_ms', 0):.1f}ms  p99={glat.get('p99_ms', 0):.1f}ms",
            "",
        ])

    # Category 4: Egress Guard
    if "egress_guard" in results and not results["egress_guard"].get("skipped"):
        e = results["egress_guard"]
        lines.extend([
            "---",
            "",
            "## 4. Egress Guard",
            "",
            f"**Accuracy:** {e.get('accuracy', 0)*100:.1f}% ({e.get('correct', 0)}/{e.get('total', 0)})",
            f"**False Positive Rate:** {e.get('false_positive_rate', 0)*100:.1f}%",
            "",
            "### Per-Rail Detection",
            "",
            "| Rail | Detection Rate | TP | FN |",
            "| --- | --- | --- | --- |",
        ])
        for rail, m in sorted(e.get("per_rail", {}).items()):
            lines.append(f"| {rail} | {m.get('detection_rate', 0)*100:.1f}% | {m['tp']} | {m['fn']} |")

        elat = e.get("latency", {})
        lines.extend([
            "",
            f"**Latency:** p50={elat.get('p50_ms', 0):.1f}ms  p95={elat.get('p95_ms', 0):.1f}ms",
            "",
        ])

    # Category 5: E2E
    if "e2e" in results:
        e2e = results["e2e"]
        lines.extend([
            "---",
            "",
            "## 5. End-to-End Pipeline Latency",
            "",
            "| Scenario | Path | Total (ms) | Steps |",
            "| --- | --- | --- | --- |",
        ])
        for s in e2e.get("scenarios", []):
            step_summary = ", ".join(
                f"{st['step']}={st.get('latency_ms', 0):.0f}ms"
                for st in s.get("steps", [])
            )
            lines.append(f"| {s['scenario']} | {s['path']} | {s.get('total_ms', 0):.1f} | {step_summary} |")
        lines.append("")

    # Category 6: Security
    if "security" in results:
        sec = results["security"]
        lines.extend([
            "---",
            "",
            "## 6. Security Verification (NetworkPolicy)",
            "",
            f"**Result:** {sec.get('passed', 0)}/{sec.get('total', 0)} tests passed",
            "",
            "| Pod | Expected | Actual | Result | Latency |",
            "| --- | --- | --- | --- | --- |",
        ])
        for t in sec.get("tests", []):
            expected = "BLOCKED" if t["expect_blocked"] else "ALLOWED"
            actual = "BLOCKED" if t.get("actual_blocked") else "ALLOWED" if t.get("actual_blocked") is not None else "SKIP"
            status = "PASS" if t.get("passed") else "FAIL" if t.get("passed") is not None else "SKIP"
            lines.append(f"| {t['pod']} | {expected} | {actual} | {status} | {t.get('latency_ms', 0):.0f}ms |")
        lines.append("")

    # Category 7: Fine-tuned comparison
    if "finetuned_comparison" in results and not results["finetuned_comparison"].get("skipped"):
        ft = results["finetuned_comparison"]
        lines.extend([
            "---",
            "",
            "## 7. Fine-tuned Model Comparison",
            "",
        ])

        if "base" in ft and "finetuned" in ft:
            base = ft["base"]
            finetuned = ft["finetuned"]
            delta = ft.get("delta", {})

            lines.extend([
                "| Metric | Base (MiniLM) | Fine-tuned | Delta |",
                "| --- | --- | --- | --- |",
                f"| Accuracy | {base.get('accuracy', 0)*100:.1f}% | {finetuned.get('accuracy', 0)*100:.1f}% | {delta.get('accuracy_delta', 0)*100:+.1f}% |",
                f"| Avg Latency | {base.get('avg_latency_ms', 0):.1f}ms | {finetuned.get('avg_latency_ms', 0):.1f}ms | — |",
            ])

            lines.extend(["", "### Per-Class F1 Comparison", "",
                           "| Level | Base F1 | Fine-tuned F1 | Delta |",
                           "| --- | --- | --- | --- |"])
            for tier in TIERS:
                base_f1 = base.get("per_class_f1", {}).get(tier, 0)
                ft_f1 = finetuned.get("per_class_f1", {}).get(tier, 0)
                d = delta.get("per_class_f1_delta", {}).get(tier, 0)
                lines.append(f"| {tier} | {base_f1:.4f} | {ft_f1:.4f} | {d:+.4f} |")

            # Show both confusion matrices
            for label in ["base", "finetuned"]:
                model_data = ft.get(label, {})
                cm = model_data.get("confusion_matrix", [])
                if cm:
                    model_name = model_data.get("model", label)
                    lines.extend(["", f"### Confusion Matrix: {model_name}", "",
                                   "| True \\ Pred | " + " | ".join(TIERS) + " |",
                                   "| --- | " + " | ".join(["---"] * len(TIERS)) + " |"])
                    for i, tier in enumerate(TIERS):
                        if i < len(cm):
                            row = " | ".join(str(cm[i][j]) for j in range(len(TIERS)))
                            lines.append(f"| {tier} | {row} |")

        lines.append("")

    lines.extend([
        "---",
        "",
        "*Generated by `benchmarks/run_benchmarks.py`*",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

CATEGORIES = {
    "classification": "Classification Accuracy",
    "redaction": "Redaction Coverage",
    "guardrails": "Guardrails Detection",
    "egress_guard": "Egress Guard",
    "e2e": "End-to-End Pipeline",
    "security": "Security Verification",
    "finetuned": "Fine-tuned Model Comparison",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark suite for semantic routing pipeline")
    parser.add_argument("--all", action="store_true", help="Run all benchmark categories")
    parser.add_argument("--category", choices=list(CATEGORIES.keys()), action="append",
                        help="Run specific category (can be repeated)")
    parser.add_argument("--output", default="results", help="Output directory (default: results/)")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup iterations (default: 3)")
    parser.add_argument("--runs", type=int, default=1, help="Repeated runs per prompt for latency (default: 1)")
    args = parser.parse_args()

    if not args.all and not args.category:
        parser.error("Specify --all or --category <name>")

    categories = list(CATEGORIES.keys()) if args.all else (args.category or [])
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Privacy-Preserving Semantic Routing: Benchmark Suite")
    print("=" * 60)
    print(f"Categories: {', '.join(categories)}")
    print(f"Output: {output_dir}")
    print()

    print("Service health check:")
    service_status = check_all_services()
    print()

    prompts = load_all_test_prompts()
    print(f"Loaded {len(prompts)} test prompts")

    results: dict[str, Any] = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "categories": categories,
            "warmup": args.warmup,
            "runs": args.runs,
            "services": service_status,
            "prompt_count": len(prompts),
        }
    }

    if "classification" in categories:
        if service_status.get("classifier"):
            results["classification"] = benchmark_classification(prompts, warmup=args.warmup, runs=args.runs)
        else:
            print("\n  SKIP classification: classifier service unreachable")
            results["classification"] = {"skipped": True, "reason": "service unreachable"}

    if "redaction" in categories:
        if service_status.get("redaction"):
            results["redaction"] = benchmark_redaction(warmup=args.warmup, runs=args.runs)
        else:
            print("\n  SKIP redaction: redaction service unreachable")
            results["redaction"] = {"skipped": True, "reason": "service unreachable"}

    if "guardrails" in categories:
        if service_status.get("guardrails"):
            results["guardrails"] = benchmark_guardrails(warmup=args.warmup)
        else:
            print("\n  SKIP guardrails: guardrails service unreachable")
            results["guardrails"] = {"skipped": True, "reason": "service unreachable"}

    if "egress_guard" in categories:
        if service_status.get("egress_guard"):
            results["egress_guard"] = benchmark_egress_guard(warmup=args.warmup)
        else:
            print("\n  SKIP egress_guard: egress guard service unreachable")
            results["egress_guard"] = {"skipped": True, "reason": "service unreachable"}

    if "e2e" in categories:
        results["e2e"] = benchmark_e2e()

    if "security" in categories:
        results["security"] = benchmark_security()

    if "finetuned" in categories:
        results["finetuned_comparison"] = benchmark_finetuned_comparison(prompts)

    # Write JSON results
    json_path = output_dir / "benchmark-results.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nJSON results: {json_path}")

    # Write markdown report
    md_path = output_dir / "benchmark-report.md"
    report = generate_markdown_report(results)
    with open(md_path, "w") as f:
        f.write(report)
    print(f"Markdown report: {md_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
