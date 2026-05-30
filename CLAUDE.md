# Semantic Redaction for Privacy-Preserving Semantic Routing

## Project Identity

This project demonstrates **privacy-preserving semantic routing** on Red Hat OpenShift. It adds a **sensitivity dimension** to the existing Semantic Claw Router's complexity-based classification, deploys Presidio + GLiNER redaction services and NeMo Guardrails as OpenShift-native microservices, and enforces egress policy so only sanitized traffic can reach SaaS model endpoints.

**Core thesis:** Semantic routing is safe only when the first trust decision happens inside the isolated environment. OpenShift becomes the enforcement boundary.

## Implementation Order

Agents execute in this sequence. Each phase MUST complete and hand off before the next begins.

```
Phase 1: Planner    → Task decomposition, acceptance criteria, risk register
Phase 2: Programmer → Code, configs, manifests, deployment
Phase 3: Tester     → Validation against acceptance criteria, demo scenarios
Phase 4: Reviewer   → Architecture audit, security review, scoring, approval/rejection
```

Any agent may escalate back to a previous agent. See `pipeline.md` for the full handoff protocol.

## Hard Constraints

1. **DO NOT** bring down or modify existing models. Qwen 3.6 in `homelab-maas` must remain running.
2. **DO NOT** modify the `semantic-claw-router` source code. Extend routing behavior via config updates only.
3. **DO NOT** modify `openai-proxy` or any service in `home-assistant` namespace.
4. All new services deploy to the `semantic-redacted` namespace.
5. Use existing Gemini API key from cluster secrets (copy or reference, do not regenerate).
6. All container images use UBI9 base images (Red Hat alignment).
7. Python 3.11+ for all services.
8. CPU-only deployments for new services (GPU is reserved for Qwen model serving).
9. No hardcoded secrets in any file. Use Kubernetes Secrets with env var references.
10. Every routing decision must produce an audit event (structured JSON log).

## Coding Standards

- **Language:** Python 3.11+
- **HTTP framework:** FastAPI + uvicorn
- **Config:** YAML with `${ENV_VAR}` expansion
- **Types:** Type hints on all public APIs
- **Logging:** Structured JSON (stdlib `logging` with JSON formatter)
- **Tests:** pytest, no mocks for integration tests
- **Containers:** Multi-stage Dockerfile, UBI9 base, non-root user
- **K8s manifests:** Plain YAML (no Helm for the demo), labeled with `app.kubernetes.io/*`

## Existing Infrastructure (Read-Only References)

| Component | Namespace | Endpoint | Purpose |
|-----------|-----------|----------|---------|
| Qwen 3.6 (Ollama) | homelab-maas | `ollama-qwen36.homelab-maas.svc:11434` | Local model (private lane) |
| Qwen 3.6 (llama.cpp) | homelab-maas | `llama-server-qwen36.homelab-maas.svc:8080` | Alt local model |
| Semantic Claw Router | homelab-maas | `semantic-claw-router.homelab-maas.svc:8080` | Request classifier + router |
| Gemini API | external | `generativelanguage.googleapis.com` | SaaS model (cloud lane) |
| OpenAI Proxy | home-assistant | `openai-proxy.home-assistant.svc:8005` | HA integration proxy |

## Key Files

- `research.md` — Background research and literature review
- `overview.md` — Project thesis, narrative, and architecture summary
- `pipeline.md` — Agent pipeline definition and handoff protocol
- `agents/*/AGENT.md` — Agent role definitions
- `agents/*/skills.md` — Agent capability inventories
- `docs/architecture.md` — Full system architecture
- `docs/sensitivity-model.md` — Sensitivity classification taxonomy
- `docs/demo-scenarios.md` — 6 end-to-end demo scenarios

## Agent Interaction Rules

1. Read your `AGENT.md` and `skills.md` before starting any work.
2. Read all previous agents' `handoff.md` files for context.
3. Check `pipeline-state.md` for current pipeline status.
4. Write your `handoff.md` when complete.
5. Update `pipeline-state.md` with your status.
6. If blocked, write an escalation in your `handoff.md` with target agent and specific ask.

## Testing Requirements

- Unit tests for redaction accuracy (>95% recall on built-in entity types)
- Unit tests for sensitivity classification (anchor-based scoring)
- Integration tests for all 6 demo scenarios
- Egress policy verification (blocked calls from non-gateway pods)
- Reviewer scorecard must achieve >= 0.80 weighted score
