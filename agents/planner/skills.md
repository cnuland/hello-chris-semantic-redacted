# Planner Agent -- Skills Inventory

## Overview

This document defines the capabilities available to the Planner agent. Each skill includes its purpose, when to use it, expected inputs and outputs, and concrete examples relevant to the privacy-preserving semantic routing project.

---

## Skill 1: Requirements Decomposition

**Purpose:** Break high-level project goals into discrete, implementable tasks with clear boundaries and acceptance criteria.

**When to use:** At the start of planning, after reading all input documents. This is the primary skill -- everything else feeds into or validates its output.

**Process:**
1. Extract every functional requirement from the input documents
2. Identify implicit requirements (health checks, resource limits, graceful shutdown)
3. Group related requirements into logical work units
4. Define boundaries: what is IN each task, what is NOT
5. Write acceptance criteria for each task
6. Assign effort estimates

**Example -- Decomposing the Redaction Service:**

High-level requirement from overview.md:
> "Presidio + GLiNER redaction service detects and pseudonymizes PII, secrets, and custom entities"

Decomposed into tasks:

```
TASK-004: Presidio Redaction Service
  Description: Deploy a FastAPI service that accepts text input, runs Presidio
  analysis with GLiNER-backed recognizers, and returns pseudonymized text with
  a reversible placeholder mapping.

  Acceptance Criteria:
  - AC-010: POST /redact with body {"text": "John Smith works at Acme Corp"}
    returns {"redacted_text": "<PERSON_1> works at <ORGANIZATION_1>",
    "mapping": {"<PERSON_1>": "John Smith", "<ORGANIZATION_1>": "Acme Corp"}}
    with status 200
  - AC-011: POST /redact with body {"text": "The weather is sunny"} returns
    the text unchanged with an empty mapping (no false positives on benign input)
  - AC-012: GET /health returns 200 with {"status": "healthy"}
  - AC-013: Service runs as non-root user in the semantic-redacted namespace
  - AC-014: Container image uses UBI9 base with multi-stage build
  - AC-015: Every redaction request produces a structured JSON audit log entry
    with fields: request_id, entity_count, entity_types, processing_time_ms

  Effort: L
  Risk: MEDIUM (GLiNER model download may fail in restricted networks)
```

---

## Skill 2: Risk Assessment

**Purpose:** Identify what can go wrong, estimate probability and impact, and define mitigations the Programmer can implement.

**When to use:** After completing the task breakdown. Evaluate each task and each integration point for failure modes.

**Process:**
1. For each task, ask: "What if this fails? What if this is slower than expected? What if this produces wrong results?"
2. For each integration point, ask: "What if the upstream service is down? What if the response format changes? What if latency exceeds budget?"
3. For the system as a whole, ask: "What if a component is bypassed? What if secrets leak? What if NetworkPolicy has a gap?"
4. Score each risk: Probability (LOW/MEDIUM/HIGH) x Impact (LOW/MEDIUM/HIGH/CRITICAL)
5. Define mitigations that are actionable by the Programmer
6. Define fallbacks for when mitigations are insufficient

**Example -- Risk Register Entries:**

