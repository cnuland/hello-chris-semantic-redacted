# Planner Handoff

## Status: COMPLETE

## Summary

Decomposed the privacy-preserving semantic routing project into 14 implementable tasks across 4 work streams, with explicit acceptance criteria, deployment sequence, dependency map, and risk register. All tasks are scoped to be independently testable and deployable within the `semantic-redacted` namespace on OpenShift.

## Task Breakdown

### Work Stream 1: Foundation Services

#### Task 1.1: Redaction Service (Presidio + GLiNER)
**Description:** FastAPI service wrapping Microsoft Presidio and GLiNER for PII detection, pseudonymization, and restoration.

**Acceptance Criteria:**
- AC-1.1.1: `POST /redact` accepts text, returns redacted text with placeholder mapping
- AC-1.1.2: `POST /restore` accepts response text + mapping_id, returns restored text
- AC-1.1.3: `POST /scan` accepts text, returns clean/dirty with findings list
- AC-1.1.4: `GET /health` returns recognizer count and status
- AC-1.1.5: Built-in Presidio recognizers detect: PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, IP_ADDRESS, URL, US_SSN
- AC-1.1.6: Custom recognizers detect: CLUSTER_NAME (*.cjlabs.dev, *.svc.cluster.local), K8S_NAMESPACE, EMPLOYEE_ID
- AC-1.1.7: GLiNER detects zero-shot entities: project codenames, internal tool names
- AC-1.1.8: Mapping is request-scoped (in-memory only, never persisted, never logged)
- AC-1.1.9: Same entity appearing multiple times gets the same placeholder
- AC-1.1.10: Redaction recall > 95% on test corpus of 20+ prompts with known entities

**Effort:** L (4-6 hours)
**Dependencies:** None

#### Task 1.2: Guardrails Service (NeMo Guardrails)
**Description:** NeMo Guardrails service with input, retrieval, and output rails.

**Acceptance Criteria:**
- AC-1.2.1: `POST /guard/input` blocks CONFIDENTIAL/REGULATED/NEVER_EGRESS from SaaS routing
- AC-1.2.2: `POST /guard/input` detects secrets (API keys, JWT tokens, private keys)
- AC-1.2.3: `POST /guard/retrieval` filters RAG chunks by sensitivity level
- AC-1.2.4: `POST /guard/output` scans SaaS response for reconstructed PII
- AC-1.2.5: `GET /health` reports loaded rails and LLM backend reachability
- AC-1.2.6: Uses local Qwen 3.6 for LLM-based rail evaluation (no SaaS calls)
- AC-1.2.7: All rail decisions produce structured JSON audit logs

**Effort:** L (4-6 hours)
**Dependencies:** Qwen 3.6 must be running (existing, homelab-maas)

#### Task 1.3: Qdrant Vector Store + RAG Document
**Description:** Deploy Qdrant, create collection, load sensitive quarterly business review document.

**Acceptance Criteria:**
- AC-1.3.1: Qdrant deployment running in semantic-redacted namespace
- AC-1.3.2: Collection `sensitive_docs` created with 384-dim cosine similarity
- AC-1.3.3: 15 document chunks loaded with NEVER_EGRESS sensitivity metadata
- AC-1.3.4: Similarity search returns relevant chunks for financial/personnel queries
- AC-1.3.5: Health check returns collection stats (point count, status)

**Effort:** M (2-3 hours)
**Dependencies:** None

#### Task 1.4: NeMo Egress Guard (Real NeMo Guardrails)
**Description:** Dedicated egress checkpoint service using the actual `nemoguardrails` Python package. Evaluates redacted content with LLM-backed (Qwen 3.6) verification before it leaves the cluster to reach SaaS endpoints. Sits between the redaction step and the SaaS call in the REDACT_THEN_SAAS flow.

