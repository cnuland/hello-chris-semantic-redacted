"""Demo scenario definitions for end-to-end privacy-preserving routing.

Each scenario maps to one of the six demo narratives described in
docs/demo-scenarios.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DemoScenario:
    """A single end-to-end demo scenario."""

    id: int
    name: str
    description: str
    input_messages: list[dict[str, str]]
    expected_complexity: str
    expected_sensitivity: str
    expected_routing_action: str
    expected_model: str
    validation_checks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scenario 1: Public Query -- Direct SaaS Routing
# ---------------------------------------------------------------------------
SCENARIO_1 = DemoScenario(
    id=1,
    name="Public Query -- Direct SaaS Routing",
    description=(
        "Establish that public content flows to SaaS models without "
        "interference. This is the baseline -- nothing changes for "
        "non-sensitive traffic."
    ),
    input_messages=[
        {
            "role": "user",
            "content": (
                "Explain the CAP theorem in distributed systems. What are the "
                "tradeoffs between consistency, availability, and partition "
                "tolerance?"
            ),
        }
    ],
    expected_complexity="MEDIUM",
    expected_sensitivity="PUBLIC",
    expected_routing_action="DIRECT_SAAS",
    expected_model="gemini-3.1-flash-preview",
    validation_checks=[
        "sensitivity_level_is_PUBLIC",
        "routing_action_is_DIRECT_SAAS",
        "redaction_count_is_0",
        "response_contains_content",
    ],
)

# ---------------------------------------------------------------------------
# Scenario 2: Confidential RAG Query -- Local Only
# ---------------------------------------------------------------------------
SCENARIO_2 = DemoScenario(
    id=2,
    name="Confidential RAG Query -- Local Only",
    description=(
        "Demonstrate that when a query retrieves sensitive RAG content, the "
        "entire request stays local regardless of the query's own sensitivity "
        "level. RAG context inheritance forces LOCAL_ONLY."
    ),
    input_messages=[
        {
            "role": "user",
            "content": (
                "What were the key highlights from last quarter's financial "
                "review?"
            ),
        }
    ],
    expected_complexity="SIMPLE",
    expected_sensitivity="NEVER_EGRESS",
    expected_routing_action="LOCAL_ONLY",
    expected_model="qwen3.6-35b-a3b",
    validation_checks=[
        "sensitivity_level_is_NEVER_EGRESS",
        "routing_action_is_LOCAL_ONLY",
        "rag_context_inheritance_triggered",
        "response_contains_content",
    ],
)

# ---------------------------------------------------------------------------
# Scenario 3: HR-Sensitive Conversation -- Confidential, Local Only
# ---------------------------------------------------------------------------
SCENARIO_3 = DemoScenario(
    id=3,
    name="HR-Sensitive Conversation -- Confidential, Local Only",
    description=(
        "Show that HR-related conversations are classified as CONFIDENTIAL "
        "regardless of structural complexity, and always route to the local "
        "model. Contains employee name, performance data, and salary."
    ),
    input_messages=[
        {
            "role": "user",
            "content": (
                "Draft a performance improvement plan for Marcus Johnson. "
                "He's been consistently missing sprint commitments for the "
                "last two quarters and his peer feedback scores dropped from "
                "4.2 to 2.8. His current salary is $165,000 and he's been "
                "with the company for 3 years."
            ),
        }
    ],
    expected_complexity="COMPLEX",
    expected_sensitivity="CONFIDENTIAL",
    expected_routing_action="LOCAL_ONLY",
    expected_model="qwen3.6-35b-a3b",
    validation_checks=[
        "sensitivity_level_is_CONFIDENTIAL",
        "routing_action_is_LOCAL_ONLY",
        "sensitivity_signals_include_PERSON",
        "sensitivity_signals_include_salary",
        "response_contains_content",
    ],
)

# ---------------------------------------------------------------------------
# Scenario 4: Sanitizable Query -- Redact, Route to SaaS, Restore
# ---------------------------------------------------------------------------
SCENARIO_4 = DemoScenario(
    id=4,
    name="Sanitizable Query -- Redact, Route to SaaS, Restore",
    description=(
        "Full redact -> route -> scan -> restore pipeline. Content has "
        "identifiable entities (project name, cluster, person, namespace, "
        "email) that are pseudonymized before sending to SaaS."
    ),
    input_messages=[
        {
            "role": "user",
            "content": (
                "Analyze the architecture of Project Phoenix deployed on the "
                "ironman.cjlabs.dev cluster. The lead engineer Sarah Chen "
                "designed a microservices system in the homelab-maas namespace "
                "with 5 services. Review the design for scalability issues and "
                "suggest improvements. Her contact is sarah.chen@company.com."
            ),
        }
    ],
    expected_complexity="COMPLEX",
    expected_sensitivity="INTERNAL",
    expected_routing_action="REDACT_THEN_SAAS",
    expected_model="gemini-3.1-pro-preview",
    validation_checks=[
        "sensitivity_level_is_INTERNAL",
        "routing_action_is_REDACT_THEN_SAAS",
        "redaction_count_gte_5",
        "redacted_text_contains_no_originals",
        "restored_text_matches_originals",
        "response_contains_content",
    ],
)

# ---------------------------------------------------------------------------
# Scenario 5: Financial Data -- Regulated, Local Only
# ---------------------------------------------------------------------------
SCENARIO_5 = DemoScenario(
    id=5,
    name="Financial Data -- Regulated, Local Only",
    description=(
        "Regulated financial data always stays local, even when the question "
        "is complex enough that a frontier model would produce a better "
        "answer. Compliance obligation outweighs quality benefit."
    ),
    input_messages=[
        {
            "role": "user",
            "content": (
                "Analyze our Q3 2026 earnings data: revenue was $4.2M with "
                "12% quarter-over-quarter growth, net income $890K, and EBITDA "
                "margin at 21.2%. Compare this against industry benchmarks and "
                "prepare a summary for the SEC 10-K filing. Include projections "
                "for Q4 based on current pipeline."
            ),
        }
    ],
    expected_complexity="REASONING",
    expected_sensitivity="REGULATED",
    expected_routing_action="LOCAL_ONLY",
    expected_model="qwen3.6-35b-a3b",
    validation_checks=[
        "sensitivity_level_is_REGULATED",
        "routing_action_is_LOCAL_ONLY",
        "sensitivity_signals_include_financial",
        "response_contains_content",
    ],
)

# ---------------------------------------------------------------------------
# Scenario 6: Bypass Attempt -- NetworkPolicy Enforcement
# ---------------------------------------------------------------------------
SCENARIO_6 = DemoScenario(
    id=6,
    name="Bypass Attempt -- NetworkPolicy Enforcement",
    description=(
        "Prove that even if application controls fail, the platform prevents "
        "unauthorized egress. NetworkPolicy blocks direct SaaS calls from "
        "non-gateway pods while allowing internal traffic."
    ),
    input_messages=[],  # No LLM messages; this scenario uses oc exec commands
    expected_complexity="N/A",
    expected_sensitivity="N/A",
    expected_routing_action="PLATFORM_ENFORCED",
    expected_model="N/A",
    validation_checks=[
        "guardrails_pod_cannot_reach_saas",
        "qdrant_pod_cannot_reach_saas",
        "redaction_service_can_reach_saas",
        "internal_traffic_allowed",
    ],
)

# ---------------------------------------------------------------------------
# All scenarios in order
# ---------------------------------------------------------------------------
ALL_SCENARIOS: list[DemoScenario] = [
    SCENARIO_1,
    SCENARIO_2,
    SCENARIO_3,
    SCENARIO_4,
    SCENARIO_5,
    SCENARIO_6,
]


def get_scenario(scenario_id: int) -> DemoScenario:
    """Retrieve a scenario by its 1-based ID."""
    for s in ALL_SCENARIOS:
        if s.id == scenario_id:
            return s
    raise ValueError(f"Unknown scenario ID: {scenario_id}. Valid IDs: 1-{len(ALL_SCENARIOS)}")