```
RISK-001: Presidio False Negatives
  Description: Presidio fails to detect a PII entity, allowing sensitive data
  to pass through redaction and reach the SaaS endpoint.
  Probability: MEDIUM (Presidio warns about this in their own docs)
  Impact: CRITICAL (defeats the entire privacy guarantee)
  Mitigation: Layer GLiNER on top of Presidio's built-in recognizers for
  higher recall. Run a secondary regex pass for common patterns (SSN, email,
  phone). Include a confidence threshold below which the request is routed
  locally instead of redacted.
  Fallback: If redaction confidence is below threshold, route to local Qwen
  regardless of complexity classification.

RISK-003: NeMo Guardrails Latency
  Description: NeMo Guardrails adds >500ms to each request, making the
  redact-then-route path unacceptably slow.
  Probability: MEDIUM (guardrails frameworks are known to add latency)
  Impact: MEDIUM (demo still works but feels slow)
  Mitigation: Profile guardrails independently. Use async processing where
  possible. Consider running input and output rails as separate lightweight
  services rather than a monolithic guardrails server.
  Fallback: For the demo, guardrails can be shown as a separate validation
  step rather than inline in the request path.

RISK-006: NetworkPolicy Egress Bypass
  Description: A misconfigured NetworkPolicy allows a non-gateway pod to
  reach generativelanguage.googleapis.com, breaking the trust boundary.
  Probability: LOW (if policies are applied correctly)
  Impact: CRITICAL (invalidates the entire security narrative)
  Mitigation: Include a negative test: exec into the redaction service pod
  and attempt to curl the Gemini endpoint. The connection must be refused
  or time out. This is demo scenario 6.
  Fallback: None -- this must work. If NetworkPolicy is insufficient,
  escalate for Cilium or Istio-level enforcement.
```

---

## Skill 3: Dependency Mapping

**Purpose:** Identify all prerequisites, ordering constraints, and integration points so the Programmer never discovers a missing dependency mid-implementation.

**When to use:** After task decomposition, before finalizing the deployment sequence.

**Process:**
1. For each task, list what must exist before it can start (namespace, secrets, base images, upstream services)
2. For each service, list what it calls (endpoints, ports, protocols) and what calls it
3. For cross-namespace communication, document the full network path
4. For secrets, trace the origin: existing cluster secret, new secret to create, or derived/generated
5. Produce a dependency graph (text-based, no images needed)

**Example -- Dependency Map:**

```
Namespace: semantic-redacted
  Prerequisite: Namespace must be created with appropriate labels
  Prerequisite: Gemini API key secret copied from homelab-maas or created new

Service: sensitivity-classifier
  Depends on: namespace exists, UBI9 base image accessible
  Called by: decision-engine (internal, port 8080)
  Calls: nothing external (CPU-only local inference)

Service: redaction-service
  Depends on: namespace exists, GLiNER model available (downloaded at build or init)
  Called by: decision-engine (when route=REDACT_THEN_SAAS)
  Calls: nothing external (all processing is local)
  Note: GLiNER model must be baked into image or downloaded on startup

Service: egress-gateway
  Depends on: namespace exists, Gemini API key secret mounted
  Called by: decision-engine (after redaction, for SaaS routing)
  Calls: generativelanguage.googleapis.com (external, HTTPS 443)
  Note: This is the ONLY pod with external egress allowed

NetworkPolicy: default-deny-egress
  Depends on: all services deployed and verified functional first
  Affects: all pods in semantic-redacted namespace
  Exception: egress-gateway pod (labeled for policy exemption)
  Verification: negative test from non-gateway pod
```

**Cross-Namespace References:**
```
semantic-redacted -> homelab-maas:
  - decision-engine calls semantic-claw-router.homelab-maas.svc:8080
    (for complexity classification)
  - decision-engine calls ollama-qwen36.homelab-maas.svc:11434
    (for local model inference on LOCAL-routed requests)
  Note: These cross-namespace calls require no special NetworkPolicy
  if default-deny is only on egress, not ingress from other namespaces.
```

---

## Skill 4: Architecture Validation

**Purpose:** Verify that the proposed task breakdown and deployment plan are consistent with the architecture described in the design documents and the hard constraints in CLAUDE.md.

**When to use:** After task decomposition and dependency mapping, as a validation pass before writing the final handoff.

**Process:**
1. Walk through the architecture diagram in overview.md box by box -- is every box covered by a task?
2. Walk through every cell in the 2D routing matrix -- is every routing outcome exercised by at least one acceptance criterion?
3. Walk through every hard constraint in CLAUDE.md -- does any task violate any constraint?
4. Walk through every demo scenario -- can it be executed with the planned services?
5. Check for orphan components: services planned but never called, or services called but never planned

**Example -- Validation Checklist:**

