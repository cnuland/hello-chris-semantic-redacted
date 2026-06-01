# Sensitivity Classification Model

## Overview

The sensitivity classifier adds a second dimension to the Semantic Claw Router's existing complexity classification. While complexity determines how capable the model needs to be, sensitivity determines whether the request can leave the cluster.

## Sensitivity Levels

### Level 1: PUBLIC

Content that has no confidentiality requirements. Can be sent to any model, including SaaS, without modification.

**Characteristics:**
- General knowledge questions
- Publicly available information
- Open-source code discussions
- Academic or educational content
- Generic technical questions

**Routing action:** REDACT_THEN_SAAS (any complexity tier)

**Anchor prompts (25 total, sample shown):**
```jsonl
{"text": "What is the capital of France?", "label": "PUBLIC"}
{"text": "Explain how photosynthesis works", "label": "PUBLIC"}
{"text": "Write a Python function to sort a list", "label": "PUBLIC"}
{"text": "What are the differences between REST and GraphQL?", "label": "PUBLIC"}
{"text": "Explain the OWASP Top 10 web application security risks and how to mitigate them", "label": "PUBLIC"}
{"text": "How does Kubernetes role-based access control (RBAC) work conceptually?", "label": "PUBLIC"}
{"text": "Describe the Model-View-Controller design pattern and when to use it", "label": "PUBLIC"}
{"text": "How does API rate limiting work and what are common algorithms like token bucket?", "label": "PUBLIC"}
```

### Level 2: INTERNAL

Content that references internal systems, processes, or non-public project details. Can be sent to SaaS after redaction of identifying details.

**Characteristics:**
- Internal project names or codenames
- Cluster or infrastructure references
- Internal tooling or workflow questions
- Non-public architecture discussions
- Internal meeting summaries (non-sensitive topics)

**Routing action:** REDACT_THEN_SAAS (for COMPLEX/REASONING) or LOCAL_ONLY (for SIMPLE/MEDIUM)

**Anchor prompts (28 total, sample shown):**
```jsonl
{"text": "How do I deploy to the staging cluster on ironman.cjlabs.dev?", "label": "INTERNAL"}
{"text": "What namespace does the semantic router run in?", "label": "INTERNAL"}
{"text": "Check whether the ClusterRole binding for the CI service account has the right RBAC permissions", "label": "INTERNAL"}
{"text": "The Argo CD sync for the guardrails deployment is stuck, check the sync status", "label": "INTERNAL"}
{"text": "What alerting rules are configured in Prometheus for pod restart thresholds?", "label": "INTERNAL"}
{"text": "Review the CI/CD pipeline definition for building our redaction-service image", "label": "INTERNAL"}
{"text": "Check the BuildConfig for our guardrails-service container on the cluster", "label": "INTERNAL"}
```

### Level 3: CONFIDENTIAL

Content involving personnel matters, customer-specific data, or business-sensitive information. Must stay on local models regardless of complexity.

**Characteristics:**
- HR conversations (hiring, firing, performance reviews, compensation)
- Customer names and account details
- Contract terms and pricing
- Internal strategy discussions
- Employee personal information
- Vendor evaluations with proprietary terms

**Routing action:** LOCAL_ONLY (all complexity tiers)

**Anchor prompts (28 total, sample shown):**
```jsonl
{"text": "Draft a performance improvement plan for Sarah Chen in engineering", "label": "CONFIDENTIAL"}
{"text": "What is the salary range for senior engineers on the platform team?", "label": "CONFIDENTIAL"}
{"text": "Review the contract terms we offered Acme Corp for the enterprise deal", "label": "CONFIDENTIAL"}
{"text": "Compile the 360-degree peer review feedback for the department manager", "label": "CONFIDENTIAL"}
{"text": "What equity refresher grants were approved for the senior engineering team?", "label": "CONFIDENTIAL"}
{"text": "Summarize the results from the new hire background check process", "label": "CONFIDENTIAL"}
{"text": "What was the outcome of the executive compensation benchmarking analysis?", "label": "CONFIDENTIAL"}
```

### Level 4: REGULATED

Content subject to legal, compliance, or regulatory requirements (GDPR, HIPAA, SOX, PCI-DSS). Must stay on local models. May require additional audit logging.

**Characteristics:**
- Financial statements and earnings data
- Healthcare records or PII in regulated context
- Legal proceedings or discovery materials
- Compliance audit findings
- PCI cardholder data
- GDPR personal data processing records

**Routing action:** LOCAL_ONLY (all complexity tiers), enhanced audit logging

**Anchor prompts (25 total, sample shown):**
```jsonl
{"text": "Analyze our Q3 2026 earnings before the public filing deadline", "label": "REGULATED"}
{"text": "Review the patient records for clinical trial participant 7829", "label": "REGULATED"}
{"text": "Process the GDPR deletion request for user account EU-2024-8891", "label": "REGULATED"}
{"text": "Draft the SEC 10-K filing summary for fiscal year 2026", "label": "REGULATED"}
{"text": "Review the PHI minimum necessary access controls per HIPAA Security Rule requirements", "label": "REGULATED"}
{"text": "Audit the tokenization vault for PCI-DSS cardholder data environment compliance", "label": "REGULATED"}
{"text": "Prepare the SOX Section 302 management certification for quarterly financial reporting", "label": "REGULATED"}
```

### Level 5: NEVER_EGRESS

Content that must never leave the cluster under any circumstances. This includes RAG documents marked as never-egress, active security incidents, cryptographic material, and credentials.

