#!/usr/bin/env python3
"""End-to-end demo runner for privacy-preserving semantic routing.

Usage:
    python run_demo.py --scenario 1          # Run a single scenario
    python run_demo.py --all                 # Run all 6 scenarios
    python run_demo.py --all --json          # Output JSON results to stdout
    python run_demo.py --scenario 4 --base-url http://router:8080

Environment variables:
    REDACTION_SERVICE_URL   (default: http://localhost:8000)
    GUARDRAILS_SERVICE_URL  (default: http://localhost:8001)
    LOCAL_MODEL_URL         (default: http://localhost:11434)
    SAAS_MODEL_URL          (default: http://localhost:8080)
    GEMINI_API_KEY          (required for SaaS-routed scenarios)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from typing import Any

# ---------------------------------------------------------------------------
# Path setup so cross-directory imports work
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SRC_DIR = os.path.join(_PROJECT_ROOT, "src")

if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Classifier lives in src/sensitivity-classifier which is not a valid Python
# package name (contains a hyphen), so we manipulate sys.path directly.
_CLASSIFIER_DIR = os.path.join(_SRC_DIR, "sensitivity-classifier")
if _CLASSIFIER_DIR not in sys.path:
    sys.path.insert(0, _CLASSIFIER_DIR)

from classifier import SensitivityClassifier  # noqa: E402

from demo.scenarios import (  # noqa: E402
    ALL_SCENARIOS,
    DemoScenario,
    get_scenario,
)

# ---------------------------------------------------------------------------
# Service URLs (configurable via env)
# ---------------------------------------------------------------------------
REDACTION_SERVICE_URL = os.environ.get("REDACTION_SERVICE_URL", "http://localhost:8000")
GUARDRAILS_SERVICE_URL = os.environ.get("GUARDRAILS_SERVICE_URL", "http://localhost:8001")
EGRESS_GUARD_URL = os.environ.get("EGRESS_GUARD_URL", "http://localhost:8003")
LOCAL_MODEL_URL = os.environ.get("LOCAL_MODEL_URL", "http://localhost:11434")
SAAS_MODEL_URL = os.environ.get("SAAS_MODEL_URL", "http://localhost:8080")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ---------------------------------------------------------------------------
# HTTP helpers (using requests if available, else urllib)
# ---------------------------------------------------------------------------

try:
    import requests as _requests

    def _post(url: str, payload: dict, timeout: int = 30) -> dict:
        resp = _requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _get(url: str, timeout: int = 10) -> dict:
        resp = _requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

except ImportError:
    import urllib.request
    import urllib.error

    def _post(url: str, payload: dict, timeout: int = 30) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(url: str, timeout: int = 10) -> dict:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Core demo logic
# ---------------------------------------------------------------------------

_classifier: SensitivityClassifier | None = None


def _get_classifier() -> SensitivityClassifier:
    global _classifier
    if _classifier is None:
        anchors_path = os.path.join(
            _PROJECT_ROOT, "data", "sensitivity-anchors", "anchors.jsonl"
        )
        config_path = os.path.join(_SRC_DIR, "sensitivity-classifier", "config.yaml")
        _classifier = SensitivityClassifier(
            anchors_path=anchors_path, config_path=config_path
        )
    return _classifier


def _run_scenario_1_to_5(scenario: DemoScenario) -> dict[str, Any]:
    """Run scenarios 1-5: classify, route, redact/restore as needed."""
    result: dict[str, Any] = {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "expected_routing_action": scenario.expected_routing_action,
        "expected_sensitivity": scenario.expected_sensitivity,
        "actual_routing_action": None,
        "actual_sensitivity": None,
        "redaction_summary": None,
        "response_preview": None,
        "passed": False,
        "errors": [],
    }

    user_text = scenario.input_messages[0]["content"]

    # Step 1: Classify sensitivity
    clf = _get_classifier()
    classification = clf.classify(user_text)
    result["actual_sensitivity"] = classification.level
    result["sensitivity_confidence"] = classification.confidence
    result["sensitivity_signals"] = classification.fast_path_signals
    result["sensitivity_source"] = classification.source

    # Determine routing action using the routing matrix
    complexity = scenario.expected_complexity  # Use expected complexity for demo
    routing_action = clf.get_routing_action(complexity, classification.level)
    result["actual_routing_action"] = routing_action

    print(f"  Sensitivity : {classification.level} (confidence={classification.confidence:.2f}, source={classification.source})")
    print(f"  Signals     : {classification.fast_path_signals}")
    print(f"  Complexity  : {complexity}")
    print(f"  Routing     : {routing_action}")

    # Step 2: Execute routing action
    if routing_action == "REDACT_THEN_SAAS":
        result = _execute_redact_then_saas(scenario, user_text, result)
    elif routing_action == "LOCAL_ONLY":
        result = _execute_local_only(scenario, user_text, result)
    else:
        result["errors"].append(f"Unknown routing action: {routing_action}")

    # Step 3: Validate
    result["passed"] = (
        result["actual_routing_action"] == scenario.expected_routing_action
        and len(result["errors"]) == 0
    )

    return result


def _execute_redact_then_saas(
    scenario: DemoScenario, user_text: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Redact -> call SaaS -> restore pipeline."""
    # Call redaction service /redact
    try:
        redact_resp = _post(
            f"{REDACTION_SERVICE_URL}/redact",
            {"text": user_text, "sensitivity_level": "INTERNAL"},
        )
        result["redaction_summary"] = {
            "entity_count": redact_resp.get("entity_count", 0),
            "entities": [
                {"type": e["type"], "placeholder": e["placeholder"]}
                for e in redact_resp.get("entities", [])
            ],
            "redacted_text_preview": redact_resp.get("redacted_text", "")[:200],
        }
        mapping_id = redact_resp.get("mapping_id", "")
        redacted_text = redact_resp.get("redacted_text", user_text)
        print(f"  Redacted    : {redact_resp.get('entity_count', 0)} entities")
        print(f"  Preview     : {redacted_text[:120]}...")
    except Exception as e:
        result["errors"].append(f"Redaction service error: {e}")
        return result

    # Call NeMo Egress Guard -- final checkpoint before SaaS
    try:
        egress_resp = _post(
            f"{EGRESS_GUARD_URL}/guard/egress",
            {
                "redacted_text": redacted_text,
                "sensitivity_level": result.get("actual_sensitivity", "INTERNAL"),
                "entity_types_redacted": [
                    e["type"] for e in redact_resp.get("entities", [])
                ],
                "mapping_id": mapping_id,
            },
        )
        result["egress_guard_result"] = egress_resp
        print(f"  Egress Guard: approved={egress_resp.get('approved')} ({egress_resp.get('reason', '')})")
        if not egress_resp.get("approved", False):
            result["errors"].append(
                f"Egress guard blocked: {egress_resp.get('reason', 'unknown')}"
            )
            return result
    except Exception as e:
        result["errors"].append(f"Egress guard unreachable (fail-safe block): {e}")
        return result

    # Call SaaS model with redacted text
    try:
        llm_resp = _post(
            f"{SAAS_MODEL_URL}/v1/chat/completions",
            {
                "model": scenario.expected_model,
                "messages": [{"role": "user", "content": redacted_text}],
            },
            timeout=60,
        )
        saas_response_text = (
            llm_resp.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        print(f"  SaaS resp   : {saas_response_text[:120]}...")
    except Exception as e:
        result["errors"].append(f"SaaS model error: {e}")
        saas_response_text = f"[SaaS unavailable: {e}]"

    # Call redaction service /restore
    if mapping_id:
        try:
            restore_resp = _post(
                f"{REDACTION_SERVICE_URL}/restore",
                {"text": saas_response_text, "mapping_id": mapping_id},
            )
            restored_text = restore_resp.get("restored_text", saas_response_text)
            result["response_preview"] = restored_text[:300]
            print(f"  Restored    : {restore_resp.get('placeholders_restored', 0)} placeholders")
        except Exception as e:
            result["errors"].append(f"Restore error: {e}")
            result["response_preview"] = saas_response_text[:300]
    else:
        result["response_preview"] = saas_response_text[:300]

    return result


def _execute_local_only(
    scenario: DemoScenario, user_text: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Send directly to local model, no redaction."""
    result["redaction_summary"] = {"entity_count": 0, "entities": []}

    try:
        llm_resp = _post(
            f"{LOCAL_MODEL_URL}/v1/chat/completions",
            {
                "model": scenario.expected_model,
                "messages": [{"role": "user", "content": user_text}],
            },
            timeout=120,
        )
        response_text = (
            llm_resp.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        result["response_preview"] = response_text[:300]
        print(f"  Response    : {response_text[:120]}...")
    except Exception as e:
        result["errors"].append(f"Local model error: {e}")
        result["response_preview"] = f"[Local model unavailable: {e}]"

    return result


# ---------------------------------------------------------------------------
# Scenario 6: NetworkPolicy bypass attempt (oc exec)
# ---------------------------------------------------------------------------

_BYPASS_STEPS = [
    {
        "name": "guardrails_pod_cannot_reach_saas",
        "description": "Guardrails pod should NOT reach Google AI API",
        "command": [
            "oc", "exec", "-it", "deploy/guardrails-service",
            "-n", os.environ.get("NAMESPACE", "user-cnuland"), "--",
            "curl", "-v", "--connect-timeout", "5",
            "https://generativelanguage.googleapis.com/v1/models",
        ],
        "expect_failure": True,
    },
    {
        "name": "qdrant_pod_cannot_reach_saas",
        "description": "Qdrant pod should NOT reach OpenAI API",
        "command": [
            "oc", "exec", "-it", "deploy/qdrant",
            "-n", os.environ.get("NAMESPACE", "user-cnuland"), "--",
            "curl", "-v", "--connect-timeout", "5",
            "https://api.openai.com/v1/models",
        ],
        "expect_failure": True,
    },
    {
        "name": "egress_guard_pod_cannot_reach_saas",
        "description": "NeMo Egress Guard pod should NOT reach Google AI API",
        "command": [
            "oc", "exec", "-it", "deploy/nemo-egress-guard",
            "-n", os.environ.get("NAMESPACE", "user-cnuland"), "--",
            "curl", "-v", "--connect-timeout", "5",
            "https://generativelanguage.googleapis.com/v1/models",
        ],
        "expect_failure": True,
    },
    {
        "name": "classifier_pod_cannot_reach_saas",
        "description": "Sensitivity Classifier pod should NOT reach Google AI API",
        "command": [
            "oc", "exec", "-it", "deploy/sensitivity-classifier",
            "-n", os.environ.get("NAMESPACE", "user-cnuland"), "--",
            "curl", "-v", "--connect-timeout", "5",
            "https://generativelanguage.googleapis.com/v1/models",
        ],
        "expect_failure": True,
    },
    {
        "name": "redaction_service_can_reach_saas",
        "description": "Redaction service pod (egress gateway) SHOULD reach Google AI API",
        "command": [
            "oc", "exec", "-it", "deploy/redaction-service",
            "-n", os.environ.get("NAMESPACE", "user-cnuland"), "--",
            "curl", "-v", "--connect-timeout", "10",
            "https://generativelanguage.googleapis.com/v1/models",
        ],
        "expect_failure": False,
    },
    {
        "name": "internal_traffic_allowed",
        "description": "Blocked pods CAN still reach internal services",
        "command": [
            "oc", "exec", "-it", "deploy/guardrails-service",
            "-n", os.environ.get("NAMESPACE", "user-cnuland"), "--",
            "curl", "-s",
            f"http://redaction-service.{os.environ.get('NAMESPACE', 'user-cnuland')}.svc:8000/health",
        ],
        "expect_failure": False,
    },
]


def _run_scenario_6() -> dict[str, Any]:
    """Run NetworkPolicy bypass-attempt scenario using oc exec."""
    result: dict[str, Any] = {
        "scenario_id": 6,
        "scenario_name": "Bypass Attempt -- NetworkPolicy Enforcement",
        "expected_routing_action": "PLATFORM_ENFORCED",
        "actual_routing_action": "PLATFORM_ENFORCED",
        "actual_sensitivity": "N/A",
        "steps": [],
        "passed": True,
        "errors": [],
    }

    for step in _BYPASS_STEPS:
        step_result = {
            "name": step["name"],
            "description": step["description"],
            "expect_failure": step["expect_failure"],
            "actual_failed": None,
            "passed": False,
            "output": "",
        }

        print(f"  Step: {step['description']}")

        try:
            proc = subprocess.run(
                step["command"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # If expect_failure=True, we want a non-zero exit code or timeout
            timed_out = False
            output = proc.stdout + proc.stderr
            step_result["output"] = output[:500]

            if step["expect_failure"]:
                # Connection should have failed (non-zero exit, timeout text, etc.)
                actual_failed = proc.returncode != 0 or "timed out" in output.lower()
                step_result["actual_failed"] = actual_failed
                step_result["passed"] = actual_failed
                status = "BLOCKED (expected)" if actual_failed else "CONNECTED (unexpected!)"
            else:
                # Connection should have succeeded
                actual_failed = proc.returncode != 0
                step_result["actual_failed"] = actual_failed
                step_result["passed"] = not actual_failed
                status = "CONNECTED (expected)" if not actual_failed else "BLOCKED (unexpected!)"

            print(f"    Result: {status}")

        except subprocess.TimeoutExpired:
            step_result["output"] = "Command timed out (15s)"
            if step["expect_failure"]:
                step_result["actual_failed"] = True
                step_result["passed"] = True
                print("    Result: TIMED OUT (expected -- network policy blocked)")
            else:
                step_result["actual_failed"] = True
                step_result["passed"] = False
                print("    Result: TIMED OUT (unexpected!)")

        except FileNotFoundError:
            step_result["output"] = "oc command not found"
            step_result["actual_failed"] = True
            step_result["passed"] = False
            result["errors"].append(
                f"oc CLI not found -- cannot run network policy tests. "
                f"Install the OpenShift CLI and log in to the cluster."
            )
            print("    Result: SKIPPED (oc not found)")

        if not step_result["passed"]:
            result["passed"] = False

        result["steps"].append(step_result)

    return result


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def _print_summary(results: list[dict[str, Any]]) -> None:
    """Print a formatted summary table."""
    print("\n" + "=" * 72)
    print(f"{'Scenario':<45} {'Expected':<18} {'Actual':<18} {'Result'}")
    print("-" * 72)

    all_passed = True
    for r in results:
        sid = r["scenario_id"]
        name = r.get("scenario_name", "")[:38]
        expected = r.get("expected_routing_action", "?")
        actual = r.get("actual_routing_action", "?")
        passed = r.get("passed", False)
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  {sid}. {name:<42} {expected:<18} {actual:<18} {status}")

    print("-" * 72)
    overall = "ALL PASSED" if all_passed else "SOME FAILED"
    print(f"  Overall: {overall}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run privacy-preserving semantic routing demo scenarios"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario", type=int, metavar="N",
        help="Run a single scenario by ID (1-6)",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Run all 6 scenarios in order",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8080",
        help="Base URL of the semantic router (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output JSON results to stdout",
    )
    args = parser.parse_args()

    # Override SaaS URL from --base-url if provided
    global SAAS_MODEL_URL
    if args.base_url != "http://localhost:8080":
        SAAS_MODEL_URL = args.base_url

    scenarios_to_run: list[DemoScenario]
    if args.all:
        scenarios_to_run = ALL_SCENARIOS
    else:
        scenarios_to_run = [get_scenario(args.scenario)]

    results: list[dict[str, Any]] = []

    for scenario in scenarios_to_run:
        print(f"\n{'='*60}")
        print(f"Scenario {scenario.id}: {scenario.name}")
        print(f"{'='*60}")
        print(f"  {scenario.description}")
        print()

        start = time.monotonic()

        if scenario.id == 6:
            result = _run_scenario_6()
        else:
            result = _run_scenario_1_to_5(scenario)

        elapsed = time.monotonic() - start
        result["elapsed_seconds"] = round(elapsed, 2)
        print(f"\n  Elapsed     : {elapsed:.2f}s")
        print(f"  Result      : {'PASS' if result['passed'] else 'FAIL'}")

        if result.get("errors"):
            for err in result["errors"]:
                print(f"  Error       : {err}")

        results.append(result)

    # Print summary table
    _print_summary(results)

    # JSON output
    if args.json_output:
        print("\n--- JSON Results ---")
        print(json.dumps(results, indent=2, default=str))

    # Exit code: 0 if all passed, 1 otherwise
    sys.exit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
