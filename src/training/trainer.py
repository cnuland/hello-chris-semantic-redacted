"""Fine-tuning trainer for sensitivity classification embedding models.

Adapted from hello-chris-semantic-rlaif/src/training/trainer.py.
Uses BatchAllTripletLoss with GROUP_BY_LABEL batch sampling to learn
separation between 5 sensitivity levels.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import yaml
from datasets import Dataset
from sentence_transformers import SentenceTransformer, losses
from sentence_transformers.trainer import SentenceTransformerTrainer
from sentence_transformers.training_args import SentenceTransformerTrainingArguments, BatchSamplers

logger = logging.getLogger(__name__)

TIERS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]
TIER_TO_LABEL = {t: i for i, t in enumerate(TIERS)}


@dataclass
class TrainingConfig:
    base_model: str = "all-MiniLM-L6-v2"
    output_dir: str = "models/finetuned/sensitivity-v1"
    train_path: str = "data/train/train.jsonl"
    epochs: int = 20
    batch_size: int = 32
    learning_rate: float = 2e-5
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01
    seed: int = 42
    evaluation_steps: int = 100
    fp16: bool = False
    loss_type: str = "batch_all_triplet"


def load_training_config(path: Path) -> TrainingConfig:
    raw = yaml.safe_load(path.read_text())
    training = raw.get("training", {})
    return TrainingConfig(
        base_model=raw.get("base_model", "all-MiniLM-L6-v2"),
        output_dir=raw.get("output_dir", "models/finetuned/sensitivity-v1"),
        train_path=raw.get("data", {}).get("train_path", "data/train/train.jsonl"),
        epochs=int(training.get("epochs", 20)),
        batch_size=int(training.get("batch_size", 32)),
        learning_rate=float(training.get("learning_rate", 2e-5)),
        warmup_ratio=float(training.get("warmup_ratio", 0.1)),
        weight_decay=float(training.get("weight_decay", 0.01)),
        seed=int(training.get("seed", 42)),
        evaluation_steps=int(training.get("evaluation_steps", 100)),
        fp16=bool(training.get("fp16", False)),
        loss_type=training.get("loss_type", "batch_all_triplet"),
    )


def load_training_data(path: Path) -> list[dict]:
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def build_label_dataset(
    examples: list[dict],
    seed: int = 42,
) -> Dataset:
    """Build (sentence, label) dataset for BatchAllTripletLoss."""
    rng = random.Random(seed)

    items = [(ex["text"], TIER_TO_LABEL[ex["tier"]]) for ex in examples if ex["tier"] in TIER_TO_LABEL]
    rng.shuffle(items)

    sentences, labels = zip(*items) if items else ([], [])
    logger.info("Built label dataset: %d examples across %d tiers", len(sentences), len(set(labels)))
    return Dataset.from_dict({"sentence": list(sentences), "label": list(labels)})


def build_pair_dataset(
    examples: list[dict],
    seed: int = 42,
    max_pairs_per_tier: int = 500,
) -> Dataset:
    """Build all (anchor, positive) pairs from same-tier examples for MNRL."""
    rng = random.Random(seed)

    by_tier: dict[str, list[str]] = {t: [] for t in TIERS}
    for ex in examples:
        tier = ex["tier"]
        if tier in by_tier:
            by_tier[tier].append(ex["text"])

    anchors = []
    positives = []

    for tier in TIERS:
        texts = by_tier[tier]
        if len(texts) < 2:
            continue
        tier_pairs = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                tier_pairs.append((texts[i], texts[j]))
        rng.shuffle(tier_pairs)
        tier_pairs = tier_pairs[:max_pairs_per_tier]
        for a, p in tier_pairs:
            anchors.append(a)
            positives.append(p)

    combined = list(zip(anchors, positives))
    rng.shuffle(combined)
    anchors, positives = zip(*combined) if combined else ([], [])

    logger.info("Built %d training pairs from %d examples", len(anchors), len(examples))
    return Dataset.from_dict({"anchor": list(anchors), "positive": list(positives)})


def train(config: TrainingConfig) -> Path:
    """Run fine-tuning and return the output model path."""
    logger.info("Loading base model: %s", config.base_model)
    model = SentenceTransformer(config.base_model)

    logger.info("Loading training data: %s", config.train_path)
    train_data = load_training_data(Path(config.train_path))

    if config.loss_type == "batch_all_triplet":
        train_dataset = build_label_dataset(train_data, seed=config.seed)
        loss = losses.BatchAllTripletLoss(model)
        logger.info("Using BatchAllTripletLoss with %d labeled examples", len(train_dataset))
    elif config.loss_type == "batch_hard_triplet":
        train_dataset = build_label_dataset(train_data, seed=config.seed)
        loss = losses.BatchHardTripletLoss(model)
        logger.info("Using BatchHardTripletLoss with %d labeled examples", len(train_dataset))
    else:
        train_dataset = build_pair_dataset(train_data, seed=config.seed)
        loss = losses.MultipleNegativesRankingLoss(model)
        logger.info("Using MNRL with %d pairs", len(train_dataset))

    output_path = Path(config.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    use_triplet = config.loss_type in ("batch_all_triplet", "batch_hard_triplet")
    args = SentenceTransformerTrainingArguments(
        output_dir=str(output_path),
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.batch_size,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        weight_decay=config.weight_decay,
        fp16=config.fp16,
        seed=config.seed,
        logging_steps=10,
        save_strategy="no",
        report_to="none",
        batch_sampler=BatchSamplers.GROUP_BY_LABEL if use_triplet else BatchSamplers.BATCH_SAMPLER,
    )

    logger.info(
        "Training: %d examples, %d epochs, batch=%d, lr=%e, loss=%s",
        len(train_dataset), config.epochs, config.batch_size,
        config.learning_rate, config.loss_type,
    )

    trainer = SentenceTransformerTrainer(
        model=model,
        args=args,
        train_dataset=train_dataset,
        loss=loss,
    )
    trainer.train()
    model.save(str(output_path))

    logger.info("Model saved to %s", output_path)
    return output_path
