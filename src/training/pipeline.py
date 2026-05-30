"""Training pipeline orchestration.

Runs the full flow: SDG -> filter -> split -> baseline benchmark ->
train -> fine-tuned benchmark -> comparison report.
"""

from __future__ import annotations

import json
import logging
import random
import sys
from pathlib import Path

import yaml

from .evaluator import evaluate, format_report, EvalResult
from .sdg_generator import (
    SDGConfig,
    generate,
    filter_duplicates,
    load_sdg_config,
    load_seeds,
    save_examples,
)
from .trainer import TrainingConfig, load_training_config, train

logger = logging.getLogger(__name__)

TIERS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]


def split_data(
    examples: list[dict],
    eval_ratio: float = 0.15,
    seed: int = 42,
) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    by_tier: dict[str, list[dict]] = {t: [] for t in TIERS}
    for ex in examples:
        tier = ex.get("tier") or ex.get("label")
        if tier in by_tier:
            by_tier[tier].append(ex)

    train_set, eval_set = [], []
    for tier in TIERS:
        items = by_tier[tier]
        rng.shuffle(items)
        split_idx = max(1, int(len(items) * eval_ratio))
        eval_set.extend(items[:split_idx])
        train_set.extend(items[split_idx:])

    rng.shuffle(train_set)
    rng.shuffle(eval_set)
    logger.info("Split: %d train, %d eval", len(train_set), len(eval_set))
    return train_set, eval_set


def run_pipeline(
    training_config_path: Path,
    sdg_config_path: Path,
    anchors_path: Path,
    skip_sdg: bool = False,
) -> dict:
    logger.info("=== Starting training pipeline ===")

    training_cfg = load_training_config(training_config_path)
    sdg_cfg = load_sdg_config(sdg_config_path)

    train_path = Path(training_cfg.train_path)
    eval_path = train_path.parent / "eval.jsonl"

    # Step 1: SDG
    if skip_sdg and train_path.exists():
        logger.info("Skipping SDG — using existing training data at %s", train_path)
    else:
        logger.info("Step 1: Generating synthetic data")
        seeds = load_seeds(anchors_path)
        seed_examples = []
        for tier, texts in seeds.items():
            for t in texts:
                seed_examples.append({"text": t, "tier": tier, "strategy": "seed", "source": "anchor"})

        generated = generate(sdg_cfg, seed=training_cfg.seed)
        all_examples = seed_examples + generated
        all_examples = filter_duplicates(all_examples)

        train_set, eval_set = split_data(all_examples, seed=training_cfg.seed)
        save_examples(train_set, train_path)
        save_examples(eval_set, eval_path)
        logger.info("SDG complete: %d train, %d eval", len(train_set), len(eval_set))

    # Step 2: Baseline evaluation
    logger.info("Step 2: Baseline evaluation with %s", training_cfg.base_model)
    baseline_result = evaluate(
        model_path=training_cfg.base_model,
        eval_path=eval_path,
        anchors_path=anchors_path,
    )
    logger.info("Baseline accuracy: %.1f%%", baseline_result.accuracy * 100)

    # Step 3: Fine-tuning
    logger.info("Step 3: Fine-tuning")
    output_path = train(training_cfg)
    logger.info("Fine-tuning complete, model at %s", output_path)

    # Step 4: Fine-tuned evaluation
    logger.info("Step 4: Fine-tuned model evaluation")
    finetuned_result = evaluate(
        model_path=str(output_path),
        eval_path=eval_path,
        anchors_path=anchors_path,
    )
    logger.info("Fine-tuned accuracy: %.1f%%", finetuned_result.accuracy * 100)

    # Step 5: Comparison report
    comparison = _build_comparison(baseline_result, finetuned_result, training_cfg)

    report_dir = Path("/app/models/reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    baseline_report = format_report(baseline_result)
    finetuned_report = format_report(finetuned_result)

    (report_dir / "baseline_eval.md").write_text(baseline_report)
    (report_dir / "finetuned_eval.md").write_text(finetuned_report)
    (report_dir / "comparison.json").write_text(json.dumps(comparison, indent=2))

    comparison_md = _format_comparison_md(comparison)
    (report_dir / "comparison.md").write_text(comparison_md)

    logger.info("=== Pipeline complete ===")
    logger.info("Baseline: %.1f%% -> Fine-tuned: %.1f%%",
                baseline_result.accuracy * 100, finetuned_result.accuracy * 100)

    return comparison


def _build_comparison(
    baseline: EvalResult,
    finetuned: EvalResult,
    config: TrainingConfig,
) -> dict:
    return {
        "base_model": config.base_model,
        "finetuned_model": config.output_dir,
        "loss_type": config.loss_type,
        "epochs": config.epochs,
        "baseline": {
            "accuracy": baseline.accuracy,
            "per_class_f1": baseline.per_class_f1,
            "avg_latency_ms": baseline.avg_latency_ms,
        },
        "finetuned": {
            "accuracy": finetuned.accuracy,
            "per_class_f1": finetuned.per_class_f1,
            "avg_latency_ms": finetuned.avg_latency_ms,
        },
        "improvement": {
            "accuracy_delta": round(finetuned.accuracy - baseline.accuracy, 4),
            "per_class_f1_delta": {
                tier: round(finetuned.per_class_f1.get(tier, 0) - baseline.per_class_f1.get(tier, 0), 4)
                for tier in TIERS
            },
        },
    }


def _format_comparison_md(comparison: dict) -> str:
    lines = [
        "# Model Comparison Report",
        "",
        f"**Base model:** {comparison['base_model']}",
        f"**Fine-tuned model:** {comparison['finetuned_model']}",
        f"**Loss:** {comparison['loss_type']} | **Epochs:** {comparison['epochs']}",
        "",
        "## Accuracy",
        "",
        f"| Model | Accuracy |",
        f"|-------|----------|",
        f"| Baseline | {comparison['baseline']['accuracy'] * 100:.1f}% |",
        f"| Fine-tuned | {comparison['finetuned']['accuracy'] * 100:.1f}% |",
        f"| **Delta** | **{comparison['improvement']['accuracy_delta'] * 100:+.1f}%** |",
        "",
        "## Per-Class F1",
        "",
        "| Tier | Baseline | Fine-tuned | Delta |",
        "|------|----------|------------|-------|",
    ]
    for tier in TIERS:
        b = comparison["baseline"]["per_class_f1"].get(tier, 0)
        f = comparison["finetuned"]["per_class_f1"].get(tier, 0)
        d = comparison["improvement"]["per_class_f1_delta"].get(tier, 0)
        lines.append(f"| {tier} | {b:.4f} | {f:.4f} | {d:+.4f} |")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    training_config = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("configs/training.yaml")
    sdg_config = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("configs/sdg.yaml")
    anchors = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("data/sensitivity-anchors/anchors.jsonl")
    skip = "--skip-sdg" in sys.argv

    run_pipeline(training_config, sdg_config, anchors, skip_sdg=skip)
