# NeMo Guardrails Configuration

## Overview

NeMo Guardrails (Apache 2.0, NVIDIA) provides programmable safety rails around LLM interactions. In this architecture, it serves three distinct roles:

1. **Input Rail** — Validates requests before the routing decision allows SaaS egress
2. **Retrieval Rail** — Filters RAG context by sensitivity before it's attached to prompts
3. **Output Rail** — Scans SaaS responses for reconstructed or hallucinated sensitive content

NeMo Guardrails runs as a standalone FastAPI service in the `semantic-redacted` namespace, using local Qwen 3.6 for any LLM-based rail evaluation (no external calls for guard logic).

## Architecture

```
                        ┌─────────────────────────────┐
                        │    NeMo Guardrails Service   │
                        │                             │
  Request ──▶ POST /guard/input ──▶ Input Rails       │
                        │         ├─ PII Check        │
                        │         ├─ Secret Detection │
                        │         ├─ Topic Control    │
                        │         └─ Jailbreak Check  │
                        │                             │
  RAG Chunks ──▶ POST /guard/retrieval ──▶ Retrieval  │
                        │         └─ Sensitivity      │
                        │            Filter           │
                        │                             │
  Response ──▶ POST /guard/output ──▶ Output Rails    │
                        │         ├─ PII Scan         │
                        │         ├─ Secret Scan      │
                        │         └─ Reconstruction   │
                        │            Detection        │
                        │                             │
                        │  LLM Backend: Qwen 3.6      │
                        │  (local, no SaaS calls)     │
                        └─────────────────────────────┘
```

## API Contract

### POST /guard/input

Evaluate input rails against an incoming request.

**Request:**
```json
{
  "messages": [
    {"role": "user", "content": "What is Sarah Chen's current salary?"}
  ],
  "sensitivity_level": "CONFIDENTIAL",
  "intended_route": "gemini-3.1-pro-preview"
}
```

**Response:**
```json
{
  "allowed": false,
  "action": "BLOCK_SAAS",
  "reason": "Content classified CONFIDENTIAL — cannot route to SaaS model",
  "rails_triggered": ["sensitivity_check"],
  "suggested_route": "qwen3.6-35b-a3b"
}
```

### POST /guard/retrieval

Filter RAG chunks before they're attached to a SaaS-bound prompt.

**Request:**
```json
{
  "chunks": [
    {"text": "Q3 revenue was $4.2M...", "metadata": {"sensitivity": "REGULATED"}},
    {"text": "The team uses Kubernetes...", "metadata": {"sensitivity": "PUBLIC"}}
  ],
  "intended_route": "gemini-3.1-flash-preview"
}
```

**Response:**
```json
{
  "filtered_chunks": [
    {"text": "The team uses Kubernetes...", "metadata": {"sensitivity": "PUBLIC"}}
  ],
  "removed_count": 1,
  "removal_reasons": [
    {"chunk_index": 0, "reason": "REGULATED content cannot be attached to SaaS-bound prompt"}
  ]
}
```

### POST /guard/output

Scan SaaS model response before placeholder restoration.

**Request:**
```json
{
  "response_text": "Based on the analysis of <PERSON_1>'s metrics, I recommend...",
  "original_sensitivity": "INTERNAL",
  "model_source": "gemini-3.1-pro-preview"
}
```

**Response:**
```json
{
  "clean": true,
  "findings": [],
  "action": "ALLOW"
}
```

### GET /health

```json
{
  "status": "healthy",
  "rails_loaded": ["input_sensitivity", "input_pii", "input_secrets", "input_jailbreak", "retrieval_filter", "output_scan"],
  "llm_backend": "qwen3.6-35b-a3b",
  "llm_reachable": true
}
```

## NeMo Configuration

### config.yml

```yaml
models:
  - type: main
    engine: openai
    model: qwen3.6-35b-a3b
    parameters:
      base_url: "http://ollama-qwen36.homelab-maas.svc.cluster.local:11434/v1"
      api_key: "not-needed"

rails:
  input:
    flows:
      - sensitivity check
      - pii detection
      - secret detection
      - jailbreak detection

  retrieval:
    flows:
      - sensitivity filter

  output:
    flows:
      - output pii scan
      - output secret scan
      - reconstruction detection

instructions:
  - type: general
    content: |
      You are a security guardrail evaluator. Your job is to determine whether
      content is safe to send to an external AI service. You must be conservative:
      if in doubt, block the content.

      Sensitive content includes: personal information (names, emails, phone numbers,
      SSNs, addresses), financial data (salaries, revenue, earnings), HR matters
      (performance reviews, terminations, hiring decisions), credentials (API keys,
      passwords, tokens), and internal infrastructure details (cluster names,
      namespace names, internal URLs).
```

