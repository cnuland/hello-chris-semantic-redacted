# System Architecture: Privacy-Preserving Semantic Routing

## Design Principles

1. **Local-first trust:** The first classification of any request happens inside the cluster. No external service participates in the sensitivity decision.
2. **Defense in depth:** Application-level guards (classifier, redaction, guardrails) are backed by platform-level enforcement (NetworkPolicy, RBAC).
3. **Composable open source:** Each component is independently replaceable. Presidio can swap for a different redactor. NeMo can swap for Guardrails AI. The architecture doesn't depend on any single vendor.
4. **Zero trust for SaaS:** External model endpoints are treated as untrusted parties. They receive only what policy explicitly permits.
5. **Auditability:** Every routing decision produces structured evidence. Compliance can reconstruct what happened to any request.

## Component Architecture

### Namespace Layout

```
homelab-maas (existing, read-only)          semantic-redacted (new)
┌─────────────────────────────────┐         ┌─────────────────────────────┐
│ ollama-qwen36 (GPU, model)      │         │ redaction-service           │
│ llama-server-qwen36 (alt model) │         │ guardrails-service          │
│ semantic-claw-router            │         │ nemo-egress-guard           │
│ gemini-api-key (secret)         │         │ qdrant (vector store)       │
└─────────────────────────────────┘         │ sensitivity-classifier      │
                                            │ demo-runner (job)           │
                                            └─────────────────────────────┘
home-assistant (existing, read-only)
┌─────────────────────────────────┐
│ openai-proxy                    │
│ home-assistant                  │
│ claude-code-agent               │
└─────────────────────────────────┘
```

### Component Inventory

| Component | Type | Namespace | Port | Image Base | Purpose |
|-----------|------|-----------|------|------------|---------|
| Sensitivity Classifier | Sidecar/Library | homelab-maas | — | — | Adds sensitivity signals to router (config-based) |
| Redaction Service | Deployment | semantic-redacted | 8000 | UBI9 Python 3.11 | Presidio + GLiNER: detect, pseudonymize, restore |
| Guardrails Service | Deployment | semantic-redacted | 8001 | UBI9 Python 3.11 | Regex guardrails: input/retrieval/output rails |
| NeMo Egress Guard | Deployment | semantic-redacted | 8003 | UBI9 Python 3.11 | NeMo Guardrails: final egress verification of redacted content |
| Qdrant | Deployment | semantic-redacted | 6333 | qdrant/qdrant | Local vector store for sensitive RAG docs |
| Demo Runner | Job | semantic-redacted | — | UBI9 Python 3.11 | Executes 6 demo scenarios |

### Data Flow: Complete Request Lifecycle

```
Step 1: Request Ingestion
─────────────────────────
Client sends POST /v1/chat/completions to Semantic Claw Router
  Headers: Authorization, Content-Type
  Body: { model: "auto", messages: [...] }

Step 2: Complexity Classification (existing)
────────────────────────────────────────────
Router's 15-dimension fast-path classifier scores complexity:
  - Token count, code presence, reasoning markers, technical terms...
  - Result: SIMPLE | MEDIUM | COMPLEX | REASONING
  - Confidence: 0.0 - 1.0
  - If confidence < 0.7: fall back to semantic embedding classifier

Step 3: Sensitivity Classification (NEW)
────────────────────────────────────────
Router calls sensitivity classifier (internal or sidecar):
  - Embedding-based similarity against sensitivity anchors
  - Checks for: PII patterns, financial keywords, HR keywords,
    customer data patterns, internal project references
  - Result: PUBLIC | INTERNAL | CONFIDENTIAL | REGULATED | NEVER_EGRESS
  - Confidence: 0.0 - 1.0

Step 4: RAG Context Check (NEW)
───────────────────────────────
If request triggers RAG retrieval:
  - Query Qdrant for relevant document chunks
  - Each chunk has a sensitivity label (metadata field)
  - If any retrieved chunk is CONFIDENTIAL or higher:
    the entire request inherits that sensitivity level
  - This prevents sensitive RAG content from being attached
    to a prompt that leaves the cluster

Step 5: 2D Routing Decision (ENHANCED)
──────────────────────────────────────
Decision engine consults the 2D matrix:

  complexity × sensitivity → routing_action

  routing_action is one of:
  - DIRECT_SAAS: Send to Gemini as-is
  - REDACT_THEN_SAAS: Redact with Presidio, then send to Gemini
  - LOCAL_ONLY: Send to local Qwen, never leaves cluster

Step 6a: LOCAL_ONLY Path
────────────────────────
Request → Qwen 3.6 (ollama-qwen36.homelab-maas.svc:11434)
  - No redaction needed
  - No egress
  - Full fidelity (all PII, context, RAG chunks included)
  - Response returned directly to client

Step 6b: DIRECT_SAAS Path
─────────────────────────
Request → Gemini (generativelanguage.googleapis.com)
  - No sensitive content detected
  - No redaction needed
  - Normal routing through provider

Step 6c: REDACT_THEN_SAAS Path
──────────────────────────────
Request → NeMo Guardrails (Input Rail)
  - Validates that redaction is appropriate for this content
  - May block if content is too sensitive even for redaction

Request → Presidio Redaction Service (/redact)
  - Detect sensitive spans (names, emails, SSNs, project codes, etc.)
  - Replace with deterministic placeholders:
    "Sarah Chen" → "<PERSON_1>"
    "Project Phoenix" → "<PROJECT_1>"
    "192.168.1.100" → "<IP_1>"
  - Store mapping in memory (request-scoped, never persisted)
  - Return: { redacted_text, mapping_id, entity_count }

Redacted Request → NeMo Egress Guard (/guard/egress)
  - Final checkpoint before content leaves the cluster
  - Verifies: no residual PII, no secrets, placeholder integrity
  - Uses real NeMo Guardrails framework with Qwen LLM backend
  - If BLOCKED: short-circuit to LOCAL_ONLY (fail-safe)
  - If APPROVED: proceed to SaaS call

Redacted Request → Gemini
  - SaaS model sees only placeholders
  - Generates response using placeholder entities

Response → NeMo Guardrails (Output Rail)
  - Scan for: reconstructed PII, hallucinated secrets, leaked context
  - Block or flag if sensitive content detected in response

Response → Presidio Restore Service (/restore)
  - Replace placeholders with original values
  - Only happens INSIDE the cluster
  - Mapping is deleted after restoration

Response → Client
  - Client sees response with original entity names

Step 7: Audit Logging
─────────────────────
Every request produces a structured JSON log entry:
{
  "request_id": "abc123",
  "timestamp": "2026-05-24T14:00:00Z",
  "complexity_tier": "MEDIUM",
  "complexity_confidence": 0.85,
  "sensitivity_level": "INTERNAL",
  "sensitivity_confidence": 0.92,
  "routing_action": "REDACT_THEN_SAAS",
  "redaction_count": 3,
  "entity_types_redacted": ["PERSON", "PROJECT", "EMAIL"],
  "target_model": "gemini-3.1-flash-preview",
  "egress_approved": true,
  "response_scan_result": "CLEAN",
  "latency_ms": 245
}
```