```
Architecture Diagram Coverage:
  [x] Sensitivity classification (TASK-003)
  [x] Decision engine with 2D matrix (TASK-006)
  [x] NeMo Guardrails input rail (TASK-007)
  [x] Presidio redaction (TASK-004)
  [x] Egress gateway (TASK-008)
  [x] NeMo Guardrails output rail (TASK-007, same service)
  [x] Presidio restore (TASK-005)
  [x] NetworkPolicy (TASK-009)
  [ ] Audit logging -- NOT a separate task, embedded in each service task

Routing Matrix Coverage:
  [x] PUBLIC + SIMPLE -> SaaS (Demo scenario 1, AC-030)
  [x] CONFIDENTIAL + SIMPLE -> Local (Demo scenario 2, AC-031)
  [x] CONFIDENTIAL + COMPLEX -> Redact>SaaS (Demo scenario 4, AC-033)
  [x] REGULATED + any -> Local (Demo scenario 5, AC-034)
  [ ] INTERNAL + MEDIUM -> Redact>SaaS -- MISSING, need to add AC

Constraint Check:
  [x] All services in semantic-redacted namespace
  [x] UBI9 base images specified in container tasks
  [x] Python 3.11+ specified
  [x] CPU-only (no GPU requests in any manifest)
  [x] No hardcoded secrets (all tasks reference K8s Secrets)
  [x] Structured JSON audit logs in every service task
  [x] No modifications to existing services
```

---

## Skill 5: Acceptance Criteria Writing

**Purpose:** Produce acceptance criteria that the Tester can verify mechanically -- no interpretation required, no "ask the Programmer what this means" needed.

**When to use:** During task decomposition, for every task.

**Process:**
1. For each task, identify the observable behaviors it must produce
2. For each behavior, write a criterion with: input, expected output, and how to verify
3. Use specific values, status codes, field names, and endpoint paths
4. Include both positive tests (it does the right thing) and negative tests (it rejects the wrong thing)
5. Include operational criteria: health checks, resource limits, logging format

**Example -- Acceptance Criteria Patterns:**

**API behavior:**
```
AC-010: POST /redact with body {"text": "John Smith works at Acme Corp"}
returns status 200 with response body containing "redacted_text" where
"John Smith" is replaced with a PERSON placeholder and "Acme Corp" is
replaced with an ORGANIZATION placeholder.
```

**Negative test (security):**
```
AC-040: From within the redaction-service pod, running
`curl -s -o /dev/null -w "%{http_code}" https://generativelanguage.googleapis.com`
returns a connection timeout or connection refused (not 200, 301, or 403).
```

**Integration behavior:**
```
AC-050: Sending the prompt "What is Sarah Chen's Q3 performance rating?"
through the decision engine results in:
  1. Sensitivity classification: CONFIDENTIAL
  2. Route decision: LOCAL
  3. Response served by Qwen 3.6 (verified by response header or audit log)
  4. No outbound connection to generativelanguage.googleapis.com
```

**Audit logging:**
```
AC-060: Every routing decision produces a JSON log line to stdout containing
at minimum: request_id (UUID), timestamp (ISO 8601), complexity_tier
(SIMPLE|MEDIUM|COMPLEX|REASONING), sensitivity_level
(PUBLIC|INTERNAL|CONFIDENTIAL|REGULATED|NEVER_EGRESS), route_decision
(DIRECT_SAAS|REDACT_THEN_SAAS|LOCAL_ONLY), and processing_time_ms (integer).
```

**End-to-end demo scenario:**
```
AC-070: Demo Scenario 4 (Redact and Route) -- Submitting a prompt that
mentions customer names and internal project codes results in:
  1. Redaction service replaces names with <CUSTOMER_N> placeholders
  2. Redacted prompt is forwarded to Gemini via egress gateway
  3. Gemini response contains placeholders, not original names
  4. Restore service replaces placeholders with original values
  5. Final response to client contains original names
  6. Audit log shows redaction_count > 0 and route=REDACT_THEN_SAAS
