# Redaction Pipeline: Presidio + GLiNER Service

## Overview

The redaction service is a FastAPI microservice that wraps Microsoft Presidio (MIT license) and GLiNER (Apache 2.0) to detect and pseudonymize sensitive spans in LLM request content before it crosses the trust boundary to SaaS models.

## API Contract

### POST /redact

Detect sensitive spans and replace with deterministic placeholders.

**Request:**
```json
{
  "text": "Please review Sarah Chen's Q3 performance and her salary of $185,000. She works on Project Phoenix in the ironman cluster.",
  "sensitivity_level": "CONFIDENTIAL",
  "entities_to_detect": ["PERSON", "MONEY", "PROJECT", "CLUSTER"],
  "language": "en"
}
```

**Response:**
```json
{
  "redacted_text": "Please review <PERSON_1>'s Q3 performance and her salary of <MONEY_1>. She works on <PROJECT_1> in the <CLUSTER_1> cluster.",
  "mapping_id": "m-7f3a9b2c",
  "entities": [
    {"type": "PERSON", "original": "Sarah Chen", "placeholder": "<PERSON_1>", "start": 14, "end": 24, "score": 0.95},
    {"type": "MONEY", "original": "$185,000", "placeholder": "<MONEY_1>", "start": 56, "end": 64, "score": 0.90},
    {"type": "PROJECT", "original": "Project Phoenix", "placeholder": "<PROJECT_1>", "start": 80, "end": 95, "score": 0.85},
    {"type": "CLUSTER", "original": "ironman", "placeholder": "<CLUSTER_1>", "start": 103, "end": 110, "score": 0.80}
  ],
  "entity_count": 4,
  "redaction_applied": true
}
```

### POST /restore

Replace placeholders in a response with original values. Only callable from inside the cluster.

**Request:**
```json
{
  "text": "Based on <PERSON_1>'s performance metrics, I recommend a 10% raise from <MONEY_1>.",
  "mapping_id": "m-7f3a9b2c"
}
```

**Response:**
```json
{
  "restored_text": "Based on Sarah Chen's performance metrics, I recommend a 10% raise from $185,000.",
  "placeholders_restored": 2,
  "mapping_deleted": true
}
```

### POST /scan

Scan text for residual sensitive content (used as output rail).

**Request:**
```json
{
  "text": "The analysis shows strong Q3 performance across the engineering team.",
  "check_types": ["PII", "SECRETS", "INTERNAL_REFS"]
}
```

**Response:**
```json
{
  "clean": true,
  "findings": [],
  "scan_timestamp": "2026-05-24T14:00:00Z"
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "presidio_version": "2.2.x",
  "recognizers_loaded": 18,
  "custom_recognizers": 6,
  "gliner_model_loaded": true
}
```

## Recognizer Stack

### Built-in Presidio Recognizers

| Entity Type | Recognizer | Examples |
|-------------|-----------|----------|
| PERSON | SpaCy NER | "Sarah Chen", "John Martinez" |
| EMAIL_ADDRESS | Pattern | "sarah@company.com" |
| PHONE_NUMBER | Pattern + Libphonenumber | "+1-555-0123" |
| US_SSN | Pattern | "123-45-6789" |
| CREDIT_CARD | Pattern + Luhn | "4111 1111 1111 1111" |
| IP_ADDRESS | Pattern | "192.168.1.100", "10.128.0.5" |
| DATE_TIME | Pattern + SpaCy | "March 15, 2026" |
| LOCATION | SpaCy NER | "New York", "San Francisco" |
| ORGANIZATION | SpaCy NER | "Acme Corp", "Red Hat" |
| URL | Pattern | "https://internal.company.com/api" |

### Custom Recognizers (Project-Specific)

