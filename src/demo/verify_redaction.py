#!/usr/bin/env python3
"""Verify redaction quality against test prompts and the RAG document.

Reads test prompts from data/test-prompts/ and, for prompts that contain
known entities, calls the redaction service /redact endpoint to check:
  - All expected entities are detected
  - Placeholders are deterministic (same input -> same placeholder)
  - Restore produces the original text

Also verifies the sensitive RAG document entities are detected.

Usage:
    python verify_redaction.py
    python verify_redaction.py --redaction-url http://redaction:8000
    python verify_redaction.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

try:
    import requests as _requests

    def _post(url: str, payload: dict, timeout: int = 30) -> dict:
        resp = _requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

except ImportError:
    import urllib.request

    def _post(url: str, payload: dict, timeout: int = 30) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEST_PROMPTS_DIR = _PROJECT_ROOT / "data" / "test-prompts"
_RAG_DOC = Path(__file__).resolve().parent / "sensitive_rag_doc.md"

# ---------------------------------------------------------------------------
# Test cases with known entities for entity-detection recall
# ---------------------------------------------------------------------------

_ENTITY_TEST_CASES: list[dict[str, Any]] = [
    {
        "text": (
            "Analyze the architecture of Project Phoenix deployed on the "
            "ironman.cjlabs.dev cluster. The lead engineer Sarah Chen "
            "designed a microservices system in the homelab-maas namespace "
            "with 5 services. Her contact is sarah.chen@company.com."
        ),
        "expected_entities": {
            "PERSON": ["Sarah Chen"],
            "EMAIL_ADDRESS": ["sarah.chen@company.com"],
        },
        "sensitivity": "INTERNAL",
    },
    {
        "text": (
            "Draft a performance improvement plan for Marcus Johnson. "
            "His current salary is $165,000 and he's been with the company "
            "for 3 years."
        ),
        "expected_entities": {
            "PERSON": ["Marcus Johnson"],
        },
        "sensitivity": "CONFIDENTIAL",
    },
    {
        "text": (
            "Send the invoice to John Martinez at john.martinez@acmecorp.com "
            "and CC Rachel Kim at rachel.kim@company.com."
        ),
        "expected_entities": {
            "PERSON": ["John Martinez", "Rachel Kim"],
            "EMAIL_ADDRESS": ["john.martinez@acmecorp.com", "rachel.kim@company.com"],
        },
        "sensitivity": "CONFIDENTIAL",
    },
    {
        "text": (
            "Process the GDPR deletion request for user account EU-2024-8891. "
            "The user's email is hans.mueller@example.de and phone is +49-30-12345678."
        ),
        "expected_entities": {
            "EMAIL_ADDRESS": ["hans.mueller@example.de"],
            "PHONE_NUMBER": ["+49-30-12345678"],
        },
        "sensitivity": "REGULATED",
    },
]

# Known entities in the sensitive RAG document
_RAG_KNOWN_ENTITIES: dict[str, list[str]] = {
    "PERSON": [
        "Sarah Chen", "Marcus Johnson", "David Park", "Rachel Kim",
        "James Wilson", "Lisa Wang", "Maria Santos",
    ],
    "EMAIL_ADDRESS": ["sarah.chen@company.com"],
    "IP_ADDRESS": ["45.33.32.156"],
    "ORGANIZATION": [
        "Acme Corp", "TechVision Industries", "GlobalMfg Inc",
        "DataFlow Systems", "Anthropic", "Datadog",
    ],
}


# ---------------------------------------------------------------------------
# Load test prompts from JSONL files
# ---------------------------------------------------------------------------


def _load_test_prompts() -> list[dict[str, str]]:
    """Load all test prompts from JSONL files."""
    prompts: list[dict[str, str]] = []
    if not _TEST_PROMPTS_DIR.exists():
        print(f"WARNING: Test prompts directory not found at {_TEST_PROMPTS_DIR}")
        return prompts

    for jsonl_file in sorted(_TEST_PROMPTS_DIR.glob("*.jsonl")):
        with open(jsonl_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    prompts.append(json.loads(line))
    return prompts


# ---------------------------------------------------------------------------
# Verification logic
# ---------------------------------------------------------------------------


def _verify_entity_detection(
    test_case: dict[str, Any],
    redaction_url: str,
    verbose: bool = False,
) -> dict[str, Any]:
    """Call /redact and check that expected entities are found."""
    text = test_case["text"]
    expected = test_case["expected_entities"]

    result: dict[str, Any] = {
        "text_preview": text[:80] + ("..." if len(text) > 80 else ""),
        "expected_entity_count": sum(len(v) for v in expected.values()),
        "detected_entity_count": 0,
        "entity_results": {},
        "passed": True,
        "errors": [],
    }

    try:
        resp = _post(
            f"{redaction_url}/redact",
            {"text": text, "sensitivity_level": test_case.get("sensitivity", "INTERNAL")},
        )
    except Exception as e:
        result["passed"] = False
        result["errors"].append(f"Redaction service error: {e}")
        return result

    detected_entities = resp.get("entities", [])
    result["detected_entity_count"] = len(detected_entities)

    # Build lookup of detected entities by type
    detected_by_type: dict[str, list[str]] = defaultdict(list)
    for ent in detected_entities:
        detected_by_type[ent["type"]].append(ent.get("original", ""))

    # Check each expected entity type
    for entity_type, expected_values in expected.items():
        detected_values = detected_by_type.get(entity_type, [])
        found = []
        missed = []

        for val in expected_values:
            if any(val in d or d in val for d in detected_values):
                found.append(val)
            else:
                missed.append(val)
                result["passed"] = False

        result["entity_results"][entity_type] = {
            "expected": expected_values,
            "found": found,
            "missed": missed,
            "recall": len(found) / len(expected_values) if expected_values else 1.0,
        }

    if verbose:
        print(f"    Detected: {[(e['type'], e.get('original', '')) for e in detected_entities]}")

    return result


def _verify_determinism(
    text: str,
    redaction_url: str,
    runs: int = 3,
) -> dict[str, Any]:
    """Verify that redacting the same text produces identical placeholders."""
    result: dict[str, Any] = {
        "text_preview": text[:60] + "...",
        "runs": runs,
        "deterministic": True,
        "errors": [],
    }

    redacted_texts: list[str] = []
    for i in range(runs):
        try:
            resp = _post(
                f"{redaction_url}/redact",
                {"text": text, "sensitivity_level": "INTERNAL"},
            )
            redacted_texts.append(resp.get("redacted_text", ""))
        except Exception as e:
            result["deterministic"] = False
            result["errors"].append(f"Run {i+1} failed: {e}")
            return result

    # All redacted texts should be identical
    if len(set(redacted_texts)) > 1:
        result["deterministic"] = False
        result["errors"].append(
            f"Got {len(set(redacted_texts))} distinct redacted versions across {runs} runs"
        )

    return result


def _verify_roundtrip(
    text: str,
    redaction_url: str,
) -> dict[str, Any]:
    """Verify that redact -> restore produces the original text."""
    result: dict[str, Any] = {
        "text_preview": text[:60] + "...",
        "roundtrip_match": False,
        "errors": [],
    }

    try:
        # Redact
        redact_resp = _post(
            f"{redaction_url}/redact",
            {"text": text, "sensitivity_level": "INTERNAL"},
        )
        redacted_text = redact_resp.get("redacted_text", "")
        mapping_id = redact_resp.get("mapping_id", "")

        if not mapping_id:
            # No entities detected, so redacted == original
            result["roundtrip_match"] = (redacted_text == text)
            return result

        # Restore
        restore_resp = _post(
            f"{redaction_url}/restore",
            {"text": redacted_text, "mapping_id": mapping_id},
        )
        restored_text = restore_resp.get("restored_text", "")
        result["roundtrip_match"] = (restored_text == text)

        if not result["roundtrip_match"]:
            result["errors"].append(
                f"Roundtrip mismatch:\n"
                f"  Original: {text[:100]}\n"
                f"  Restored: {restored_text[:100]}"
            )

    except Exception as e:
        result["errors"].append(f"Roundtrip test error: {e}")

    return result


def _verify_rag_document(
    redaction_url: str,
    verbose: bool = False,
) -> dict[str, Any]:
    """Verify entity detection against the sensitive RAG document."""
    result: dict[str, Any] = {
        "total_known": sum(len(v) for v in _RAG_KNOWN_ENTITIES.values()),
        "total_detected": 0,
        "total_missed": 0,
        "entity_results": {},
        "recall": 0.0,
    }

    if not _RAG_DOC.exists():
        result["errors"] = [f"RAG document not found at {_RAG_DOC}"]
        return result

    doc_text = _RAG_DOC.read_text()
    sections = doc_text.split("\n---\n")
    detected_originals: set[str] = set()

    for i, section in enumerate(sections):
        section = section.strip()
        if not section or len(section) < 20:
            continue

        try:
            resp = _post(
                f"{redaction_url}/redact",
                {"text": section, "sensitivity_level": "NEVER_EGRESS"},
            )
            for entity in resp.get("entities", []):
                detected_originals.add(entity["original"])
            if verbose:
                print(f"    Section {i}: {resp.get('entity_count', 0)} entities")
        except Exception as e:
            print(f"    Section {i}: ERROR - {e}")

    for entity_type, known_values in _RAG_KNOWN_ENTITIES.items():
        if not known_values:
            continue

        found = [v for v in known_values if v in detected_originals]
        missed = [v for v in known_values if v not in detected_originals]

        result["total_detected"] += len(found)
        result["total_missed"] += len(missed)

        result["entity_results"][entity_type] = {
            "expected": known_values,
            "found": found,
            "missed": missed,
            "recall": len(found) / len(known_values) if known_values else 1.0,
        }

    total_known = result["total_known"]
    result["recall"] = result["total_detected"] / total_known if total_known > 0 else 0.0
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify redaction quality against test prompts"
    )
    parser.add_argument(
        "--redaction-url",
        default=os.environ.get("REDACTION_SERVICE_URL", "http://localhost:8000"),
        help="Redaction service URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed detection results",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Redaction Quality Verification")
    print("=" * 60)
    print(f"Redaction service: {args.redaction_url}")
    print()

    # -------------------------------------------------------------------
    # Part 1: Entity detection recall (curated test cases)
    # -------------------------------------------------------------------
    print("--- Part 1: Entity Detection Recall ---")

    recall_by_type: dict[str, list[float]] = defaultdict(list)
    total_passed = 0
    total_tests = 0

    for idx, test_case in enumerate(_ENTITY_TEST_CASES):
        if not test_case["expected_entities"]:
            continue

        total_tests += 1
        print(f"\n  Test {idx+1}: {test_case['text'][:70]}...")
        result = _verify_entity_detection(test_case, args.redaction_url, args.verbose)

        if result["passed"]:
            total_passed += 1
            print(f"    PASS ({result['detected_entity_count']} entities detected)")
        else:
            print(f"    FAIL")
            for err in result["errors"]:
                print(f"      Error: {err}")
            for etype, eres in result["entity_results"].items():
                if eres["missed"]:
                    print(f"      Missed {etype}: {eres['missed']}")

        for etype, eres in result.get("entity_results", {}).items():
            recall_by_type[etype].append(eres["recall"])

    print(f"\n  Entity Detection: {total_passed}/{total_tests} tests passed")
    print("\n  Recall per entity type:")
    for etype, recalls in sorted(recall_by_type.items()):
        avg_recall = sum(recalls) / len(recalls) if recalls else 0.0
        print(f"    {etype:<20}: {avg_recall:.1%} ({len(recalls)} tests)")

    # -------------------------------------------------------------------
    # Part 2: Placeholder determinism
    # -------------------------------------------------------------------
    print("\n--- Part 2: Placeholder Determinism ---")

    determinism_text = (
        "Sarah Chen works on Project Phoenix at ironman.cjlabs.dev. "
        "Contact: sarah.chen@company.com"
    )
    det_result = _verify_determinism(determinism_text, args.redaction_url)
    if det_result["deterministic"]:
        print(f"  PASS: Placeholders are deterministic across {det_result['runs']} runs")
    else:
        print(f"  FAIL: Placeholders are NOT deterministic")
        for err in det_result["errors"]:
            print(f"    {err}")

    # -------------------------------------------------------------------
    # Part 3: Roundtrip (redact -> restore)
    # -------------------------------------------------------------------
    print("\n--- Part 3: Redact/Restore Roundtrip ---")

    roundtrip_passed = 0
    roundtrip_total = 0

    for idx, test_case in enumerate(_ENTITY_TEST_CASES):
        roundtrip_total += 1
        rt_result = _verify_roundtrip(test_case["text"], args.redaction_url)
        if rt_result["roundtrip_match"]:
            roundtrip_passed += 1
            if args.verbose:
                print(f"  Test {idx+1}: PASS")
        else:
            print(f"  Test {idx+1}: FAIL")
            for err in rt_result["errors"]:
                print(f"    {err}")

    print(f"\n  Roundtrip: {roundtrip_passed}/{roundtrip_total} tests passed")

    # -------------------------------------------------------------------
    # Part 4: RAG document entity detection
    # -------------------------------------------------------------------
    print("\n--- Part 4: RAG Document Entity Detection ---")

    rag_result = _verify_rag_document(args.redaction_url, args.verbose)
    print(f"\n  RAG entities: {rag_result['total_detected']}/{rag_result['total_known']} detected")

    for etype, eres in sorted(rag_result.get("entity_results", {}).items()):
        status = "PASS" if not eres["missed"] else "PARTIAL" if eres["found"] else "FAIL"
        print(f"    {etype:<20}: {len(eres['found'])}/{len(eres['expected'])} ({status})")
        if eres["missed"] and args.verbose:
            for m in eres["missed"]:
                print(f"      MISSED: {m}")

    # -------------------------------------------------------------------
    # Overall summary
    # -------------------------------------------------------------------
    all_entity_recall = []
    for recalls in recall_by_type.values():
        all_entity_recall.extend(recalls)
    overall_recall = (
        sum(all_entity_recall) / len(all_entity_recall) if all_entity_recall else 0.0
    )

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Entity detection : {total_passed}/{total_tests} passed")
    print(f"  Overall recall   : {overall_recall:.1%}")
    print(f"  Determinism      : {'PASS' if det_result['deterministic'] else 'FAIL'}")
    print(f"  Roundtrip        : {roundtrip_passed}/{roundtrip_total} passed")
    print(f"  RAG recall       : {rag_result['recall']:.1%}")

    all_ok = (
        total_passed == total_tests
        and det_result["deterministic"]
        and roundtrip_passed == roundtrip_total
    )
    print(f"  Overall          : {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    print("=" * 60)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