### Colang Rail Definitions

#### Input Rail: Sensitivity Check (input.co)

```colang
define flow sensitivity check
  """Check if the request sensitivity level allows SaaS routing."""

  $sensitivity = $context.sensitivity_level

  if $sensitivity in ["CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"]
    bot refuse saas routing
    stop

define bot refuse saas routing
  "Content classified as {{$context.sensitivity_level}} — cannot route to external SaaS model. Routing to local model."
```

#### Input Rail: PII Detection (input.co)

```colang
define flow pii detection
  """Detect PII in user input that hasn't been caught by the classifier."""

  $user_message = $last_user_message

  $has_pii = execute check_pii(text=$user_message)

  if $has_pii
    $pii_types = execute get_pii_types(text=$user_message)
    bot warn pii detected
    # Don't block — let the redaction service handle it
    # But flag it for the routing decision

define bot warn pii detected
  "PII detected in input: {{$pii_types}}. Flagging for redaction before any SaaS routing."
```

#### Input Rail: Secret Detection (input.co)

```colang
define flow secret detection
  """Detect secrets, API keys, tokens, and credentials."""

  $user_message = $last_user_message

  $has_secrets = execute check_secrets(text=$user_message)

  if $has_secrets
    bot block secret content
    stop

define bot block secret content
  "Credential or secret material detected. This content MUST NOT leave the cluster. Routing to local model only."
```

#### Input Rail: Jailbreak Detection (input.co)

```colang
define flow jailbreak detection
  """Detect prompt injection and jailbreak attempts."""

  $user_message = $last_user_message

  $is_jailbreak = execute check_jailbreak(text=$user_message)

  if $is_jailbreak
    bot refuse jailbreak
    stop

define bot refuse jailbreak
  "Potential prompt injection detected. Request blocked."
```

#### Retrieval Rail: Sensitivity Filter (retrieval.co)

```colang
define flow sensitivity filter
  """Filter RAG chunks by sensitivity before attaching to SaaS-bound prompts."""

  $intended_route = $context.intended_route
  $is_saas = execute is_saas_model(model=$intended_route)

  if $is_saas
    $chunks = $context.relevant_chunks
    $filtered = execute filter_sensitive_chunks(chunks=$chunks)
    $context.relevant_chunks = $filtered
```

#### Output Rail: Response Scanning (output.co)

```colang
define flow output pii scan
  """Scan SaaS model response for PII before returning to user."""

  $bot_message = $last_bot_message

  $has_pii = execute check_pii(text=$bot_message)

  if $has_pii
    $bot_message = execute redact_output_pii(text=$bot_message)

define flow output secret scan
  """Scan for secrets in model output."""

  $bot_message = $last_bot_message

  $has_secrets = execute check_secrets(text=$bot_message)

  if $has_secrets
    bot warn output secrets
    # Replace the response with a safe version
    $bot_message = "[Response contained credential material and was blocked]"

define flow reconstruction detection
  """Detect if the SaaS model reconstructed redacted entities."""

  $bot_message = $last_bot_message

  $has_reconstruction = execute check_reconstruction(
    text=$bot_message,
    original_entities=$context.redacted_entities
  )

  if $has_reconstruction
    bot warn reconstruction
    $bot_message = execute re_redact_output(text=$bot_message)

define bot warn output secrets
  "The model's response contained credential material and was blocked for security."

define bot warn reconstruction
  "The model attempted to reconstruct redacted entities. Re-redacting the response."
```

## Custom Actions

NeMo Guardrails actions are Python functions registered with the guardrails runtime:

```python
from nemoguardrails.actions import action

@action()
async def check_pii(text: str) -> bool:
    """Call the redaction service's /scan endpoint."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://redaction-service.semantic-redacted.svc:8000/scan",
            json={"text": text, "check_types": ["PII"]},
        )
        return not resp.json()["clean"]

@action()
async def check_secrets(text: str) -> bool:
    """Detect API keys, tokens, private keys, and credentials."""
    secret_patterns = [
        r"(?:sk|pk)[-_](?:live|test)[-_]\w{20,}",     # Stripe-style
        r"ghp_\w{36}",                                   # GitHub PAT
        r"eyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+",       # JWT
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",      # PEM keys
        r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+",  # Generic
    ]
    import re
    return any(re.search(p, text, re.IGNORECASE) for p in secret_patterns)

@action()
async def filter_sensitive_chunks(chunks: list[dict]) -> list[dict]:
    """Remove chunks with sensitivity >= CONFIDENTIAL from SaaS-bound prompts."""
    blocked_levels = {"CONFIDENTIAL", "REGULATED", "NEVER_EGRESS"}
    return [c for c in chunks if c.get("metadata", {}).get("sensitivity", "PUBLIC") not in blocked_levels]

@action()
async def is_saas_model(model: str) -> bool:
    """Check if the target model is a SaaS endpoint."""
    saas_prefixes = ["gemini", "gpt", "claude", "openai"]
    return any(model.lower().startswith(p) for p in saas_prefixes)
```

