"""Synthetic Data Generation for sensitivity classification training.

Uses an LLM (Kimi K2.6 or compatible OpenAI-format endpoint) to generate
diverse training examples across 6 strategies:
  - paraphrase: reword seed examples
  - domain_transfer: shift to different business domains
  - boundary: create edge cases near tier boundaries
  - hard_negative: create examples that look like one tier but belong to another
  - real_world: realistic enterprise scenarios
  - perturbation: add noise, typos, reformulations

Adapted from hello-chris-semantic-rlaif/src/training/sdg_generator.py.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml

logger = logging.getLogger(__name__)

TIERS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]

STRATEGY_PROMPTS = {
    "paraphrase": (
        "Rewrite the following text to convey the same meaning using different words "
        "and sentence structure. Keep the sensitivity level the same. "
        "Output ONLY the rewritten text, nothing else.\n\nOriginal: {text}"
    ),
    "domain_transfer": (
        "Write a NEW example text that belongs to the {tier} sensitivity tier "
        "but in a DIFFERENT business domain than the original. "
        "The text should be a realistic prompt or question someone might ask an AI assistant. "
        "Output ONLY the new text, nothing else.\n\nOriginal domain example: {text}"
    ),
    "boundary": (
        "Write a text that is a borderline case between {tier} and {adjacent_tier}. "
        "It should ultimately belong to {tier} but be tricky to classify. "
        "Make it a realistic enterprise prompt. Output ONLY the text, nothing else."
    ),
    "hard_negative": (
        "Write a text that LOOKS like it belongs to {decoy_tier} due to similar vocabulary, "
        "but actually belongs to {tier} based on its true intent and content. "
        "Make it realistic and tricky. Output ONLY the text, nothing else."
    ),
    "real_world": (
        "Write a realistic enterprise prompt or question that belongs to the {tier} sensitivity level. "
        "It should sound like something an employee would actually type into an AI assistant. "
        "Topic area: {domain}. Output ONLY the text, nothing else."
    ),
    "perturbation": (
        "Take this text and add minor realistic variations: slight rewording, "
        "casual tone, or minor typos. Keep the meaning and sensitivity level identical. "
        "Output ONLY the modified text, nothing else.\n\nOriginal: {text}"
    ),
}

DOMAINS = [
    "software engineering", "human resources", "finance", "legal",
    "healthcare", "cybersecurity", "infrastructure", "sales",
    "customer support", "compliance", "data engineering", "devops",
]

ADJACENT_TIERS = {
    "PUBLIC": "INTERNAL",
    "INTERNAL": "CONFIDENTIAL",
    "CONFIDENTIAL": "REGULATED",
    "REGULATED": "NEVER_EGRESS",
    "NEVER_EGRESS": "REGULATED",
}


@dataclass
class SDGConfig:
    endpoint: str = "https://kimi-k2-6-kserve-workload-svc.prelude-maas.svc:8000/v1/chat/completions"
    model: str = "kimi-k2-6"
    temperature: float = 0.8
    max_tokens: int = 200
    examples_per_tier: int = 100
    strategies: list[str] | None = None
    seed_path: str = "data/sensitivity-anchors/anchors.jsonl"
    output_path: str = "data/train/train.jsonl"
    request_delay: float = 0.5
    timeout: int = 30
    verify_ssl: bool = False

    def __post_init__(self) -> None:
        if self.strategies is None:
            self.strategies = list(STRATEGY_PROMPTS.keys())


def load_sdg_config(path: Path) -> SDGConfig:
    raw = yaml.safe_load(path.read_text())
    sdg = raw.get("sdg", raw)
    return SDGConfig(
        endpoint=sdg.get("endpoint", SDGConfig.endpoint),
        model=sdg.get("model", SDGConfig.model),
        temperature=sdg.get("temperature", 0.8),
        max_tokens=sdg.get("max_tokens", 200),
        examples_per_tier=sdg.get("examples_per_tier", 100),
        strategies=sdg.get("strategies"),
        seed_path=sdg.get("seed_path", "data/sensitivity-anchors/anchors.jsonl"),
        output_path=sdg.get("output_path", "data/train/train.jsonl"),
        request_delay=sdg.get("request_delay", 0.5),
        timeout=sdg.get("timeout", 30),
        verify_ssl=sdg.get("verify_ssl", False),
    )


def load_seeds(path: Path) -> dict[str, list[str]]:
    seeds: dict[str, list[str]] = {t: [] for t in TIERS}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            label = obj.get("tier") or obj.get("label")
            if label in seeds:
                seeds[label].append(obj["text"])
    return seeds


def _call_llm(config: SDGConfig, prompt: str) -> str | None:
    try:
        resp = requests.post(
            config.endpoint,
            json={
                "model": config.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            },
            timeout=config.timeout,
            verify=config.verify_ssl,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        content = msg.get("content")
        if content is None:
            content = msg.get("reasoning", "")
        if content:
            return content.strip()
        return None
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return None


def _generate_for_strategy(
    config: SDGConfig,
    strategy: str,
    tier: str,
    seeds: list[str],
    count: int,
    rng: random.Random,
) -> list[dict]:
    results = []
    template = STRATEGY_PROMPTS[strategy]

    for _ in range(count):
        if strategy == "paraphrase":
            text = rng.choice(seeds)
            prompt = template.format(text=text)
        elif strategy == "domain_transfer":
            text = rng.choice(seeds)
            prompt = template.format(tier=tier, text=text)
        elif strategy == "boundary":
            adjacent = ADJACENT_TIERS[tier]
            prompt = template.format(tier=tier, adjacent_tier=adjacent)
        elif strategy == "hard_negative":
            decoy = rng.choice([t for t in TIERS if t != tier])
            prompt = template.format(tier=tier, decoy_tier=decoy)
        elif strategy == "real_world":
            domain = rng.choice(DOMAINS)
            prompt = template.format(tier=tier, domain=domain)
        elif strategy == "perturbation":
            text = rng.choice(seeds)
            prompt = template.format(text=text)
        else:
            continue

        generated = _call_llm(config, prompt)
        if generated and len(generated) > 10:
            results.append({
                "text": generated,
                "tier": tier,
                "strategy": strategy,
                "source": "sdg",
            })

        time.sleep(config.request_delay)

    return results


def generate(config: SDGConfig, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    seeds = load_seeds(Path(config.seed_path))

    strategies = config.strategies or list(STRATEGY_PROMPTS.keys())
    per_strategy = max(1, config.examples_per_tier // len(strategies))

    all_examples = []
    for tier in TIERS:
        tier_seeds = seeds.get(tier, [])
        if not tier_seeds:
            logger.warning("No seeds for tier %s, skipping", tier)
            continue

        for strategy in strategies:
            logger.info("Generating %d examples: tier=%s strategy=%s", per_strategy, tier, strategy)
            examples = _generate_for_strategy(
                config, strategy, tier, tier_seeds, per_strategy, rng,
            )
            all_examples.extend(examples)
            logger.info("Got %d examples for %s/%s", len(examples), tier, strategy)

    logger.info("Total generated: %d examples across %d tiers", len(all_examples), len(TIERS))
    return all_examples


def save_examples(examples: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")
    logger.info("Saved %d examples to %s", len(examples), path)


def filter_duplicates(examples: list[dict], min_length: int = 15) -> list[dict]:
    seen = set()
    filtered = []
    for ex in examples:
        text = ex["text"].strip()
        if len(text) < min_length:
            continue
        text_lower = text.lower()
        if text_lower in seen:
            continue
        seen.add(text_lower)
        filtered.append(ex)
    logger.info("Filtered: %d -> %d examples", len(examples), len(filtered))
    return filtered