**Acceptance Criteria:**
- AC-1.4.1: `POST /guard/egress` accepts redacted text, sensitivity level, entity types, returns approved/blocked with reason
- AC-1.4.2: Blocks content with residual PII (email, SSN, phone) that escaped redaction
- AC-1.4.3: Blocks content with secrets (API keys, JWT tokens, PEM keys)
- AC-1.4.4: Blocks NEVER_EGRESS and REGULATED sensitivity levels (defense-in-depth)
- AC-1.4.5: Verifies placeholder integrity (`<TYPE_N>` format consistency)
- AC-1.4.6: `GET /health` reports NeMo availability, loaded rails, LLM backend
- AC-1.4.7: Uses Qwen 3.6 via llama-server (port 8080) for LLM-based evaluation
- AC-1.4.8: Fail-safe: if egress guard unreachable, demo runner blocks egress (not bypasses)
- AC-1.4.9: Pod does NOT have `role: egress-gateway` label — cannot directly call SaaS
- AC-1.4.10: Structured JSON audit logs for every egress decision
- AC-1.4.11: Dedicated ServiceAccount with `automountServiceAccountToken: false`

**Effort:** L (4-6 hours)
**Dependencies:** Qwen 3.6 running (homelab-maas), redaction-service (for /scan verification)

### Work Stream 2: Classification & Routing

#### Task 2.1: Sensitivity Classifier
**Description:** Embedding-based sensitivity scorer using all-MiniLM-L6-v2 and anchor prompts.

**Acceptance Criteria:**
- AC-2.1.1: Classifies prompts into 5 levels: PUBLIC, INTERNAL, CONFIDENTIAL, REGULATED, NEVER_EGRESS
- AC-2.1.2: Uses cosine similarity against 60 anchor prompts (12 per level)
- AC-2.1.3: Top-K=3 averaging per level, highest wins
- AC-2.1.4: Keyword-based fast-path for obvious cases (PII patterns, secret patterns, HR keywords)
- AC-2.1.5: Classification accuracy > 85% on test prompts per level
- AC-2.1.6: Latency < 50ms per classification
- AC-2.1.7: Produces structured JSON output with level, confidence, signals matched

**Effort:** M (2-3 hours)
**Dependencies:** None

#### Task 2.2: Sensitivity Anchor Data
**Description:** Create JSONL anchor data files for each sensitivity level.

