# Reviewer Skills Inventory

## Overview

The Reviewer agent possesses the following skill domains. Each skill is applied during the structured 7-step review methodology defined in `AGENT.md`. Skills are listed in order of evaluation priority.

---

## 1. Security Audit

**Purpose:** Identify vulnerabilities that could compromise the privacy-preserving guarantees of the system.

**Capabilities:**

- **OWASP analysis:** Evaluate the system against relevant OWASP Top 10 categories, with emphasis on injection (prompt injection that evades sensitivity classification), broken access control (unauthorized access to redaction mappings or restoration endpoints), security misconfiguration (permissive NetworkPolicy, default credentials), and sensitive data exposure (PII in logs, caches, error responses).

- **Secret exposure scanning:** Verify that no secrets (API keys, tokens, passwords) appear in source code, Kubernetes manifests, configuration files, log output, error messages, or stack traces. Confirm that all secrets are sourced from Kubernetes Secret resources via environment variable references.

- **Data leakage vector identification:** Map every path through which sensitive data could exit the cluster boundary. This includes direct SaaS API calls, log aggregation pipelines, error reporting services, semantic caches, debug endpoints, and response bodies where SaaS models may reconstruct redacted information from context.

- **Privilege and access checks:** Verify containers run as non-root, SecurityContext is restrictive, ServiceAccount permissions follow least-privilege, no hostPath or privileged mounts exist, and RBAC does not grant unnecessary cluster-level access.

- **NetworkPolicy enforcement verification:** Confirm default-deny egress is in place, only the designated egress gateway pod has external network access, and no alternative egress paths exist (different ports, protocols, or DNS-based bypasses).

---

## 2. Architecture Review

**Purpose:** Verify that the implemented system architecture matches the designed trust model and that no component violates the privacy boundary.

**Capabilities:**

- **Component coupling analysis:** Evaluate inter-service dependencies for tight coupling, circular dependencies, or inappropriate direct connections. Verify that the redaction service does not depend on SaaS availability. Confirm that the sensitivity classifier is self-contained and runs locally.

- **Trust boundary mapping:** Identify and verify every trust boundary in the system. The primary boundary is the cluster perimeter (OpenShift). Secondary boundaries include namespace isolation, pod-level NetworkPolicy, and service-to-service authentication. Verify that sensitive data (unredacted PII, redaction mappings, original entity values) never crosses the primary trust boundary.

- **Data flow tracing:** For each of the 5 sensitivity levels (PUBLIC, INTERNAL, CONFIDENTIAL, REGULATED, NEVER_EGRESS), trace the complete request/response path and verify that the correct routing decision is made. Confirm that the 2D routing matrix (complexity x sensitivity) is correctly implemented.

- **Failure mode analysis:** Evaluate what happens when individual components fail. Critical question: if the redaction service is unavailable, does the system fail open (send unredacted to SaaS -- CRITICAL vulnerability) or fail closed (route to local model -- correct behavior)?

- **Consistency verification:** Confirm that the architecture described in `overview.md`, the implementation in `src/`, the manifests in `manifests/openshift/`, and the test coverage in `tests/` all describe the same system. Inconsistencies between documentation and implementation are flagged.

---

## 3. Red Hat Alignment

**Purpose:** Verify that the project aligns with Red Hat's open-source strategy, uses OpenShift-native patterns, and supports the sovereignty narrative.

**Capabilities:**

- **Upstream OSS verification:** Confirm that every dependency is genuinely open source. Check license types (MIT, Apache-2.0, BSD are acceptable). Flag any "open core" products where critical features require an enterprise license. Verify specific versions of Presidio, GLiNER, NeMo Guardrails, and all Python dependencies.

- **OpenShift-native pattern verification:** Confirm that all deployments use standard Kubernetes resource types (Deployment, Service, ConfigMap, Secret, NetworkPolicy). Verify UBI9 base images for all containers. Check that labels follow the `app.kubernetes.io/*` convention. Confirm non-root security contexts. Verify dedicated namespace usage (`semantic-redacted`).

- **Narrative fit assessment:** Evaluate whether the demo supports the core thesis: "Semantic routing is safe only when the first trust decision happens inside the isolated environment. OpenShift becomes the enforcement boundary." The demo must position OpenShift as the enforcement boundary, not just a hosting platform. The composable open-source stack must be clearly visible. The sovereignty story must be compelling for a Red Hat field audience.

- **Product vs. project distinction:** Verify that the project references upstream open-source projects, not Red Hat product names. Use "OpenShift" (the project/platform), not specific product SKUs. Use "Kubernetes" for generic concepts. Reference community projects by their upstream names.

---

## 4. Scoring Methodology

**Purpose:** Apply the weighted rubric consistently and produce evidence-backed scores.

**Capabilities:**

- **Weighted rubric application:** Score each of the 6 dimensions (Redaction Accuracy, Architecture Integrity, Demo Completeness, Security Posture, Red Hat Alignment, Observability) using the criteria defined in `scorecard.md`. Each score is a value between 0.0 and 1.0.

- **Evidence-based scoring:** Every score must cite specific evidence. Acceptable evidence types include: test results (pass/fail counts), code references (file:line), manifest excerpts, log samples, metric values, and screenshots. Scores without evidence are invalid.

- **Threshold validation:** After scoring, verify that each dimension meets its minimum threshold. Flag any dimension that falls below threshold, as this affects the overall decision even if the weighted score is acceptable.

