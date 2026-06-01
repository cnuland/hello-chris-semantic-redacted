# Privacy-Preserving Semantic Routing on OpenShift

## Core Thesis

Semantic routing is safe only when the first trust decision happens inside the isolated environment. Red Hat OpenShift becomes the enforcement boundary: local inference, local classification, local guardrails, local memory, policy-controlled egress, auditable telemetry, and only sanitized traffic crossing to SaaS.

## The Problem

The Semantic Claw Router classifies LLM requests by **complexity** and routes them:

| Tier | Model | Cost |
|------|-------|------|
| SIMPLE | Local Qwen 3.6 | $0 |
| MEDIUM | Local Qwen 3.6 | $0 |
| COMPLEX | Gemini Pro | $1.25/$10 per 1M tokens |
| REASONING | Gemini Pro | $1.25/$10 per 1M tokens |

This works for cost optimization. But complexity is only one dimension. A "SIMPLE" question like *"What is Sarah Chen's salary?"* gets routed to Gemini because it's structurally simple — and the trust boundary is already broken.

The router currently handles:
- 15-dimension fast-path complexity scoring (27us latency)
- Semantic embedding fallback for ambiguous cases
- Session pinning, deduplication, compression, graceful degradation
- 98.53% accuracy on complexity classification (after RLAIF fine-tuning)

What it does NOT handle:
- Sensitivity classification (PII, secrets, financial data, HR content)
- Content redaction before SaaS routing
- RAG document governance (preventing sensitive retrieved context from leaving)
- Egress enforcement at the platform level

## The Solution: Multi-Dimensional Routing

Add **sensitivity** as a second routing dimension, creating a 2D decision matrix:

```
                    PUBLIC       INTERNAL    CONFIDENTIAL    REGULATED    NEVER_EGRESS
SIMPLE              Redact→SaaS  Local       Local           Local        Local
MEDIUM              Redact→SaaS  Redact→SaaS Local           Local        Local
COMPLEX             Redact→SaaS  Redact→SaaS Redact→SaaS     Local        Local
REASONING           Redact→SaaS  Redact→SaaS Local           Local        Local
```

**Two routing outcomes:**
1. **Redact → SaaS** — All SaaS-bound content goes through the redaction pipeline. Pseudonymize PII, strip secrets, then route to SaaS. Restore placeholders in the response. Even public content runs through redaction — the overhead is negligible and eliminates the risk of misclassification leaking data.
2. **Local only** — Content is too sensitive to leave the cluster, even redacted. Route to Qwen.

## What Red Hat Brings

This is not "trust the router." This is "trust the platform."

**OpenShift as the enforcement boundary:**
- All classification, redaction, and guardrail services run inside the cluster
- NetworkPolicy default-deny prevents any pod from directly calling SaaS endpoints
- Only the approved sanitizer/gateway service can make external calls
- Red Hat Advanced Cluster Security can alert on unexpected egress
- Compliance Operator verifies baseline controls exist

**The composable open-source stack:**

| Layer | Component | Role |
|-------|-----------|------|
| Platform | Red Hat OpenShift | Runs everything, enforces egress |
| Local inference | Qwen 3.6 on OpenShift | Private lane for sensitive traffic |
| Routing | Semantic Claw Router | Classifies complexity + sensitivity |
| Guardrails | Regex Rails (custom) | Fast input/retrieval/output pre-screening |
| Egress Guard | NeMo Guardrails (real) | LLM-backed final egress checkpoint |
| Redaction | Presidio + GLiNER | Detect and pseudonymize sensitive spans |
| Policy | NetworkPolicy | Only sanitized traffic crosses boundary |
| Observability | Structured JSON logs | Every decision is auditable |

## Architecture