**Acceptance Criteria:**
- AC-2.2.1: 12+ anchors per sensitivity level (60+ total)
- AC-2.2.2: Anchors are realistic and cover diverse domains
- AC-2.2.3: Test prompts (5+ per level) are distinct from anchors
- AC-2.2.4: Anchors stored in data/sensitivity-anchors/anchors.jsonl
- AC-2.2.5: Test prompts stored in data/test-prompts/*.jsonl

**Effort:** S (1-2 hours)
**Dependencies:** None

### Work Stream 3: Infrastructure & Deployment

#### Task 3.1: OpenShift Namespace + NetworkPolicies
**Description:** Create namespace and apply network policies before any services deploy.

**Acceptance Criteria:**
- AC-3.1.1: Namespace `semantic-redacted` created
- AC-3.1.2: Default-deny egress policy active
- AC-3.1.3: Allow-internal policy permits DNS, K8s API, intra-namespace, and homelab-maas access
- AC-3.1.4: Allow-sanitized-egress grants only `role: egress-gateway` pods external HTTPS
- AC-3.1.5: Non-gateway pods cannot reach external endpoints (verified by curl timeout)
- AC-3.1.6: Gateway pods CAN reach external endpoints (verified by curl success)

**Effort:** M (2-3 hours)
**Dependencies:** Cluster access

#### Task 3.2: Redaction Service Deployment
**Description:** Container build and OpenShift deployment for redaction service.

**Acceptance Criteria:**
- AC-3.2.1: Dockerfile builds successfully on UBI9 base
- AC-3.2.2: Deployment running with 1 replica, health check passing
- AC-3.2.3: Service ClusterIP reachable from other pods in namespace
- AC-3.2.4: Pod labeled with `app: redaction-service` AND `role: egress-gateway`
- AC-3.2.5: Gemini API key mounted from secret
- AC-3.2.6: Can reach external HTTPS endpoints (egress gateway)

**Effort:** M (2-3 hours)
**Dependencies:** Task 1.1 (service code), Task 3.1 (namespace + policies)

#### Task 3.3: Guardrails Service Deployment
**Description:** Container build and OpenShift deployment for guardrails service.

**Acceptance Criteria:**
- AC-3.3.1: Dockerfile builds successfully
- AC-3.3.2: Deployment running with 1 replica, health check passing
- AC-3.3.3: Pod labeled `app: guardrails-service` (NO `role: egress-gateway`)
- AC-3.3.4: Can reach Qwen 3.6 cross-namespace
- AC-3.3.5: CANNOT reach external HTTPS endpoints (blocked by NetworkPolicy)
- AC-3.3.6: Can reach redaction-service within namespace

**Effort:** M (2-3 hours)
**Dependencies:** Task 1.2 (service code), Task 3.1 (namespace + policies)

#### Task 3.4: Qdrant Deployment
**Description:** Deploy Qdrant vector store to OpenShift.

**Acceptance Criteria:**
- AC-3.4.1: Qdrant deployment running with PVC
- AC-3.4.2: Service reachable on port 6333
- AC-3.4.3: CANNOT reach external endpoints (blocked by NetworkPolicy)
- AC-3.4.4: Health check passing

**Effort:** S (1-2 hours)
**Dependencies:** Task 3.1 (namespace + policies)

#### Task 3.5: Router Config Update
**Description:** Update Semantic Claw Router config with sensitivity signals.

**Acceptance Criteria:**
- AC-3.5.1: Router config includes sensitivity keyword signals
- AC-3.5.2: Router config includes sensitivity anchor data
- AC-3.5.3: Routing decisions include sensitivity-based overrides (priority > complexity routes)
- AC-3.5.4: Router restarts successfully with new config
- AC-3.5.5: Existing complexity routing still works (no regression)

**Effort:** M (2-3 hours)
**Dependencies:** Task 2.1, Task 2.2

### Work Stream 4: Demo & Validation

#### Task 4.1: Demo Runner
**Description:** Python script that executes all 6 demo scenarios against the live cluster.

**Acceptance Criteria:**
- AC-4.1.1: Supports `--scenario N` for individual scenarios and `--all` for all 6
- AC-4.1.2: Each scenario produces structured JSON output (input, classification, routing, redaction, response)
- AC-4.1.3: Prints clear PASS/FAIL per scenario with evidence
- AC-4.1.4: Scenario 4 (redact-and-route) demonstrates full pipeline with visible placeholders
- AC-4.1.5: Scenario 6 (bypass attempt) shows NetworkPolicy enforcement
- AC-4.1.6: All 6 scenarios pass when run against deployed services

**Effort:** M (2-3 hours)
**Dependencies:** All services deployed

#### Task 4.2: Test Suite
**Description:** pytest test suite for unit and integration tests.

**Acceptance Criteria:**
- AC-4.2.1: Unit tests for redaction service (entity detection, pseudonymization, restoration)
- AC-4.2.2: Unit tests for sensitivity classifier (all 5 levels, boundary cases)
- AC-4.2.3: Integration tests for guardrails service (input/retrieval/output rails)
- AC-4.2.4: Egress policy tests (blocked vs allowed connections)
- AC-4.2.5: All tests pass with > 80% coverage on core modules
- AC-4.2.6: Tests can run locally (against port-forwarded services) or in-cluster

**Effort:** L (4-6 hours)
**Dependencies:** All service code complete

#### Task 4.3: Benchmarking Report
**Description:** Produce benchmarking results showing classification accuracy, redaction recall, latency, and demo scenario outcomes.

**Acceptance Criteria:**
- AC-4.3.1: Sensitivity classification accuracy per level (target > 85%)
- AC-4.3.2: Redaction recall per entity type (target > 95% for built-in, > 80% for custom)
- AC-4.3.3: Latency measurements for redaction, guardrails, and end-to-end
- AC-4.3.4: Demo scenario pass/fail matrix
- AC-4.3.5: NetworkPolicy enforcement evidence (blocked vs allowed)
- AC-4.3.6: Results written to `report.md`

**Effort:** M (2-3 hours)
**Dependencies:** All tests and demos complete

## Deployment Sequence

```
1. Namespace + NetworkPolicies  (Task 3.1)
2. Qdrant                       (Task 3.4) — no dependencies
3. Redaction Service             (Task 3.2) — depends on 3.1
4. Guardrails Service            (Task 3.3) — depends on 3.1
5. RAG Document Load             (Task 1.3) — depends on 3.4
6. Router Config Update          (Task 3.5) — depends on 3.2, 3.3
7. Demo Runner                   (Task 4.1) — depends on everything
8. Test Suite + Benchmarks       (Task 4.2, 4.3) — depends on everything
```

## Risk Register

| ID | Risk | Probability | Impact | Mitigation | Fallback |
|----|------|-------------|--------|------------|----------|
| R1 | Cluster unreachable during implementation | HIGH | HIGH | Build everything locally first, deploy when available | Local testing with mock services |
| R2 | Presidio false negatives on custom entities | MEDIUM | HIGH | Layer Presidio + GLiNER + regex patterns | Add more regex patterns, lower confidence threshold |
| R3 | NeMo Guardrails latency too high with Qwen | MEDIUM | MEDIUM | Use simple regex-based rails instead of LLM-based | Fall back to rule-based guardrails (no LLM eval) |
| R4 | GLiNER model download fails in container | MEDIUM | MEDIUM | Pre-download model in Dockerfile build | Use regex-only fallback for project codenames |
| R5 | NetworkPolicy not enforced (wrong SDN) | LOW | CRITICAL | Verify SDN type before deploying | Document as known limitation |
| R6 | Qwen 3.6 overloaded by guardrails + inference | MEDIUM | HIGH | Keep guardrails simple (avoid LLM-based rails) | Use regex/pattern rails only |
| R7 | OpenShift build fails on UBI9 image | LOW | MEDIUM | Test Dockerfile locally first | Use standard Python image |

## Decisions Made

1. **Simplified guardrails:** Given R3 and R6, the guardrails service will use regex/pattern-based rails rather than LLM-based evaluation to avoid Qwen overload. LLM-based jailbreak and reconstruction detection are deferred to a future iteration.

2. **Integrated architecture:** Rather than deploying the sensitivity classifier as a separate service, it will be integrated into the demo runner and redaction service as a library. This reduces deployment complexity.

3. **Local-first development:** Given R1 (cluster unreachability), all services will be built and tested locally first with mock endpoints. OpenShift deployment is the last step.

4. **Pragmatic NeMo approach:** NeMo Guardrails has complex dependencies. The guardrails service will implement the same API contract (`/guard/input`, `/guard/retrieval`, `/guard/output`) but use a lightweight custom implementation rather than the full NeMo framework. This preserves the architecture and API contract while reducing deployment risk.

## Assumptions

1. Qwen 3.6 is accessible at `ollama-qwen36.homelab-maas.svc.cluster.local:11434`
2. Gemini API key is available in homelab-maas secrets
3. OpenShift supports OVN-Kubernetes NetworkPolicy
4. Container builds can use the cluster's internal registry or quay.io
5. The existing semantic-claw-router config can be extended without modifying source code

## Acceptance Criteria Summary

| Category | Count | Target |
|----------|-------|--------|
| Redaction Service | 10 criteria | 100% pass |
| Guardrails Service | 7 criteria | 100% pass |
| Qdrant + RAG | 5 criteria | 100% pass |
| Sensitivity Classifier | 7 criteria | 100% pass |
| Anchor Data | 5 criteria | 100% pass |
| Namespace + Policies | 6 criteria | 100% pass |
| Service Deployments | 14 criteria | 100% pass |
| Demo Runner | 6 criteria | 100% pass |
| Test Suite | 6 criteria | 100% pass |
| Benchmarking | 6 criteria | 100% pass |
| **Total** | **72 criteria** | **>90% pass rate** |
