# Demo Scenarios: End-to-End Privacy-Preserving Routing

## Overview

Six scenarios demonstrate the complete privacy-preserving semantic routing pipeline. Each scenario shows a different routing decision and the controls that enforce it.

The demo narrative builds progressively:
1. Establish the baseline (public traffic works normally)
2. Show the problem (sensitive content would leak)
3. Show the fix (sensitivity classification catches it)
4. Show redaction (sanitize and route to SaaS)
5. Show compliance (regulated data stays local)
6. Show enforcement (platform prevents bypass)

---

## Scenario 1: Public Query — Direct SaaS Routing

**Purpose:** Establish that public content flows to SaaS models without interference. This is the baseline — nothing changes for non-sensitive traffic.

**Input:**
```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": "Explain the CAP theorem in distributed systems. What are the tradeoffs between consistency, availability, and partition tolerance?"
    }
  ]
}
```

**Expected Classification:**
- Complexity: MEDIUM (technical question, moderate depth)
- Sensitivity: PUBLIC (general knowledge, no sensitive data)

**Expected Routing:**
- Action: DIRECT_SAAS
- Model: Gemini 3.1 Flash (cost-optimized for MEDIUM)
- Redaction: None
- Guardrails: Input rail passes (PUBLIC), no retrieval rail, no output rail

**Expected Response Headers:**
```
x-scr-tier: MEDIUM
x-scr-sensitivity: PUBLIC
x-scr-routing-action: DIRECT_SAAS
x-scr-model: gemini-3.1-flash-preview
x-scr-redaction-count: 0
```

**Expected Audit Log:**
```json
{
  "event": "route",
  "complexity_tier": "MEDIUM",
  "sensitivity_level": "PUBLIC",
  "routing_action": "DIRECT_SAAS",
  "target_model": "gemini-3.1-flash-preview",
  "redaction_count": 0,
  "egress_approved": true
}
```

**What This Proves:** Non-sensitive traffic is not affected by the privacy controls. No latency added, no content modified.

---

## Scenario 2: Confidential RAG Query — Local Only

**Purpose:** Demonstrate that when a query retrieves sensitive RAG content, the entire request stays local regardless of the query's own sensitivity level.

**Setup:** A synthetic company document is loaded into Qdrant with sensitivity metadata `NEVER_EGRESS`. The document contains quarterly earnings, employee names, and project codenames.

**Input:**
```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": "What were the key highlights from last quarter's financial review?"
    }
  ]
}
```

**Expected Classification:**
- Complexity: SIMPLE (straightforward recall question)
- Sensitivity (query alone): PUBLIC (generic question, no PII)
- Sensitivity (after RAG): NEVER_EGRESS (inherited from retrieved document chunks)

**Expected Routing:**
- Action: LOCAL_ONLY
- Model: Qwen 3.6 (local)
- Redaction: None (staying local)
- Guardrails: Retrieval rail filters — if SaaS was considered, chunks would be blocked. Since routing is LOCAL_ONLY, full chunks are included.

**Expected RAG Retrieval:**
```json
{
  "chunks": [
    {
      "text": "Q3 2026 Revenue: $4.2M (+12% QoQ). Net income: $890K...",
      "metadata": {"sensitivity": "NEVER_EGRESS", "source": "quarterly_review_q3_2026.md"}
    },
    {
      "text": "Project Phoenix delivered ahead of schedule. Team lead Sarah Chen...",
      "metadata": {"sensitivity": "NEVER_EGRESS", "source": "quarterly_review_q3_2026.md"}
    }
  ]
}
```

**Expected Audit Log:**
```json
{
  "event": "route",
  "complexity_tier": "SIMPLE",
  "sensitivity_level": "NEVER_EGRESS",
  "sensitivity_source": "rag_context_inheritance",
  "routing_action": "LOCAL_ONLY",
  "target_model": "qwen3.6-35b-a3b",
  "rag_chunks_retrieved": 2,
  "rag_sensitivity_override": true,
  "egress_approved": false
}
```

**What This Proves:** RAG context inheritance works. Even a harmless query stays local if the retrieved documents are sensitive. The financial data in the RAG document never leaves the cluster.

---

## Scenario 3: HR-Sensitive Conversation — Confidential, Local Only

**Purpose:** Show that HR-related conversations are classified as CONFIDENTIAL regardless of their structural complexity, and always route to the local model.

**Input:**
```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": "Draft a performance improvement plan for Marcus Johnson. He's been consistently missing sprint commitments for the last two quarters and his peer feedback scores dropped from 4.2 to 2.8. His current salary is $165,000 and he's been with the company for 3 years."
    }
  ]
}
```

