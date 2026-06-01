# Semantic Redaction: Data Isolation for SaaS-Routed LLM Workloads

An experiment in keeping sensitive data inside a Kubernetes cluster while still using external SaaS models for the queries that need them.

## The Problem

Semantic routers classify LLM requests by **complexity** — simple queries go to a cheap local model, complex ones go to a frontier SaaS model. But complexity alone isn't enough. A structurally simple question like _"What is Sarah Chen's salary?"_ gets routed to a SaaS endpoint because it's easy to answer — and the data is already gone.

## What This Does

This project adds **sensitivity classification** alongside complexity routing. Every request is scored against five sensitivity levels before a routing decision is made. Depending on the level, the system either:

- **Redacts and routes to SaaS** — names, emails, IPs, credentials become pseudonymized placeholders (`<PERSON_1>`, `<EMAIL_1>`), the sanitized prompt goes to SaaS, and placeholders are restored when the response comes back inside the cluster
- **Keeps it local** — the request never leaves the cluster

Nothing goes to SaaS without redaction. Even public content runs through the pipeline — the cost is a few milliseconds of scanning, and the payoff is that no data classification mistake can leak sensitive content.

```
              PUBLIC       INTERNAL      CONFIDENTIAL   REGULATED   NEVER_EGRESS
┌──────────┬────────────┬─────────────┬──────────────┬───────────┬──────────────┐
│ SIMPLE   │ Redact→SaaS│ Local       │ Local        │ Local     │ Local        │
│ MEDIUM   │ Redact→SaaS│ Redact→SaaS │ Local        │ Local     │ Local        │
│ COMPLEX  │ Redact→SaaS│ Redact→SaaS │ Redact→SaaS  │ Local     │ Local        │
│ REASONING│ Redact→SaaS│ Redact→SaaS │ Local        │ Local     │ Local        │
└──────────┴────────────┴─────────────┴──────────────┴───────────┴──────────────┘
```

The redaction path is where it gets interesting — you get the capability of a frontier model without exposing the underlying data. Guardrails validate both sides of the exchange: input rails catch secrets and PII before egress, output rails scan the SaaS response for reconstruction attempts (the model trying to guess what was behind a placeholder).

## How It Works

```
                                Kubernetes Cluster
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
│         │ Local  │  (verify redaction)           │ NetworkPolicy│   │
│         │ Model  │       │                       │ default-deny │   │
│         │        │       ▼                       │ egress       │   │
│         └────────┘  SaaS Model ───sanitized───▶  └──────────────┘   │
│              │           │                                           │
│              │      Output Rails                                     │
│              │      (scan response,                                  │
│              │       detect reconstruction)                          │
│              │           │                                           │
│              │      Restore                                          │
│              │      (replace placeholders)                           │
│              │           │                                           │
│              └─────┬─────┘                                           │
│                    ▼                                                  │
│                Response                                              │
└──────────────────────────────────────────────────────────────────────┘
```

NetworkPolicy is the real enforcement layer. The cluster runs a default-deny egress policy — only one pod (the redaction service, acting as an egress gateway) can reach external endpoints. Even if every application-level check fails, the network won't let unredacted data out.

## Benchmark Results

Benchmarks run against a live homelab cluster (125 classification prompts, 42 redaction test cases, 31 guardrails scenarios):

| Component | Metric | Score | Target |
|-----------|--------|-------|--------|
| Sensitivity Classification | Accuracy | 100% | > 85% |
| Sensitivity Classification | Macro F1 | 1.000 | > 0.85 |
| Redaction | Recall | 98.5% | > 95% |
| Redaction | Roundtrip Fidelity | 100% | 100% |
| Guardrails | Accuracy | 100% | > 95% |
| NetworkPolicy Enforcement | Pass Rate | 5/5 | 5/5 |
| Fine-tuned vs Base Model | Accuracy | +4.8% | > 0% |

Full analysis: [`reports/benchmark-comparison-2026-05-30.md`](reports/benchmark-comparison-2026-05-30.md)

## Components

