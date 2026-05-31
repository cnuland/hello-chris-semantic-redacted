# Privacy-Preserving Semantic Routing on OpenShift

Semantic routing is safe only when the first trust decision happens inside the isolated environment. This project adds a **sensitivity dimension** to LLM request routing, deploys Presidio-based redaction and guardrails as OpenShift-native microservices, and enforces egress policy so only sanitized traffic can reach SaaS model endpoints.

OpenShift becomes the enforcement boundary, not the application.

## The Problem

Existing semantic routers classify requests by **complexity** and route simple queries to cheap local models while sending complex ones to frontier SaaS models. But complexity is only one dimension. A structurally simple question like _"What is Sarah Chen's salary?"_ gets routed to a SaaS endpoint because it's easy to answer — and the trust boundary is already broken.

## The Solution

Add **sensitivity** as a second routing dimension, creating a 2D decision matrix:

```
                 PUBLIC     INTERNAL      CONFIDENTIAL   REGULATED   NEVER_EGRESS
  ┌──────────┬───────────┬─────────────┬──────────────┬───────────┬──────────────┐
  │ SIMPLE   │ SaaS      │ Local       │ Local        │ Local     │ Local        │
  │ MEDIUM   │ SaaS      │ Redact→SaaS │ Local        │ Local     │ Local        │
  │ COMPLEX  │ SaaS      │ Redact→SaaS │ Redact→SaaS  │ Local     │ Local        │
  │ REASONING│ SaaS      │ Redact→SaaS │ Local        │ Local     │ Local        │
  └──────────┴───────────┴─────────────┴──────────────┴───────────┴──────────────┘
```

Three routing outcomes:

- **Direct SaaS** — Public content, no redaction needed
- **Redact then SaaS** — Sensitive content is pseudonymized (names, emails, IPs become `<PERSON_1>`, `<EMAIL_1>`, etc.), sent to SaaS, then placeholders are restored inside the cluster
- **Local Only** — Content never leaves the cluster

## Architecture

```
                              OpenShift Cluster
  ┌──────────────────────────────────────────────────────────────────────┐
  │                                                                      │
  │   Request ──▶ Sensitivity    ──▶ Guardrails     ──▶ Redaction       │
  │               Classifier          (Input Rails)      (Presidio)      │
  │               (embedding +        (PII, secrets,     (pseudonymize   │
  │                keywords)           sensitivity)       + store map)    │
  │                    │                    │                   │         │
  │              ┌─────┴─────┐              │                   │         │
  │              │           │              │                   │         │
  │         LOCAL_ONLY  SaaS-eligible       │                   │         │
  │              │           │              │                   │         │
  │              ▼           ▼              ▼                   ▼         │
  │         ┌────────┐  Egress Guard ◄──────┘        ┌──────────────┐   │
  │         │ Qwen   │  (verify redaction)           │ NetworkPolicy│   │
  │         │ 3.6    │       │                       │ default-deny │   │
  │         │(local) │       ▼                       │ egress       │   │
  │         └────────┘  Gemini (SaaS) ──sanitized──▶ └──────────────┘   │
  │              │           │                                           │
  │              │      Output Rails                                     │
  │              │      (scan response)                                  │
  │              │           │                                           │
  │              │      Restore                                          │
  │              │      (replace placeholders)                           │
  │              │           │                                           │
  │              └─────┬─────┘                                           │
  │                    ▼                                                  │
  │                Response                                              │
  └──────────────────────────────────────────────────────────────────────┘
```

## Benchmark Results

Benchmarks run against live homelab cluster with 125 classification prompts, 42 redaction test cases, and 31 guardrails scenarios.

| Component | Metric | Score | Target |
|-----------|--------|-------|--------|
| Sensitivity Classification | Accuracy | 89.6% | > 85% |
| Sensitivity Classification | Macro F1 | 0.896 | > 0.85 |
| Sensitivity Classification | p50 Latency | 191ms | — |
| Redaction | Recall | 98.5% | > 95% |
| Redaction | Roundtrip Fidelity | 100% | 100% |
| Redaction | p50 Latency | 8ms | — |
| Guardrails | Accuracy | 100% | > 95% |
| Guardrails | p50 Latency | 13ms | — |
| NetworkPolicy | Enforcement | 5/5 | 5/5 |
| Fine-tuned vs Base | Accuracy Delta | +0.8% | > 0% |
| Fine-tuned vs Base | Latency Delta | -37% | — |