```

---

## Skill 6: Timeline Estimation

**Purpose:** Provide effort sizing for each task so the Programmer can prioritize and the Reviewer can assess whether the plan is realistic.

**When to use:** During task decomposition, assigned per task.

**Sizing scale:**
- **S (Small):** Single file, straightforward configuration, well-documented pattern. Examples: namespace YAML, secret manifest, NetworkPolicy, health check endpoint.
- **M (Medium):** One service with 2-3 endpoints, standard patterns, minimal integration. Examples: sensitivity classifier service, restore service.
- **L (Large):** One service with complex logic, multiple integration points, or external dependencies. Examples: redaction service (Presidio + GLiNER integration), decision engine (2D matrix, multiple upstream calls).
- **XL (Extra Large):** Multi-service integration, complex configuration, significant testing surface. Examples: NeMo Guardrails with custom rails, full end-to-end demo validation.

**Example -- Effort Estimation:**

```
TASK-001: Namespace and RBAC setup               S
TASK-002: Secrets management (Gemini API key)     S
TASK-003: Sensitivity classifier service          M
TASK-004: Presidio redaction service              L
TASK-005: Presidio restore service                M
TASK-006: Decision engine (2D routing)            L
TASK-007: NeMo Guardrails (input + output rails)  XL
TASK-008: Egress gateway service                  M
TASK-009: NetworkPolicy (default-deny + allow)    S
TASK-010: Demo scenario test fixtures             M
TASK-011: Integration test suite                  L
```

**Critical path:** TASK-001 -> TASK-002 -> TASK-003 + TASK-004 (parallel) -> TASK-006 -> TASK-008 -> TASK-009

---

## Tool Access

The Planner agent has access to the following tools, all in **read-only** mode:

### File Operations
- **Read files:** Read any file in the project repository or referenced repositories
- **Search codebase:** Search for patterns, function names, configurations across files
- **List directories:** Explore project structure to understand what exists

### Cluster State (Read-Only)
- **kubectl get:** Query existing resources in the cluster (pods, services, secrets, networkpolicies)
- **kubectl describe:** Inspect resource details for existing infrastructure
- **oc get:** OpenShift-specific resource queries (routes, deploymentconfigs, SCCs)

### What the Planner CANNOT Do With Tools
- Write or modify any file (except handoff.md and pipeline-state.md)
- Create or delete Kubernetes resources
- Execute code or run tests
- Build or push container images
- SSH into nodes or exec into pods

### Example Tool Usage

**Understanding existing infrastructure:**
```
kubectl get pods -n homelab-maas
kubectl get svc -n homelab-maas
kubectl get secrets -n homelab-maas | grep gemini
kubectl get networkpolicy -n homelab-maas
```

**Checking what exists in the project:**
```
find src/ -name "*.py" -type f
find manifests/ -name "*.yaml" -type f
find tests/ -name "*.py" -type f
```

**Reading reference implementations:**
```
Read: src/classifier/main.py (if exists)
Read: manifests/namespace.yaml (if exists)
Read: ../semantic-claw-router/src/classifier.py (reference only)
```

---

## Skill Interaction Model

Skills are not used in isolation. The typical planning workflow chains them:

```
Requirements Decomposition
        │
        ▼
Dependency Mapping ──────► Architecture Validation
        │                          │
        ▼                          ▼
Risk Assessment              Fix gaps found
        │                     in validation
        ▼                          │
Timeline Estimation                │
        │                          │
        ▼                          ▼
Acceptance Criteria Writing (final pass, informed by all above)
        │
        ▼
    handoff.md
```

Each skill may reveal information that requires revisiting an earlier skill. For example, dependency mapping may reveal a missing task (go back to decomposition), or architecture validation may reveal an uncovered routing matrix cell (go back to acceptance criteria writing).

The Planner iterates internally until the plan is self-consistent before writing the handoff.
