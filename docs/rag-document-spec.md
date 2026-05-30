# Sensitive RAG Document Specification

## Purpose

The sensitive RAG document is a synthetic company document that serves as the anchor for demonstrating RAG context governance. It contains a realistic mix of financial data, employee information, project codenames, and internal metrics that would be catastrophic if leaked to a SaaS model provider.

The document is stored in the local Qdrant vector store with sensitivity metadata `NEVER_EGRESS`. When any query retrieves chunks from this document, the entire request inherits the NEVER_EGRESS sensitivity level through context inheritance.

## Document: Quarterly Business Review — Q3 2026

The synthetic document simulates an internal quarterly business review with these sections:

### Financial Summary
- Revenue figures ($4.2M, +12% QoQ)
- Net income ($890K)
- EBITDA margin (21.2%)
- Burn rate and runway projections
- Customer acquisition cost (CAC) and lifetime value (LTV)
- Revenue breakdown by segment

### Personnel Updates
- Employee names (synthetic but realistic)
- Compensation ranges
- Hiring pipeline status
- Performance review summaries
- Promotion and termination decisions
- Team size changes

### Project Status
- Internal project codenames (Project Phoenix, Operation Lighthouse, Initiative Trident)
- Milestone dates and delivery status
- Technical architecture decisions
- Infrastructure details (cluster names, namespaces, resource allocations)
- Vendor evaluations with pricing

### Customer Data
- Customer company names and account IDs
- Contract values and terms
- Usage metrics per customer
- Support escalation history
- Churn risk assessments

### Security and Compliance
- Audit findings
- Vulnerability remediation status
- Compliance certification progress
- Incident response summaries

## Document Content

```markdown
# Quarterly Business Review — Q3 2026
## Confidential — Internal Use Only
### Prepared by: CFO Office | Distribution: Executive Team

---

## Financial Performance

Q3 2026 closed with total revenue of $4,218,500, representing 12.3% quarter-over-quarter
growth from Q2's $3,756,200. Net income reached $891,450 (21.1% margin), up from $702,300
in Q2. EBITDA was $894,200 (21.2% margin).

Revenue by segment:
- Enterprise: $2,531,100 (60%) — driven by Acme Corp renewal ($890K ARR) and new
  TechVision Industries deal ($340K ARR)
- Mid-market: $1,265,550 (30%) — 8 new accounts, avg. $52K ARR
- SMB: $421,850 (10%) — self-serve growth continues

Customer acquisition cost (CAC) improved to $12,400 (from $15,200 in Q2) due to the
inbound marketing program led by Maria Santos on the growth team.

Cash position: $8.4M with 22 months runway at current burn rate ($382K/month).

Q4 pipeline: $1.8M in qualified opportunities. Forecast confidence: 72%.

---

## Personnel

### Headcount
Total: 47 employees (up from 42 in Q2)
- Engineering: 24 (+3 new hires)
- Sales: 8 (+1)
- Marketing: 5 (+1)
- G&A: 6 (unchanged)
- Executive: 4 (unchanged)

### Compensation Ranges (Annual)
- Senior Engineer: $165,000 — $195,000
- Staff Engineer: $195,000 — $230,000
- Engineering Manager: $180,000 — $215,000
- VP Engineering: $250,000 — $310,000 + equity

### Notable Personnel Actions
- Sarah Chen (Staff Engineer): Promoted to Tech Lead, Project Phoenix. New comp: $210,000 + 0.15% equity refresh.
- Marcus Johnson (Senior Engineer): Performance improvement plan initiated. Sprint commitment rate dropped from 87% to 54% over Q2-Q3. Peer feedback score: 2.8/5.0 (down from 4.2). Current comp: $165,000.
- David Park (Engineering Manager): Leading the Kubernetes migration for Operation Lighthouse. Team of 6. Annual performance review: Exceeds Expectations.
- Rachel Kim (Marketing Director): Hired Q3. Previously at Datadog. Base: $185,000 + 0.1% equity.
- James Wilson (Sales Rep): Closed Acme Corp renewal. Attainment: 142% of quota. Commission: $67,200.

### Departures
- Lisa Wang (Senior Engineer): Resigned effective 2026-09-15. Exit reason: competing offer from Anthropic ($245K base). Knowledge transfer in progress for the auth middleware service.

---

## Project Status

### Project Phoenix — AI-Powered Customer Analytics
- Status: On track (Green)
- Lead: Sarah Chen
- Team: 5 engineers
- Milestone: Beta launch 2026-11-01
- Infrastructure: Deployed on ironman.cjlabs.dev cluster, homelab-maas namespace
- Stack: Qwen 3.6 (local inference), Qdrant (vector store), FastAPI (services)
- Budget: $120K allocated, $78K spent
- Risk: GPU memory pressure on worker-01 during peak inference loads

### Operation Lighthouse — Platform Migration to OpenShift
- Status: At risk (Yellow)
- Lead: David Park
- Team: 6 engineers
- Milestone: Production cutover 2026-12-15
- Blocker: TLS certificate rotation automation not yet tested in DR environment
- Dependencies: Keycloak SSO integration (Sarah Chen's team)
- Budget: $250K allocated, $190K spent

### Initiative Trident — Enterprise SSO Consolidation
- Status: Planning (Blue)
- Lead: TBD (pending hire of Senior Security Engineer)
- Budget request: $180K for FY2027
- Justification: 3 customers (Acme Corp, TechVision, GlobalMfg) contractually require SOC 2 Type II by 2027-06

---

## Customer Data

### Top Accounts
| Customer | Account ID | ARR | Contract End | Health | Risk |
|----------|-----------|-----|-------------|--------|------|
| Acme Corp | CUST-001 | $890,000 | 2027-09-30 | Green | Low |
| TechVision Industries | CUST-017 | $340,000 | 2028-03-31 | Green | Low |
| GlobalMfg Inc | CUST-023 | $275,000 | 2027-06-30 | Yellow | Medium |
| DataFlow Systems | CUST-041 | $198,000 | 2027-01-31 | Red | High |

### Churn Risk: DataFlow Systems
- Contract renewal in 60 days
- Support ticket volume up 340% in Q3 (auth failures, API timeouts)
- Champion (VP Eng) left DataFlow in August
- Mitigation: David Park assigned as executive sponsor, weekly check-ins scheduled
- Contingency: If churned, $198K ARR impact (4.7% of total)

---

## Security & Compliance

### Audit Findings
- SOC 2 Type I: Passed with 2 observations
  - OBS-1: Database backup encryption key rotation exceeds 90-day policy (currently 120 days)
  - OBS-2: Three developer accounts lack MFA enforcement (resolved 2026-08-20)
- PCI DSS: Not applicable (no payment card data processed directly)
- GDPR: EU data processing agreement updated for Acme Corp EU subsidiary

### Vulnerability Status
- Critical: 0 open
- High: 2 open (CVE-2026-4421 in auth-middleware, patch scheduled 2026-10-05)
- Medium: 7 open
- Low: 23 open

### Incident Log
- 2026-07-14: API gateway outage (47 minutes). Root cause: expired TLS cert on ingress controller.
  Customer impact: Acme Corp, TechVision reported API errors. Post-mortem completed.
- 2026-08-22: Unauthorized access attempt on admin panel. Source: IP 45.33.x.x (Linode).
  Blocked by WAF. No data exposure. Reported to security team, IP blocklisted.
```