**Expected Classification:**
- Complexity: COMPLEX (requires drafting a structured document with multiple inputs)
- Sensitivity: CONFIDENTIAL (employee name, performance data, salary, personnel action)

**Expected Routing:**
- Action: LOCAL_ONLY
- Model: Qwen 3.6 (local)
- Redaction: None (staying local)
- Guardrails: Input rail blocks SaaS routing (CONFIDENTIAL)

**Expected Audit Log:**
```json
{
  "event": "route",
  "complexity_tier": "COMPLEX",
  "sensitivity_level": "CONFIDENTIAL",
  "sensitivity_signals": ["PERSON", "MONEY", "hr_keywords:performance_improvement_plan,salary"],
  "routing_action": "LOCAL_ONLY",
  "target_model": "qwen3.6-35b-a3b",
  "redaction_count": 0,
  "egress_approved": false,
  "guardrail_triggered": "sensitivity_check"
}
```

**What This Proves:** HR content with employee PII stays local. The sensitivity classifier catches both the HR keywords (performance improvement plan) and PII (name, salary). Even though Gemini Pro could produce a better PIP, the privacy controls correctly prevent the data from leaving.

---

## Scenario 4: Sanitizable Query — Redact, Route to SaaS, Restore

**Purpose:** This is the most complex scenario. It demonstrates the full redact → route → scan → restore pipeline. Content has identifiable entities that can be pseudonymized, allowing a more capable SaaS model to process it while protecting specific data.

**Input:**
```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": "Analyze the architecture of Project Phoenix deployed on the ironman.cjlabs.dev cluster. The lead engineer Sarah Chen designed a microservices system in the homelab-maas namespace with 5 services. Review the design for scalability issues and suggest improvements. Her contact is sarah.chen@company.com."
    }
  ]
}
```

**Expected Classification:**
- Complexity: COMPLEX (architecture review, multi-factor analysis)
- Sensitivity: INTERNAL (internal project name, cluster name, namespace, employee name, email)

**Expected Routing:**
- Action: REDACT_THEN_SAAS
- Model: Gemini 3.1 Pro (COMPLEX tier)

**Expected Redaction (before SaaS):**
```
Original: "Analyze the architecture of Project Phoenix deployed on the ironman.cjlabs.dev cluster. The lead engineer Sarah Chen designed a microservices system in the homelab-maas namespace with 5 services. Review the design for scalability issues and suggest improvements. Her contact is sarah.chen@company.com."

Redacted: "Analyze the architecture of <PROJECT_1> deployed on the <CLUSTER_1> cluster. The lead engineer <PERSON_1> designed a microservices system in the <NAMESPACE_1> namespace with 5 services. Review the design for scalability issues and suggest improvements. Her contact is <EMAIL_1>."

Mapping (stored in-memory, NEVER sent to SaaS):
  <PROJECT_1>   → Project Phoenix
  <CLUSTER_1>   → ironman.cjlabs.dev
  <PERSON_1>    → Sarah Chen
  <NAMESPACE_1> → homelab-maas
  <EMAIL_1>     → sarah.chen@company.com
```

**Expected SaaS Response (with placeholders):**
```
"Based on the <PROJECT_1> architecture in <NAMESPACE_1>, I'd recommend:
1. Service mesh for inter-service communication...
2. Horizontal pod autoscaling for the stateless services...
3. <PERSON_1> should consider circuit breakers..."
```

**Expected Egress Guard Result (NeMo):**
```json
{
  "approved": true,
  "action": "ALLOW",
  "reason": "All egress rails passed",
  "rails_triggered": [],
  "latency_ms": 12.3
}
```
- Verifies: no residual PII, no secrets, placeholder integrity
- Checks completed before redacted text leaves the cluster

**Expected Output Rail:**
- Scan result: CLEAN (no reconstructed PII in response)

**Expected Restoration (inside cluster):**
```
"Based on the Project Phoenix architecture in homelab-maas, I'd recommend:
1. Service mesh for inter-service communication...
2. Horizontal pod autoscaling for the stateless services...
3. Sarah Chen should consider circuit breakers..."
```

**Expected Audit Log:**
```json
{
  "event": "route",
  "complexity_tier": "COMPLEX",
  "sensitivity_level": "INTERNAL",
  "routing_action": "REDACT_THEN_SAAS",
  "target_model": "gemini-3.1-pro-preview",
  "redaction_count": 5,
  "entity_types_redacted": ["PROJECT", "CLUSTER_NAME", "PERSON", "K8S_NAMESPACE", "EMAIL_ADDRESS"],
  "egress_approved": true,
  "output_scan_result": "CLEAN",
  "placeholders_restored": 3
}
```

**What This Proves:** The full redact-route-scan-restore pipeline works end-to-end. Gemini sees only placeholders. The response uses placeholders naturally. Restoration happens inside the cluster. The mapping never leaves memory.