## Trust Boundary Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    TRUSTED ZONE (OpenShift)                       │
│                                                                   │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐     │
│  │ Classification │  │ Redaction      │  │ Guardrails     │     │
│  │ (sensitivity)  │  │ (Presidio)     │  │ (regex rails)  │     │
│  └────────────────┘  └────────────────┘  └────────────────┘     │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ NeMo Egress Guard (final checkpoint before SaaS)          │  │
│  │ - Real NeMo Guardrails framework + Qwen LLM backend      │  │
│  │ - Verifies redaction completeness before egress           │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐     │
│  │ Local Model    │  │ Vector Store   │  │ Mapping Store  │     │
│  │ (Qwen 3.6)    │  │ (Qdrant)       │  │ (in-memory)    │     │
│  └────────────────┘  └────────────────┘  └────────────────┘     │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ NetworkPolicy Enforcement                                  │  │
│  │ - Default deny egress for all pods                        │  │
│  │ - Only redaction-service (egress gateway) can call SaaS   │  │
│  │ - All other pods: internal traffic only                   │  │
│  └────────────────────────────────────────────────────────────┘  │
│                           │                                       │
│                    SANITIZED ONLY                                 │
│                           │                                       │
├───────────────────────────┼───────────────────────────────────────┤
│                           ▼                                       │
│                 UNTRUSTED ZONE                                    │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ SaaS Models (Gemini, OpenAI, Claude, etc.)                │  │
│  │ - Receive only: PUBLIC content or redacted/pseudonymized  │  │
│  │ - Never receive: raw PII, secrets, NEVER_EGRESS content   │  │
│  │ - Responses are scanned before entering trusted zone      │  │
│  └────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

## Service Communication Matrix

| From | To | Protocol | Purpose | Allowed? |
|------|----|----------|---------|----------|
| Router | Redaction Service | HTTP/8000 | Redact/restore/scan | Yes (internal) |
| Router | Guardrails Service | HTTP/8001 | Input/output rails | Yes (internal) |
| Router | Qdrant | HTTP/6333 | RAG retrieval | Yes (internal) |
| Router | Qwen 3.6 | HTTP/11434 | Local inference | Yes (cross-namespace) |
| Redaction Service | Gemini API | HTTPS/443 | Sanitized SaaS call | Yes (egress gateway) |
| Guardrails Service | Qwen 3.6 | HTTP/11434 | Rail evaluation | Yes (cross-namespace) |
| Guardrails Service | Gemini API | HTTPS/443 | — | **BLOCKED** |
| NeMo Egress Guard | Qwen 3.6 | HTTP/8080 | Rail evaluation (LLM) | Yes (cross-namespace) |
| NeMo Egress Guard | Redaction Service | HTTP/8000 | /scan verification | Yes (internal) |
| NeMo Egress Guard | Gemini API | HTTPS/443 | — | **BLOCKED** |
| Qdrant | any external | HTTPS/443 | — | **BLOCKED** |
| Demo Runner | Router | HTTP/8080 | Test requests | Yes (cross-namespace) |
| Any pod | Gemini API | HTTPS/443 | Direct SaaS | **BLOCKED** |