```
                                    OpenShift Cluster
    ┌──────────────────────────────────────────────────────────────────────┐
    │                                                                      │
    │   Client Request                                                     │
    │        │                                                             │
    │        ▼                                                             │
    │   ┌─────────────────────┐                                           │
    │   │  Semantic Claw      │                                           │
    │   │  Router             │ ◄── Complexity classification (existing)  │
    │   │  + Sensitivity      │ ◄── Sensitivity classification (NEW)     │
    │   │    Signals          │                                           │
    │   └────────┬────────────┘                                           │
    │            │                                                         │
    │     ┌──────┴──────┐                                                 │
    │     │  Decision   │                                                 │
    │     │  Engine     │ ◄── 2D matrix: complexity × sensitivity        │
    │     └──┬──────┬───┘                                                 │
    │        │      │                                                      │
    │   LOCAL│      │SaaS-ELIGIBLE                                        │
    │        │      │                                                      │
    │        ▼      ▼                                                      │
    │   ┌────────┐ ┌───────────────┐                                      │
    │   │ Qwen   │ │ NeMo          │                                      │
    │   │ 3.6    │ │ Guardrails    │ ◄── Input rail: check sensitivity   │
    │   │ Local  │ │ (Input Rail)  │                                      │
    │   └────────┘ └───────┬───────┘                                      │
    │                      │                                               │
    │                      ▼                                               │
    │              ┌───────────────┐                                       │
    │              │ Presidio      │                                       │
    │              │ Redaction     │ ◄── Pseudonymize PII + secrets       │
    │              │ Service       │     Store mapping locally             │
    │              └───────┬───────┘                                       │
    │                      │                                               │
    │                      ▼                                               │
    │              ┌───────────────┐                                       │
    │              │ NeMo Egress   │ ◄── Final checkpoint: verify         │
    │              │ Guard         │     redaction before SaaS call       │
    │              └───────┬───────┘                                       │
    │                      │                                               │
    │              ┌───────────────┐     ┌─────────────────┐              │
    │              │ Egress        │────▶│ Gemini (SaaS)   │              │
    │              │ Gateway       │     │ (sanitized only) │              │
    │              └───────┬───────┘     └────────┬────────┘              │
    │                      │                      │                        │
    │                      ▼                      │                        │
    │              ┌───────────────┐              │                        │
    │              │ NeMo          │◄─────────────┘                       │
    │              │ Guardrails    │ ◄── Output rail: scan response       │
    │              │ (Output Rail) │                                       │
    │              └───────┬───────┘                                       │
    │                      │                                               │
    │                      ▼                                               │
    │              ┌───────────────┐                                       │
    │              │ Presidio      │                                       │
    │              │ Restore       │ ◄── Replace placeholders with        │
    │              │ Service       │     original values (inside cluster)  │
    │              └───────┬───────┘                                       │
    │                      │                                               │
    │                      ▼                                               │
    │                  Response                                            │
    │                                                                      │
    │   ┌──────────────────────────────────────────┐                      │
    │   │           NetworkPolicy                   │                      │
    │   │  - Default deny egress                    │                      │
    │   │  - Only egress-gateway pod can call SaaS  │                      │
    │   │  - All other pods: internal only          │                      │
    │   └──────────────────────────────────────────┘                      │
    └──────────────────────────────────────────────────────────────────────┘
```

## Demo Narrative

The demo tells this story in 6 acts:

1. **Baseline:** A public question flows through the router to Gemini. Nothing changes. This is the happy path.

2. **The leak:** A query about a confidential RAG document gets classified as SIMPLE and would normally go to Gemini. We show the sensitivity classifier catching it and routing to local Qwen instead.

3. **HR sensitivity:** A conversation about employee performance reviews is classified as CONFIDENTIAL regardless of complexity. It stays local.

4. **Redact and route:** A query that mentions customer names and internal project codes gets redacted (Customer A → `<CUSTOMER_1>`, Project Phoenix → `<PROJECT_1>`), sent to Gemini in sanitized form, and the response gets placeholders restored inside the cluster.

5. **Financial data:** Quarterly earnings data from the RAG document is classified REGULATED. Even though Gemini could answer better, it stays local.

6. **The enforcement boundary:** We exec into a pod and try to curl Gemini directly. NetworkPolicy blocks it. Only the sanitizer pod can call external endpoints.

## How This Extends Existing Work

| Project | Contribution to This Demo |
|---------|---------------------------|
| `semantic-claw-router` | Base routing infrastructure, 15-dimension classifier, provider integrations |
| `hello-chris-semantic-rlaif` | Fine-tuned embedding model (98.53% accuracy), SDG pipeline pattern, ASDLC agent structure |
| `hello-chris-the-last-mile` | Multi-agent pipeline pattern (programmer/designer/editor/reviewer), handoff protocol, scoring rubric |
| `hello-chris-ai-homelab` | OpenShift cluster, Qwen model serving, Gemini API keys, network infrastructure |
| Red Hat blog (Athena) | vLLM Semantic Router architecture, signal-decision pattern, ExtProc design, classifier stack |

## Red Hat Positioning

The strongest Red Hat-aligned message:

> OpenShift turns semantic routing from an application convenience into a governed AI control plane.

Claims this demo supports:
- **Data sovereignty:** Sensitive prompts, memory, and tool outputs stay inside the OpenShift boundary
- **Local-first AI:** OpenShift AI provides the local lane for sensitive workloads
- **Policy-driven egress:** External models are reachable only through approved, sanitized paths
- **Composable open source:** vLLM Semantic Router, NeMo Guardrails, Presidio, GLiNER, and OpenShift-native controls are all composable
- **Auditability:** Every routing decision produces evidence: selected model, risk label, redaction count, egress approval