Full results: [`reports/benchmark-comparison-2026-05-30.md`](reports/benchmark-comparison-2026-05-30.md)

## Services

| Service | Port | Stack | Purpose |
|---------|------|-------|---------|
| **sensitivity-classifier** | 8002 | FastAPI, sentence-transformers | Two-phase classification: keyword/regex fast-path + embedding similarity against 100 curated anchors |
| **redaction-service** | 8000 | FastAPI, Presidio, spaCy | PII detection (23 recognizers, 6 custom), pseudonymization with reversible mappings, `/scan` endpoint for guardrails |
| **guardrails-service** | 8001 | FastAPI, httpx | Input/output rails: sensitivity check, secret detection, PII detection, retrieval filter, output scan, reconstruction detection |
| **nemo-egress-guard** | 8003 | FastAPI, NeMo Guardrails | LLM-backed final egress checkpoint using Colang policies and Qwen as the reasoning backend |
| **qdrant** | 6333 | Qdrant | Local vector store for sensitivity-labeled RAG documents |

All services deploy to the `semantic-redacted` namespace. All custom images use UBI9 base with Python 3.11, CPU-only.

## Sensitivity Levels

| Level | Description | Routing |
|-------|-------------|---------|
| **PUBLIC** | General knowledge, open-source, educational | Direct to SaaS |
| **INTERNAL** | Internal infrastructure, project names, cluster refs | Redact then SaaS (complex queries) |
| **CONFIDENTIAL** | HR, customer data, contracts, strategy | Local only |
| **REGULATED** | Financial filings, healthcare, GDPR, PCI, SOX | Local only |
| **NEVER_EGRESS** | Credentials, security incidents, vulnerability reports | Local only, alert on egress attempt |

## Egress Enforcement

NetworkPolicy is the enforcement layer, not the application:

```
default-deny-egress          All pods blocked from external traffic
allow-sanitized-egress       Only redaction-service (egress gateway) can reach SaaS
allow-internal               Intra-namespace + cross-namespace to Qwen
```

Verified by exec-ing into each pod and attempting to reach `generativelanguage.googleapis.com`:

| Pod | Egress | Verified |
|-----|--------|----------|
| guardrails-service | BLOCKED | Yes |
| sensitivity-classifier | BLOCKED | Yes |
| qdrant | BLOCKED | Yes |
| nemo-egress-guard | BLOCKED | Yes |
| redaction-service | ALLOWED | Yes |

## Fine-tuned Model