- **Weighted score calculation:** Compute the final weighted score as: `sum(dimension_score * dimension_weight)` across all 6 dimensions. Apply the decision matrix from `AGENT.md` to determine APPROVED, CONDITIONAL, or REJECTED.

- **Scoring calibration:** Apply consistent standards across dimensions. A score of 1.0 means exceptional, exceeding expectations. A score of 0.8 means meets expectations with minor gaps. A score of 0.5 means significant gaps. A score of 0.0 means the dimension is completely unaddressed.

---

## 5. Independent Verification

**Purpose:** Spot-check claims made by the Tester agent to ensure accuracy and completeness.

**Capabilities:**

- **Test result re-verification:** Select a representative sample of test results from `agents/tester/handoff.md` and independently verify them. This means reading the test implementation, understanding what it asserts, and confirming the assertion is meaningful (not a tautology or trivially true).

- **Assertion quality assessment:** Evaluate whether tests actually verify the claimed behavior. A test that calls an endpoint and checks for HTTP 200 without validating the response body is weak. A test that verifies specific redacted entities in the response is strong.

- **Log-based verification:** When the Tester claims a routing decision was made correctly, verify by examining the logging code and confirming that structured JSON logs would contain the claimed fields (sensitivity level, routing decision, redaction count, model selected).

- **Coverage gap identification:** Identify acceptance criteria from the Planner's handoff that the Tester did not adequately cover. Identify edge cases that no test addresses (e.g., mixed-sensitivity prompts, very long prompts that exceed token limits, concurrent requests).

- **Negative testing verification:** Confirm that the Tester included negative tests: prompts that should be blocked, egress attempts that should fail, invalid inputs that should return errors. Positive-only testing is insufficient for a security-critical system.

---

## 6. Code Review

**Purpose:** Evaluate code quality, maintainability, and adherence to project coding standards.

**Capabilities:**

- **Python best practices:** Verify Python 3.11+ usage, proper type hints on all public APIs, appropriate use of dataclasses/Pydantic models, clean import organization, and no deprecated patterns. Check for bare `except:` clauses, mutable default arguments, and other common Python pitfalls.

- **Kubernetes manifest quality:** Verify resource requests/limits, health checks (liveness and readiness probes), label consistency, ConfigMap/Secret usage, SecurityContext configuration, and image pull policies. Check for anti-patterns like `latest` tags, missing resource limits, or overly permissive RBAC.

- **Security patterns in code:** Verify input validation on all API endpoints, proper sanitization of user-supplied data before logging, no SQL injection or command injection vectors, and secure handling of the Gemini API key and other credentials.

- **Error handling robustness:** Verify that all external calls have try/except blocks with appropriate error handling, that the system fails closed (routes to local model) when redaction or classification fails, and that error responses do not leak internal system details.

- **Logging implementation:** Verify structured JSON logging is implemented consistently, that every routing decision produces an audit event, that no PII appears in log messages, and that log levels are used appropriately.

---

## 7. Demo Evaluation

**Purpose:** Assess whether the demo is ready to present and tells a compelling story.

**Capabilities:**

- **Narrative coherence assessment:** Evaluate whether the 6 demo scenarios build a logical progression from simple (public query) to complex (enforcement boundary). Each scenario should demonstrate a distinct capability. The overall narrative should support the core thesis about OpenShift as the enforcement boundary.

- **Visual evidence quality:** Evaluate whether the structured JSON logs, redaction reports, and NetworkPolicy enforcement evidence are clear and compelling. A demo audience should be able to follow the trust story through the log output without requiring deep technical explanation.

- **Log quality analysis:** Structured JSON logs should include: timestamp, request ID, sensitivity classification, complexity classification, routing decision, model selected, redaction count (if applicable), egress approval status (if applicable). Missing fields reduce the narrative impact.

- **Scenario completeness verification:** For each of the 6 scenarios, verify that: (1) an implementation exists, (2) a test covers it, (3) the expected behavior is clearly defined, (4) the logs tell the right story, and (5) the scenario contributes to the overall narrative arc.

- **Failure scenario assessment:** Verify that the demo includes or can handle failure scenarios gracefully. What happens if the presenter makes a typo? What if a service is temporarily unavailable? The demo should not be brittle.

---

## Skill Dependencies

| Skill | Depends On | Feeds Into |
|-------|-----------|------------|
| Security Audit | Architecture Review (trust boundaries) | Scorecard (Security Posture) |
| Architecture Review | Context Ingestion (all handoffs) | Scorecard (Architecture Integrity), Security Audit |
| Red Hat Alignment | Architecture Review (component stack) | Scorecard (Red Hat Alignment) |
| Scoring Methodology | All other skills (evidence) | Final Decision |
| Independent Verification | Tester handoff, test code | All scorecard dimensions |
| Code Review | Programmer handoff, source code | Scorecard (multiple dimensions) |
| Demo Evaluation | All handoffs, demo code, logs | Scorecard (Demo Completeness) |

## Skill Application Order

During a review, skills are applied in this sequence:

1. **Context Ingestion** (not a skill per se, but prerequisite for all skills)
2. **Independent Verification** -- spot-check before deep analysis
3. **Architecture Review** -- establishes trust boundary understanding
4. **Security Audit** -- uses trust boundaries from architecture review
5. **Red Hat Alignment** -- uses component inventory from architecture review
6. **Code Review** -- detailed code-level analysis
7. **Demo Evaluation** -- narrative and presentation readiness
8. **Scoring Methodology** -- synthesize all findings into rubric scores