---

## Scenario 5: Financial Data — Regulated, Local Only

**Purpose:** Show that regulated financial data always stays local, even when the question is complex enough that a frontier model would produce a better answer.

**Input:**
```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": "Analyze our Q3 2026 earnings data: revenue was $4.2M with 12% quarter-over-quarter growth, net income $890K, and EBITDA margin at 21.2%. Compare this against industry benchmarks and prepare a summary for the SEC 10-K filing. Include projections for Q4 based on current pipeline."
    }
  ]
}
```

**Expected Classification:**
- Complexity: REASONING (financial analysis, projections, formal document preparation)
- Sensitivity: REGULATED (financial data, SEC filing, pre-publication earnings)

**Expected Routing:**
- Action: LOCAL_ONLY
- Model: Qwen 3.6 (local)
- Guardrails: Input rail blocks SaaS routing (REGULATED), enhanced audit logging

**Expected Audit Log:**
```json
{
  "event": "route",
  "complexity_tier": "REASONING",
  "sensitivity_level": "REGULATED",
  "sensitivity_signals": ["financial_keywords:earnings,revenue,EBITDA,SEC_filing", "MONEY"],
  "routing_action": "LOCAL_ONLY",
  "target_model": "qwen3.6-35b-a3b",
  "redaction_count": 0,
  "egress_approved": false,
  "compliance_flag": "regulated_data",
  "guardrail_triggered": "sensitivity_check"
}
```

**What This Proves:** Regulated data stays local even when a frontier model would perform better. The compliance obligation outweighs the quality benefit. The audit log explicitly flags the compliance context.

---

## Scenario 6: Bypass Attempt — NetworkPolicy Enforcement

**Purpose:** Prove that even if application controls fail, the platform prevents unauthorized egress. This is the defense-in-depth argument — the platform is the enforcement boundary, not the application.

**Test Procedure:**

### Step 1: Attempt direct SaaS call from guardrails pod

```bash
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -v --connect-timeout 5 \
  https://generativelanguage.googleapis.com/v1/models
```

**Expected:** Connection timeout or refused. The guardrails pod does NOT have the `role: egress-gateway` label.

### Step 2: Attempt direct SaaS call from qdrant pod

```bash
oc exec -it deploy/qdrant -n semantic-redacted -- \
  curl -v --connect-timeout 5 \
  https://api.openai.com/v1/models
```

**Expected:** Connection timeout or refused.

### Step 3: Verify redaction service CAN reach SaaS

```bash
oc exec -it deploy/redaction-service -n semantic-redacted -- \
  curl -v --connect-timeout 10 \
  https://generativelanguage.googleapis.com/v1/models
```

**Expected:** HTTP response (200 or 401). The redaction-service pod HAS the `role: egress-gateway` label.

### Step 4: Verify internal communication works for blocked pods

```bash
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://redaction-service.semantic-redacted.svc:8000/health
```

**Expected:** HTTP 200 with health JSON. Internal traffic is allowed.

**Expected Audit Log (for blocked attempts):**

NetworkPolicy blocks happen at the kernel/CNI level, so the application never sees the request. The evidence is:
- The `curl` command times out (no TCP connection established)
- OpenShift OVN-Kubernetes flow logs (if enabled) show the drop
- ACS (if deployed) would alert on the attempt

**What This Proves:** The trust boundary is enforced by the platform, not just by the application. Even if a developer accidentally adds a direct SaaS call to the guardrails service, NetworkPolicy blocks it. This is the strongest part of the Red Hat story: OpenShift is the enforcement boundary.

---

## Demo Execution Order

Run scenarios in order 1 → 2 → 3 → 4 → 5 → 6 for the best narrative flow:

1. **Scenario 1** establishes the baseline (everything works for public content)
2. **Scenario 2** introduces RAG governance (context inheritance)
3. **Scenario 3** shows personnel data protection (HR sensitivity)
4. **Scenario 4** demonstrates the full redaction pipeline (the technical centerpiece)
5. **Scenario 5** adds compliance context (regulated data)
6. **Scenario 6** closes with platform enforcement (the Red Hat story)

## Success Criteria

All 6 scenarios must:
- [ ] Produce the correct routing action
- [ ] Route to the correct model
- [ ] Apply redaction only when expected
- [ ] Produce structured audit logs
- [ ] Complete without errors
- [ ] Demonstrate the claimed control (local-only, redaction, or policy block)

Scenario 4 specifically must:
- [ ] Successfully redact all 5 entity types
- [ ] Send only pseudonymized content to Gemini
- [ ] Receive a response that uses placeholders naturally
- [ ] Successfully restore placeholders in the response
- [ ] Pass output rail scanning