The sensitivity classifier uses a [fine-tuned sentence-transformers model](https://huggingface.co/cnuland/semantic-routing-sensitivity) trained via synthetic data generation (SDG) on the 100 anchor prompts across 5 sensitivity levels.

| Metric | Base (all-MiniLM-L6-v2) | Fine-tuned | Delta |
|--------|------------------------|------------|-------|
| Accuracy | 89.6% | 90.4% | +0.8% |
| Avg Latency | 10ms | 6ms | -37% |

The model is baked into the container image at build time (`TRANSFORMERS_OFFLINE=1`) — no runtime dependency on HuggingFace.

## Project Structure

```
src/
  sensitivity-classifier/    Embedding + keyword classifier (FastAPI)
  redaction-service/         Presidio + custom recognizers (FastAPI)
  guardrails-service/        Regex-based input/output rails (FastAPI)
  nemo-egress-guard/         NeMo Guardrails egress checkpoint (FastAPI)
  training/                  SDG pipeline, fine-tuning, evaluation
  demo/                      End-to-end demo runner (6 scenarios)

manifests/openshift/         Kubernetes manifests (plain YAML)
  network-policy/            Default-deny egress + allow rules
  */deployment.yaml          Per-service deployments
  */service.yaml             ClusterIP services
  rbac/                      ServiceAccounts

data/
  sensitivity-anchors/       100 curated anchor prompts (JSONL)
  test-prompts/              125 classification test prompts (25/level)
  benchmark-corpus/          Redaction, guardrails, egress test cases

benchmarks/
  run_benchmarks.py          7-category benchmark suite
  port-forward.sh            Port-forward script for local testing

reports/                     Benchmark baselines and comparisons
tests/                       pytest: unit + integration tests
docs/                        Architecture, deployment, sensitivity model
```

## Running Benchmarks

Prerequisites: `oc` CLI authenticated to the cluster, Python 3.11+.

```bash
# Port-forward all services
bash benchmarks/port-forward.sh

# Run the full benchmark suite
python3 benchmarks/run_benchmarks.py --all --warmup 3 --runs 3

# Run a single category
python3 benchmarks/run_benchmarks.py --category classification
python3 benchmarks/run_benchmarks.py --category redaction
python3 benchmarks/run_benchmarks.py --category guardrails
python3 benchmarks/run_benchmarks.py --category security
```

Results are written to `results/benchmark-results.json` (machine-readable) and `results/benchmark-report.md` (publication-ready).

## Deployment

```bash
# Create namespace and network policies first
oc apply -f manifests/openshift/namespace.yaml
oc apply -f manifests/openshift/network-policy/
oc apply -f manifests/openshift/rbac/

# Deploy services (order matters)
oc apply -f manifests/openshift/rag-store/
oc apply -f manifests/openshift/redaction-service/
oc apply -f manifests/openshift/guardrails-service/
oc apply -f manifests/openshift/sensitivity-classifier/
oc apply -f manifests/openshift/nemo-egress-guard/

# Build images from source
oc start-build redaction-service --from-dir=src/redaction-service -n semantic-redacted --follow
oc start-build guardrails-service --from-dir=src/guardrails-service -n semantic-redacted --follow
oc start-build sensitivity-classifier --from-dir=src/sensitivity-classifier -n semantic-redacted --follow
```

See [`docs/deployment.md`](docs/deployment.md) for the full deployment guide.

## Key Design Decisions

**Fail-safe routing.** When any service is unavailable or classification confidence is low, the system defaults to LOCAL_ONLY. False positives (over-classifying) route to the local model and cost nothing. False negatives (under-classifying) leak data. The system biases toward false positives.

**Defense in depth.** Application-level guards (classifier, redaction, guardrails) are backed by platform-level enforcement (NetworkPolicy). Even if every application check fails, NetworkPolicy prevents unauthorized egress.

**Composable open source.** Every component is independently replaceable. Presidio can swap for a different redactor. The guardrails engine can swap for Guardrails AI or a custom solution. The architecture doesn't depend on any single vendor.

**No secrets in code.** API keys are Kubernetes Secrets referenced via environment variables. Pseudonym mappings are in-memory and request-scoped — never persisted to disk.

## Related Projects

| Project | Relationship |
|---------|-------------|
| [semantic-claw-router](https://github.com/cnuland/semantic-claw-router) | Base routing infrastructure — complexity classification, provider integrations |
| [hello-chris-semantic-rlaif](https://github.com/cnuland/hello-chris-semantic-rlaif) | Fine-tuned embedding model via SDG pipeline, ASDLC agent structure |
| [hello-chris-ai-homelab](https://github.com/cnuland/hello-chris-ai-homelab) | OpenShift cluster, Qwen model serving, network infrastructure |

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — Full system architecture and data flow
- [`docs/sensitivity-model.md`](docs/sensitivity-model.md) — Sensitivity classification taxonomy and anchor prompts
- [`docs/redaction-pipeline.md`](docs/redaction-pipeline.md) — Presidio pipeline, custom recognizers, pseudonymization
- [`docs/egress-policy.md`](docs/egress-policy.md) — NetworkPolicy design and enforcement
- [`docs/guardrails-config.md`](docs/guardrails-config.md) — Rail definitions and configuration
- [`docs/demo-scenarios.md`](docs/demo-scenarios.md) — 6 end-to-end demo scenarios
- [`docs/deployment.md`](docs/deployment.md) — Deployment guide and prerequisites

## License

This project is provided as a reference implementation for privacy-preserving AI routing patterns on OpenShift.