| Entity Type | Detection Method | Examples | Rationale |
|-------------|-----------------|----------|-----------|
| CLUSTER_NAME | Regex pattern | `*.cjlabs.dev`, `ironman`, `api-int.ironman` | Cluster DNS names leak infrastructure topology |
| K8S_NAMESPACE | Regex + allowlist | `homelab-maas`, `home-assistant`, `semantic-redacted` | Namespace names reveal workload organization |
| K8S_RESOURCE | Pattern | `ollama-qwen36`, `semantic-claw-router`, `openai-proxy` | Resource names reveal deployed services |
| PROJECT_CODENAME | GLiNER zero-shot | "Project Phoenix", "Operation Lighthouse" | Internal project names are business-confidential |
| EMPLOYEE_ID | Pattern | `EMP-12345`, `E-7829` | Employee identifiers link to personnel records |
| INTERNAL_URL | Pattern | `*.svc.cluster.local`, `*.internal` | Internal URLs reveal network topology |

### Custom Recognizer Implementation Pattern

```python
from presidio_analyzer import PatternRecognizer, Pattern

cluster_recognizer = PatternRecognizer(
    supported_entity="CLUSTER_NAME",
    patterns=[
        Pattern(
            name="cjlabs_domain",
            regex=r"\b[\w-]+\.cjlabs\.dev\b",
            score=0.9,
        ),
        Pattern(
            name="k8s_svc_dns",
            regex=r"\b[\w-]+\.[\w-]+\.svc\.cluster\.local\b",
            score=0.95,
        ),
    ],
    context=["cluster", "node", "endpoint", "host"],
)
```

### GLiNER Integration

GLiNER provides zero-shot entity detection for custom taxonomies without training:

```python
from gliner import GLiNER

model = GLiNER.from_pretrained("urchade/gliner_multi_pii-v1")

entities = model.predict_entities(
    text,
    labels=["project codename", "internal tool", "team name", "building name"],
    threshold=0.5,
)
```

GLiNER runs as a secondary detector alongside Presidio. Its results are merged with Presidio's, with deduplication on overlapping spans (highest-confidence wins).

## Pseudonymization Strategy

### Placeholder Format

```
<ENTITY_TYPE_N>
```

Where:
- `ENTITY_TYPE` is the uppercase entity category (PERSON, EMAIL, PROJECT, etc.)
- `N` is a sequential counter per type within the request

Examples:
- `Sarah Chen` → `<PERSON_1>`
- `John Martinez` → `<PERSON_2>`
- `sarah@company.com` → `<EMAIL_1>`
- `Project Phoenix` → `<PROJECT_1>`

### Why Placeholders Over Deletion

Research (Anonymous-by-Construction) shows that type-preserving substitution preserves semantic utility for downstream Q&A better than deletion. The SaaS model needs to understand that `<PERSON_1>` is a person to generate a coherent response about them.

### Mapping Store

- **Scope:** Per-request. Each request gets a unique `mapping_id`.
- **Storage:** In-memory dictionary. Never written to disk, never logged, never persisted.
- **Lifetime:** Created on `/redact`, consumed on `/restore`, deleted after restoration.
- **Security:** Mapping contains the original sensitive values. It NEVER leaves the redaction service process.

```python
# In-memory mapping structure
mappings: dict[str, dict[str, str]] = {
    "m-7f3a9b2c": {
        "<PERSON_1>": "Sarah Chen",
        "<PERSON_2>": "John Martinez",
        "<EMAIL_1>": "sarah@company.com",
        "<PROJECT_1>": "Project Phoenix",
    }
}
```

### Deterministic Mapping

The same entity appearing multiple times in a request always gets the same placeholder. "Sarah Chen" is always `<PERSON_1>` within a single request, regardless of where it appears. This preserves co-reference in the redacted text.

## Entity Detection Pipeline

