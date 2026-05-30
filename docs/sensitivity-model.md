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

**Routing action:** DIRECT_SAAS (any complexity tier)

**Anchor prompts:**
```jsonl
{"text": "What is the capital of France?", "label": "PUBLIC"}
{"text": "Explain how photosynthesis works", "label": "PUBLIC"}
{"text": "Write a Python function to sort a list", "label": "PUBLIC"}
{"text": "What are the differences between REST and GraphQL?", "label": "PUBLIC"}
{"text": "Summarize the history of the Roman Empire", "label": "PUBLIC"}
{"text": "How does TCP three-way handshake work?", "label": "PUBLIC"}
{"text": "Translate 'hello world' into Spanish", "label": "PUBLIC"}
{"text": "What is the time complexity of quicksort?", "label": "PUBLIC"}
{"text": "Explain the CAP theorem in distributed systems", "label": "PUBLIC"}
{"text": "Write a regex to validate email addresses", "label": "PUBLIC"}
{"text": "What are the SOLID principles in software engineering?", "label": "PUBLIC"}
{"text": "How do neural networks learn through backpropagation?", "label": "PUBLIC"}
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

**Anchor prompts:**
```jsonl
{"text": "How do I deploy to the staging cluster on ironman.cjlabs.dev?", "label": "INTERNAL"}
{"text": "What namespace does the semantic router run in?", "label": "INTERNAL"}
{"text": "Can you review the architecture of Project Phoenix?", "label": "INTERNAL"}
{"text": "How do I configure the internal CI pipeline for sprint deployments?", "label": "INTERNAL"}
{"text": "What's the current state of the homelab-maas namespace?", "label": "INTERNAL"}
{"text": "Explain the data flow in our openai-proxy service", "label": "INTERNAL"}
{"text": "How should we structure the Jira tickets for the Q3 migration?", "label": "INTERNAL"}
{"text": "What are the resource limits for pods on worker-01?", "label": "INTERNAL"}
{"text": "Debug the ollama-qwen36 deployment — it's not pulling the model", "label": "INTERNAL"}
{"text": "Review the network topology between home-assistant and homelab-maas namespaces", "label": "INTERNAL"}
{"text": "What does the internal SLA say about model response latency?", "label": "INTERNAL"}
{"text": "How do we handle secrets rotation for the Gemini API key?", "label": "INTERNAL"}
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

**Anchor prompts:**
```jsonl
{"text": "Draft a performance improvement plan for Sarah Chen in engineering", "label": "CONFIDENTIAL"}
{"text": "What is the salary range for senior engineers on the platform team?", "label": "CONFIDENTIAL"}
{"text": "Summarize the termination discussion from yesterday's HR meeting", "label": "CONFIDENTIAL"}
{"text": "Review the contract terms we offered Acme Corp for the enterprise deal", "label": "CONFIDENTIAL"}
{"text": "What were the key takeaways from the board strategy session?", "label": "CONFIDENTIAL"}
{"text": "Draft an email to John Martinez about his promotion timeline", "label": "CONFIDENTIAL"}
{"text": "Compare the vendor proposals from DataDog and New Relic with our pricing", "label": "CONFIDENTIAL"}
{"text": "What is customer ABC-1234's current usage and billing tier?", "label": "CONFIDENTIAL"}
{"text": "Prepare talking points for the Q3 headcount planning meeting", "label": "CONFIDENTIAL"}
{"text": "Review the non-compete clause in the acquisition term sheet", "label": "CONFIDENTIAL"}
{"text": "Draft a response to the employee complaint filed by Jane Doe", "label": "CONFIDENTIAL"}
{"text": "What's our customer churn rate by segment for the last quarter?", "label": "CONFIDENTIAL"}
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

**Anchor prompts:**
```jsonl
{"text": "Analyze our Q3 2026 earnings before the public filing deadline", "label": "REGULATED"}
{"text": "Review the patient records for clinical trial participant 7829", "label": "REGULATED"}
{"text": "What did the SOX audit finding say about our financial controls?", "label": "REGULATED"}
{"text": "Process the GDPR deletion request for user account EU-2024-8891", "label": "REGULATED"}
{"text": "Review the credit card transaction logs for PCI compliance", "label": "REGULATED"}
{"text": "Draft the SEC 10-K filing summary for fiscal year 2026", "label": "REGULATED"}
{"text": "What are the HIPAA implications of storing patient data in our cluster?", "label": "REGULATED"}
{"text": "Analyze the insider trading risk from the pre-earnings data leak", "label": "REGULATED"}
{"text": "Review the data processing agreement for GDPR Article 28 compliance", "label": "REGULATED"}
{"text": "What personal data do we hold on EU residents and what's our lawful basis?", "label": "REGULATED"}
{"text": "Prepare the quarterly financial consolidation report for the audit committee", "label": "REGULATED"}
{"text": "Review the sanctions screening results for the new vendor onboarding", "label": "REGULATED"}
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