**Characteristics:**
- RAG documents with NEVER_EGRESS metadata
- Active security incident discussions
- Cryptographic keys, tokens, or credentials
- Vulnerability reports before patching
- Penetration test results
- Source code with embedded secrets

**Routing action:** LOCAL_ONLY (all complexity tiers), alert on any egress attempt

**Anchor prompts (28 total, sample shown):**
```jsonl
{"text": "Here is the SSH private key for the production bastion host", "label": "NEVER_EGRESS"}
{"text": "The API key for our payment gateway is sk_live_EXAMPLE_KEY_00000000000000", "label": "NEVER_EGRESS"}
{"text": "We found a critical RCE vulnerability in the auth service — CVE pending", "label": "NEVER_EGRESS"}
{"text": "Review the incident response log for the data breach detected at 3am", "label": "NEVER_EGRESS"}
{"text": "Map the attack kill chain from the initial phishing email to lateral movement in our cluster", "label": "NEVER_EGRESS"}
{"text": "Document all indicators of compromise found during the supply chain incident investigation", "label": "NEVER_EGRESS"}
{"text": "The SIEM detected lateral movement from compromised hosts in the DMZ network", "label": "NEVER_EGRESS"}
```

## 2D Routing Matrix

The decision engine combines complexity and sensitivity into a single routing action:

```
                 PUBLIC       INTERNAL       CONFIDENTIAL    REGULATED    NEVER_EGRESS
  ┌──────────┬─────────────┬──────────────┬──────────────┬────────────┬──────────────┐
  │ SIMPLE   │ REDACT→SAAS │ LOCAL_ONLY   │ LOCAL_ONLY   │ LOCAL_ONLY │ LOCAL_ONLY   │
  ├──────────┼─────────────┼──────────────┼──────────────┼────────────┼──────────────┤
  │ MEDIUM   │ REDACT→SAAS │ REDACT→SAAS  │ LOCAL_ONLY   │ LOCAL_ONLY │ LOCAL_ONLY   │
  ├──────────┼─────────────┼──────────────┼──────────────┼────────────┼──────────────┤
  │ COMPLEX  │ REDACT→SAAS │ REDACT→SAAS  │ REDACT→SAAS  │ LOCAL_ONLY │ LOCAL_ONLY   │
  ├──────────┼─────────────┼──────────────┼──────────────┼────────────┼──────────────┤
  │ REASONING│ REDACT→SAAS │ REDACT→SAAS  │ LOCAL_ONLY   │ LOCAL_ONLY │ LOCAL_ONLY   │
  └──────────┴─────────────┴──────────────┴──────────────┴────────────┴──────────────┘
```

**Key decisions:**
- INTERNAL + SIMPLE stays local because redaction overhead isn't worth it for simple queries the local model handles fine
- CONFIDENTIAL + COMPLEX gets REDACT→SAAS because the complexity benefit of a frontier model outweighs the risk when properly redacted
- CONFIDENTIAL + REASONING stays local because reasoning traces are especially prone to leaking context (per Leaky Thoughts research)
- REGULATED always stays local — compliance risk outweighs any model quality benefit
- NEVER_EGRESS always stays local — this is an absolute policy, not a risk/reward tradeoff

## Classification Method

### Primary: Keyword + Pattern Detection

Fast-path signals for immediate classification:
- **PII patterns:** Email regex, SSN format, phone numbers, credit card numbers
- **Secret patterns:** API key prefixes (`sk_`, `ghp_`, `eyJ`), private key headers
- **HR keywords:** salary, termination, performance review, hiring, compensation
- **Financial keywords:** earnings, revenue, profit margin, SEC filing, quarterly report
- **Infrastructure patterns:** `.svc.cluster.local`, namespace names, pod names

### Secondary: Semantic Embedding Similarity

For ambiguous cases where keywords don't trigger:
- Fine-tuned sentence-transformers model ([cnuland/semantic-routing-sensitivity](https://huggingface.co/cnuland/semantic-routing-sensitivity)), based on all-MiniLM-L6-v2
- Cosine similarity against 134 curated anchor prompts (25-28 per level)
- Top-K=3 averaging per level
- Highest-scoring level wins
- Minimum confidence threshold: 0.6 (below this, default to INTERNAL)
- Achieves 100% accuracy on a 125-prompt test corpus spanning all 5 levels

### Tertiary: RAG Context Inheritance

If the request triggers RAG retrieval:
- Each document chunk in Qdrant has a `sensitivity` metadata field
- The retrieved chunks' sensitivity levels are checked
- The request inherits the HIGHEST sensitivity of any retrieved chunk
- This prevents a PUBLIC query from returning CONFIDENTIAL content to SaaS

## False Negative Strategy

Presidio's docs warn that automated detection is not guaranteed to catch everything. Our layered approach:

1. **Keyword signals** — Catch obvious patterns (high precision, moderate recall)
2. **Semantic embeddings** — Catch thematic similarity (moderate precision, high recall)
3. **Presidio recognizers** — Catch structured PII (high precision for known formats)
4. **GLiNER** — Catch zero-shot entities (project names, custom taxonomies)
5. **NeMo Egress Guard** — LLM-backed final verification of redaction completeness before content crosses the trust boundary (real NeMo Guardrails framework + Qwen)
6. **NeMo output rail** — Final catch for anything the SaaS model reconstructed
7. **Default to LOCAL_ONLY** — When uncertain, keep it local (fail-safe)

The design philosophy: false positives (over-classifying) route to local and cost nothing extra. False negatives (under-classifying) leak data. The system biases toward false positives.