## Integration Points with Existing Infrastructure

### Semantic Claw Router (Config Extension)

The router's YAML config gets new sensitivity-related signals and decisions:

```yaml
# Added to existing config
signals:
  keywords:
    - name: "sensitivity_pii"
      operator: "OR"
      keywords: ["salary", "SSN", "social security", "date of birth", "home address"]
    - name: "sensitivity_hr"
      operator: "OR"
      keywords: ["performance review", "termination", "hiring", "firing", "compensation"]
    - name: "sensitivity_financial"
      operator: "OR"
      keywords: ["quarterly earnings", "revenue", "profit margin", "stock price", "insider"]

  semantic:
    enabled: true
    model_name: "all-MiniLM-L6-v2"
    sensitivity_anchors:
      PUBLIC: [...]
      INTERNAL: [...]
      CONFIDENTIAL: [...]
      REGULATED: [...]
      NEVER_EGRESS: [...]

decisions:
  - name: "sensitivity-block"
    priority: 200  # Higher than all complexity routes
    rules:
      operator: "OR"
      conditions:
        - type: "sensitivity"
          level: ["CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]
    modelRefs:
      - model: "qwen3.6-35b-a3b"  # Force local

  - name: "sensitivity-redact"
    priority: 150
    rules:
      operator: "AND"
      conditions:
        - type: "sensitivity"
          level: ["INTERNAL"]
        - type: "complexity"
          tier: ["COMPLEX", "REASONING"]
    actions:
      - redact: true
    modelRefs:
      - model: "gemini-3.1-pro-preview"
```

### Qwen 3.6 (Local Model Lane)

Used for two purposes:
1. **Primary inference** for sensitive traffic (private lane)
2. **Rail evaluation** for NeMo Guardrails (guardrails don't call SaaS to make guard decisions)

Endpoint: `http://ollama-qwen36.homelab-maas.svc.cluster.local:11434/v1/chat/completions`

### Gemini API (SaaS Lane)

Used only for sanitized traffic. The redaction service acts as the egress gateway:
- Only the redaction-service pod has NetworkPolicy permission to call `generativelanguage.googleapis.com`
- All requests pass through Presidio before reaching Gemini
- Responses pass through NeMo output rail before returning

## Failure Modes and Mitigations

| Failure Mode | Impact | Mitigation |
|-------------|--------|------------|
| Sensitivity classifier fails | Request may be under-classified | Default to LOCAL_ONLY (fail-safe) |
| Redaction service unavailable | Cannot sanitize for SaaS | Route to local model (graceful degradation) |
| Guardrails service unavailable | No input/output rails | Route to local model (fail-safe) |
| Egress guard unavailable | Cannot verify redaction quality | Route to local model (fail-safe block) |
| Qdrant unavailable | No RAG context | Proceed without RAG (reduced quality, not a security risk) |
| Qwen 3.6 unavailable | No local inference | Redact everything and route to SaaS (reduced privacy) |
| NetworkPolicy misconfigured | Pods may bypass redaction | ACS alert on unexpected egress, compliance operator audit |
| Presidio false negative | PII leaks through | GLiNER as secondary detector, NeMo output rail as final catch |
| Gemini API error | SaaS lane fails | Graceful degradation to local model |

## Deployment Sequence

Services must deploy in this order due to dependencies:

1. **Namespace** (`semantic-redacted`) — Everything else lives here
2. **NetworkPolicies** — Default-deny egress FIRST, before any pods exist
3. **Qdrant** — Vector store, no dependencies
4. **Redaction Service** — Depends on: nothing internal (calls Gemini externally)
5. **Guardrails Service** — Depends on: Qwen 3.6 (cross-namespace, already running)
6. **NeMo Egress Guard** — Depends on: Qwen 3.6 (LLM backend), redaction-service (scan verification)
7. **Router Config Update** — Depends on: redaction-service, guardrails-service, nemo-egress-guard endpoints
7. **RAG Document Load** — Depends on: Qdrant running
8. **Demo Runner** — Depends on: everything above

## Observability Design

### Structured Log Fields (JSON)

Every service emits logs with these common fields:
- `timestamp` (ISO 8601)
- `service` (redaction-service | guardrails-service | router)
- `request_id` (correlation ID, propagated via `X-Request-ID` header)
- `level` (INFO | WARN | ERROR)
- `event` (classify | redact | restore | scan | route | block)

### Metrics (Future: Prometheus)

- `semantic_redacted_requests_total{sensitivity, complexity, action}`
- `semantic_redacted_redaction_entities_total{entity_type}`
- `semantic_redacted_egress_blocked_total`
- `semantic_redacted_rail_triggered_total{rail_type, action}`
- `semantic_redacted_latency_seconds{stage}`

### Audit Trail

For compliance, every request that involves redaction produces:
- Sensitivity classification result + confidence
- Number of entities redacted (by type)
- Whether egress was approved
- Which model received the request
- Output scan result

The audit trail NEVER contains the original sensitive values or the mapping table.