## Container Specification

```dockerfile
FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0
RUN pip install --no-cache-dir \
    nemoguardrails \
    fastapi uvicorn \
    httpx pyyaml

USER 1001
COPY config/ /app/config/
COPY server.py actions.py /app/
WORKDIR /app

EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
```

## LLM Backend for Rail Evaluation

NeMo Guardrails uses an LLM to evaluate complex rails (jailbreak detection, reconstruction detection). This LLM MUST be local:

- **Model:** Qwen 3.6 via Ollama
- **Endpoint:** `http://ollama-qwen36.homelab-maas.svc.cluster.local:11434/v1`
- **Why local:** If the guard LLM is a SaaS model, the content has already leaked before the guard decision is made. This is the exact problem the project solves.

## NeMo Egress Guard (Real NeMo Guardrails)

In addition to the regex-based guardrails-service above, the architecture includes a dedicated **NeMo Egress Guard** service that uses the actual `nemoguardrails` Python package. This is the **final egress checkpoint** — it evaluates redacted content just before it leaves the cluster.

### Distinction from Guardrails Service

| Aspect | Guardrails Service (port 8001) | NeMo Egress Guard (port 8003) |
|--------|-------------------------------|-------------------------------|
| Framework | Custom regex rails | Real `nemoguardrails` package |
| LLM calls | None (pure pattern matching) | Qwen 3.6 via llama-server |
| Role | Input/output/retrieval pre-screening | Final egress verification |
| Position in flow | Before redaction (input) and after SaaS response (output) | After redaction, before SaaS call |
| Latency | <15ms | 300-900ms (LLM-backed) |

### API Contract

**POST /guard/egress**

Evaluates redacted text for egress safety.

```json
// Request
{
  "redacted_text": "Analyze the architecture of <PROJECT_1> deployed on <CLUSTER_1>...",
  "sensitivity_level": "INTERNAL",
  "entity_types_redacted": ["PROJECT", "CLUSTER_NAME", "PERSON", "EMAIL_ADDRESS"],
  "mapping_id": "abc123"
}

// Response (approved)
{
  "approved": true,
  "action": "ALLOW",
  "reason": "All egress rails passed",
  "rails_triggered": [],
  "latency_ms": 12.3
}

// Response (blocked)
{
  "approved": false,
  "action": "BLOCK",
  "reason": "Residual PII detected: EMAIL",
  "rails_triggered": ["egress_pii_verification"],
  "latency_ms": 8.7
}
```

### Colang Flows

Four egress-specific flows defined in `config/egress.co`:

1. **egress pii verification** — Calls redaction-service `/scan` to verify no PII remains
2. **egress secret scan** — Regex check for API keys, JWTs, PEM keys
3. **egress sensitivity check** — Defense-in-depth block for NEVER_EGRESS/REGULATED
4. **egress placeholder integrity** — Verifies `<TYPE_N>` placeholder format consistency

### Fail-Safe Behavior

If the egress guard is unreachable, the demo runner **blocks egress** — content does NOT proceed to SaaS. This is the correct fail-safe: uncertain conditions default to LOCAL_ONLY.

## Performance Budget

The guardrails service adds latency to the REDACT_THEN_SAAS path only:

| Rail | Expected Latency | Notes |
|------|-----------------|-------|
| Input sensitivity check | <1ms | Simple level comparison |
| Input PII detection | 5-15ms | Calls redaction service /scan |
| Input secret detection | <2ms | Regex matching |
| Input jailbreak detection | 200-500ms | LLM-based (Qwen) |
| Retrieval sensitivity filter | <1ms | Metadata filtering |
| Output PII scan | 5-15ms | Calls redaction service /scan |
| Output secret scan | <2ms | Regex matching |
| Output reconstruction detection | 200-500ms | LLM-based (Qwen) |

Total guardrails overhead: 400ms-1s (dominated by LLM-based jailbreak and reconstruction checks).

For the demo, this is acceptable. In production, the LLM-based rails could be replaced with fine-tuned classifiers (like Athena's mmBERT jailbreak detector) for sub-50ms latency.