## Chunking Strategy

The document is split into chunks for Qdrant ingestion:

| Chunk ID | Section | Approx. Tokens | Sensitivity |
|----------|---------|----------------|-------------|
| chunk-01 | Financial Performance | ~250 | NEVER_EGRESS |
| chunk-02 | Revenue by Segment | ~150 | NEVER_EGRESS |
| chunk-03 | Cash Position + Q4 Pipeline | ~100 | NEVER_EGRESS |
| chunk-04 | Headcount Summary | ~100 | NEVER_EGRESS |
| chunk-05 | Compensation Ranges | ~100 | NEVER_EGRESS |
| chunk-06 | Personnel Actions (Sarah, Marcus) | ~200 | NEVER_EGRESS |
| chunk-07 | Personnel Actions (David, Rachel, James) | ~150 | NEVER_EGRESS |
| chunk-08 | Departures (Lisa Wang) | ~100 | NEVER_EGRESS |
| chunk-09 | Project Phoenix | ~200 | NEVER_EGRESS |
| chunk-10 | Operation Lighthouse | ~150 | NEVER_EGRESS |
| chunk-11 | Initiative Trident | ~100 | NEVER_EGRESS |
| chunk-12 | Customer Data Table | ~150 | NEVER_EGRESS |
| chunk-13 | Churn Risk: DataFlow | ~150 | NEVER_EGRESS |
| chunk-14 | Security Audit Findings | ~150 | NEVER_EGRESS |
| chunk-15 | Vulnerability + Incident Log | ~200 | NEVER_EGRESS |

All chunks are labeled `NEVER_EGRESS` because any fragment of this document contains business-critical information.

## Qdrant Collection Configuration

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

client = QdrantClient(url="http://qdrant.semantic-redacted.svc:6333")

client.create_collection(
    collection_name="sensitive_docs",
    vectors_config=VectorParams(
        size=384,  # all-MiniLM-L6-v2 embedding dimension
        distance=Distance.COSINE,
    ),
)

# Each point has:
# - id: sequential integer
# - vector: 384-dim embedding of the chunk text
# - payload: { text, source, section, sensitivity, chunk_id }
```

## Governance Rules

1. **NEVER_EGRESS chunks are never attached to SaaS-bound prompts.** The retrieval rail enforces this.
2. **Context inheritance:** If any retrieved chunk is CONFIDENTIAL or higher, the request sensitivity inherits the highest chunk sensitivity.
3. **No chunk summaries to SaaS:** Even summarized versions of NEVER_EGRESS chunks cannot be sent externally. The summary could still contain enough context for reconstruction.
4. **Local embedding only:** The chunk embeddings are computed locally (all-MiniLM-L6-v2). The raw text is never sent to an external embedding service.
5. **No export:** The Qdrant collection has no external backup path. Data stays in the PVC within the cluster.