**Anchor prompts:**
```jsonl
{"text": "Here is the SSH private key for the production bastion host", "label": "NEVER_EGRESS"}
{"text": "The API key for our payment gateway is sk_live_EXAMPLE_KEY_00000000000000", "label": "NEVER_EGRESS"}
{"text": "We found a critical RCE vulnerability in the auth service — CVE pending", "label": "NEVER_EGRESS"}
{"text": "Review the penetration test report from last week's red team exercise", "label": "NEVER_EGRESS"}
{"text": "The database root password is stored in vault at secret/prod/db-creds", "label": "NEVER_EGRESS"}
{"text": "Analyze this memory dump from the compromised worker node", "label": "NEVER_EGRESS"}
{"text": "The bearer token for the admin API is eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9", "label": "NEVER_EGRESS"}
{"text": "Review the incident response log for the data breach detected at 3am", "label": "NEVER_EGRESS"}
{"text": "What customer data was exposed in the S3 bucket misconfiguration?", "label": "NEVER_EGRESS"}
{"text": "Here is the TLS certificate chain for *.production.internal", "label": "NEVER_EGRESS"}
{"text": "Analyze the malware sample extracted from the compromised container", "label": "NEVER_EGRESS"}
{"text": "The OAuth client secret for our SSO integration is 7f3a9b2c-d4e5-6f78", "label": "NEVER_EGRESS"}
```

## 2D Routing Matrix

The decision engine combines complexity and sensitivity into a single routing action:

```
                 PUBLIC      INTERNAL       CONFIDENTIAL    REGULATED    NEVER_EGRESS
  ┌──────────┬────────────┬──────────────┬──────────────┬────────────┬──────────────┐
  │ SIMPLE   │ DIRECT_SAAS│ LOCAL_ONLY   │ LOCAL_ONLY   │ LOCAL_ONLY │ LOCAL_ONLY   │
  ├──────────┼────────────┼──────────────┼──────────────┼────────────┼──────────────┤
  │ MEDIUM   │ DIRECT_SAAS│ REDACT→SAAS  │ LOCAL_ONLY   │ LOCAL_ONLY │ LOCAL_ONLY   │
  ├──────────┼────────────┼──────────────┼──────────────┼────────────┼──────────────┤
  │ COMPLEX  │ DIRECT_SAAS│ REDACT→SAAS  │ REDACT→SAAS  │ LOCAL_ONLY │ LOCAL_ONLY   │
  ├──────────┼────────────┼──────────────┼──────────────┼────────────┼──────────────┤
  │ REASONING│ DIRECT_SAAS│ REDACT→SAAS  │ LOCAL_ONLY   │ LOCAL_ONLY │ LOCAL_ONLY   │
  └──────────┴────────────┴──────────────┴──────────────┴────────────┴──────────────┘
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
- Same `all-MiniLM-L6-v2` model used for complexity classification
- Cosine similarity against the 60 anchor prompts above
- Top-K=3 averaging per level
- Highest-scoring level wins
- Minimum confidence threshold: 0.6 (below this, default to INTERNAL)

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