```
Input Text
    │
    ▼
┌───────────────────────┐
│ 1. Presidio Analyzer   │  Built-in recognizers (SpaCy, regex, pattern)
│    (primary)           │  Score threshold: 0.5
└──────────┬────────────┘
           │
           ▼
┌───────────────────────┐
│ 2. Custom Recognizers  │  Cluster names, K8s resources, employee IDs
│    (Presidio patterns) │  Score threshold: 0.7
└──────────┬────────────┘
           │
           ▼
┌───────────────────────┐
│ 3. GLiNER             │  Zero-shot: project codenames, team names
│    (secondary)         │  Score threshold: 0.5
└──────────┬────────────┘
           │
           ▼
┌───────────────────────┐
│ 4. Merge & Dedup      │  Overlapping spans: keep highest confidence
│                        │  Adjacent spans: merge if same entity type
└──────────┬────────────┘
           │
           ▼
┌───────────────────────┐
│ 5. Anonymizer          │  Replace detected spans with placeholders
│    (Presidio)          │  Build mapping table
└──────────┬────────────┘
           │
           ▼
Redacted Text + Mapping ID
```

## Configuration

```yaml
# config.yaml
presidio:
  analyzer:
    supported_entities:
      - PERSON
      - EMAIL_ADDRESS
      - PHONE_NUMBER
      - US_SSN
      - CREDIT_CARD
      - IP_ADDRESS
      - DATE_TIME
      - LOCATION
      - ORGANIZATION
      - URL
    score_threshold: 0.5
    nlp_engine: "spacy"
    spacy_model: "en_core_web_sm"

  custom_recognizers:
    - entity: CLUSTER_NAME
      patterns:
        - regex: "\\b[\\w-]+\\.cjlabs\\.dev\\b"
          score: 0.9
        - regex: "\\b[\\w-]+\\.[\\w-]+\\.svc\\.cluster\\.local\\b"
          score: 0.95
    - entity: K8S_NAMESPACE
      deny_list:
        - "homelab-maas"
        - "home-assistant"
        - "semantic-redacted"
        - "voice"
        - "n8n"
        - "keycloak"
      score: 0.85
    - entity: EMPLOYEE_ID
      patterns:
        - regex: "\\b(EMP|E)-\\d{4,6}\\b"
          score: 0.9

gliner:
  model: "urchade/gliner_multi_pii-v1"
  labels:
    - "project codename"
    - "internal tool name"
    - "team name"
    - "building name"
    - "internal product name"
  threshold: 0.5

anonymizer:
  placeholder_format: "<{entity_type}_{index}>"

server:
  host: "0.0.0.0"
  port: 8000
  workers: 2
  log_level: "info"
  log_format: "json"
```

## Container Specification

```dockerfile
FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0
RUN pip install --no-cache-dir \
    fastapi uvicorn \
    presidio-analyzer presidio-anonymizer \
    spacy gliner \
    pyyaml

RUN python -m spacy download en_core_web_sm

USER 1001
COPY app.py recognizers.py pseudonymizer.py config.yaml /app/
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Performance Expectations

| Operation | Expected Latency | Notes |
|-----------|-----------------|-------|
| Presidio analysis | 5-15ms | Depends on text length and recognizer count |
| GLiNER prediction | 20-50ms | CPU inference, batch-friendly |
| Anonymization | <1ms | String replacement |
| Restoration | <1ms | String replacement |
| Total /redact | 30-70ms | Including all recognizers |
| Total /restore | <2ms | Lookup + replace |

## Security Considerations

1. **Mapping store is attack surface:** If the redaction service is compromised, all in-flight mappings are exposed. Mitigate with: short mapping lifetime, no persistence, network isolation.
2. **False negatives are data leaks:** Presidio warns automated detection isn't comprehensive. GLiNER and custom recognizers reduce but don't eliminate this risk.
3. **Side-channel through placeholders:** The COUNT of entities and their TYPES (e.g., 3 PERSONs, 1 PROJECT) leak metadata. Acceptable for this demo; production may need fixed-count padding.
4. **Restore endpoint must be internal-only:** Only cluster-internal callers should be able to restore. Enforce via NetworkPolicy and/or caller IP check.