| Service | What it does |
|---------|-------------|
| **sensitivity-classifier** | Two-phase classification: keyword/regex fast-path, then embedding similarity against 134 curated anchors. Uses a [fine-tuned sentence-transformers model](https://huggingface.co/cnuland/semantic-routing-sensitivity) that achieves 100% accuracy on the 125-prompt test corpus. |
| **redaction-service** | Presidio + spaCy + GLiNER for PII detection (23 recognizers, 6 custom). Pseudonymizes entities with reversible mappings so responses can be restored. Also serves as the egress gateway — the only pod allowed external access. |
| **guardrails-service** | Input/output rails: secret detection, PII scanning, sensitivity enforcement, retrieval filtering, output scanning, and reconstruction detection. |
| **nemo-egress-guard** | Final checkpoint before SaaS egress. Uses Colang policies with a local LLM backend to reason about whether the redacted payload is safe to send. |
| **qdrant** | Vector store for sensitivity-labeled RAG documents. Chunks inherit sensitivity metadata so the retrieval filter can strip classified content from SaaS-bound prompts. |

## Egress Verification

Every pod was tested by exec-ing into the container and attempting to reach an external API:

| Pod | Can reach external APIs? |
|-----|--------------------------|
| guardrails-service | No |
| sensitivity-classifier | No |
| qdrant | No |
| nemo-egress-guard | No |
| redaction-service (egress gateway) | Yes |

## Running It

Requires a Kubernetes cluster with the services deployed, plus `kubectl`/`oc` CLI access.

```bash
# Port-forward all services for local testing
bash benchmarks/port-forward.sh

# Run benchmarks
python3 benchmarks/run_benchmarks.py --all --warmup 3 --runs 3

# Or run individual categories
python3 benchmarks/run_benchmarks.py --category classification
python3 benchmarks/run_benchmarks.py --category redaction
python3 benchmarks/run_benchmarks.py --category guardrails
python3 benchmarks/run_benchmarks.py --category security
```

Deployment guide: [`docs/deployment.md`](docs/deployment.md)

## Project Structure

```
src/
  sensitivity-classifier/    Embedding + keyword classifier
  redaction-service/         Presidio + custom recognizers
  guardrails-service/        Input/output rails
  nemo-egress-guard/         Colang-based egress checkpoint
  training/                  SDG pipeline, fine-tuning, evaluation
  demo/                      End-to-end demo runner

manifests/openshift/         Kubernetes manifests (plain YAML)
  network-policy/            Default-deny egress + allow rules

data/
  sensitivity-anchors/       134 curated anchor prompts
  test-prompts/              125 classification test prompts
  benchmark-corpus/          Redaction, guardrails, egress test cases

benchmarks/                  Benchmark runner + port-forward script
reports/                     Baseline and comparison results
tests/                       pytest unit + integration tests
docs/                        Architecture, deployment, sensitivity model
```

## Design Choices

**Fail-safe to local.** If any service is down or classification confidence is low, the request stays local. Over-classifying wastes some compute on the local model. Under-classifying leaks data. The system biases toward keeping things inside the cluster.

**Network policy as backstop.** Application-level guards are defense-in-depth, but the actual enforcement is at the network layer. The system doesn't trust itself — it trusts the platform.

**Redact everything, always.** Even public content goes through the redaction pipeline before hitting SaaS. The overhead is negligible (8ms p50), and it eliminates the risk of a misclassified prompt leaking data. There is no "direct to SaaS" path.

**Reversible pseudonymization.** Redaction isn't lossy. Entities get deterministic placeholders, mappings are held in-memory for the request lifetime, and the original values are restored after the SaaS response comes back. The SaaS model sees `<PERSON_1> requested a review of <PROJECT_1>` and responds coherently — the user gets back the real names.

**Everything is swappable.** Presidio can be replaced with a different redaction engine. The guardrails layer can swap for Guardrails AI or anything else. The classifier model can be retrained. No vendor lock-in beyond Kubernetes itself.

## Related Projects

- [semantic-claw-router](https://github.com/cnuland/semantic-claw-router) — The base complexity router this project extends
- [hello-chris-semantic-rlaif](https://github.com/cnuland/hello-chris-semantic-rlaif) — Fine-tuned embedding model and the SDG pipeline that produced it
- [hello-chris-ai-homelab](https://github.com/cnuland/hello-chris-ai-homelab) — The homelab cluster running all of this

## Docs

- [Architecture](docs/architecture.md)
- [Sensitivity Model](docs/sensitivity-model.md)
- [Redaction Pipeline](docs/redaction-pipeline.md)
- [Egress Policy](docs/egress-policy.md)
- [Guardrails](docs/guardrails-config.md)
- [Demo Scenarios](docs/demo-scenarios.md)
- [Deployment](docs/deployment.md)
