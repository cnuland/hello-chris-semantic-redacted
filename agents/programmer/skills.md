# Programmer Agent -- Skills Inventory

This document catalogs the Programmer agent's technical capabilities with concrete examples relevant to the privacy-preserving semantic routing project.

## Python Development

### FastAPI & Async HTTP

Build high-performance API services with automatic OpenAPI documentation and async request handling.

```python
from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger("redaction-service")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load models, warm caches
    app.state.analyzer = initialize_presidio()
    logger.info("Presidio AnalyzerEngine initialized")
    yield
    # Shutdown: release resources
    logger.info("Shutting down")

app = FastAPI(title="Redaction Service", lifespan=lifespan)

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}

@app.post("/redact", response_model=RedactionResponse)
async def redact(request: RedactionRequest) -> RedactionResponse:
    try:
        result = await perform_redaction(request.text, request.entity_types)
        return result
    except Exception as e:
        logger.error("Redaction failed", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Redaction processing error")
```

### Presidio Integration

Configure Microsoft Presidio for entity detection and anonymization, including custom recognizer registration.

```python
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

def initialize_presidio() -> tuple[AnalyzerEngine, AnonymizerEngine]:
    """Initialize Presidio with default + custom recognizers."""
    analyzer = AnalyzerEngine()

    # Register custom recognizer for employee IDs (EMP-XXXXX pattern)
    emp_id_recognizer = PatternRecognizer(
        supported_entity="EMPLOYEE_ID",
        patterns=[
            Pattern(
                name="employee_id",
                regex=r"\bEMP-\d{5}\b",
                score=0.95,
            )
        ],
        supported_language="en",
    )
    analyzer.registry.add_recognizer(emp_id_recognizer)

    anonymizer = AnonymizerEngine()
    return analyzer, anonymizer

def analyze_text(
    analyzer: AnalyzerEngine,
    text: str,
    entity_types: list[str] | None = None,
) -> list:
    """Run Presidio analysis, return detected entities."""
    return analyzer.analyze(
        text=text,
        entities=entity_types,
        language="en",
    )
```

### Custom Presidio Recognizers

Build domain-specific recognizers for entities Presidio does not detect by default.

```python
from presidio_analyzer import EntityRecognizer, RecognizerResult

class KubernetesResourceRecognizer(EntityRecognizer):
    """Detect Kubernetes resource references like pod names, namespaces."""

    SUPPORTED_ENTITIES = ["K8S_RESOURCE"]
    K8S_PATTERNS = [
        r"\b[a-z0-9]+(?:-[a-z0-9]+)*-[a-f0-9]{8,10}-[a-z0-9]{5}\b",  # pod names
        r"\b(?:namespace|ns)[:/\s]+[a-z0-9-]+\b",                       # namespace refs
    ]

    def __init__(self):
        super().__init__(
            supported_entities=self.SUPPORTED_ENTITIES,
            supported_language="en",
        )

    def load(self) -> None:
        pass

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts=None
    ) -> list[RecognizerResult]:
        results = []
        for pattern in self.K8S_PATTERNS:
            for match in re.finditer(pattern, text):
                results.append(
                    RecognizerResult(
                        entity_type="K8S_RESOURCE",
                        start=match.start(),
                        end=match.end(),
                        score=0.85,
                    )
                )
        return results


class ProjectCodenameRecognizer(EntityRecognizer):
    """Detect internal project codenames loaded from config."""

    SUPPORTED_ENTITIES = ["PROJECT_CODENAME"]

    def __init__(self, codenames: list[str]):
        super().__init__(
            supported_entities=self.SUPPORTED_ENTITIES,
            supported_language="en",
        )
        self.codenames = [cn.lower() for cn in codenames]

    def load(self) -> None:
        pass

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts=None
    ) -> list[RecognizerResult]:
        results = []
        text_lower = text.lower()
        for codename in self.codenames:
            start = 0
            while True:
                idx = text_lower.find(codename, start)
                if idx == -1:
                    break
                results.append(
                    RecognizerResult(
                        entity_type="PROJECT_CODENAME",
                        start=idx,
                        end=idx + len(codename),
                        score=0.95,
                    )
                )
                start = idx + 1
        return results
```

### Sentence-Transformers & Embedding-Based Classification

Use pre-trained embedding models for sensitivity scoring via cosine similarity against anchor prompts.

```python
from sentence_transformers import SentenceTransformer
import numpy as np

class SensitivityClassifier:
    """Classify text sensitivity using embedding similarity to anchor prompts."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name)
        self.anchors: dict[str, np.ndarray] = {}

    def load_anchors(self, anchors: dict[str, list[str]]) -> None:
        """Pre-compute anchor embeddings for each sensitivity level."""
        for level, prompts in anchors.items():
            embeddings = self.model.encode(prompts, normalize_embeddings=True)
            self.anchors[level] = np.mean(embeddings, axis=0)

    def classify(self, text: str) -> tuple[str, float]:
        """Return (sensitivity_level, confidence_score)."""
        text_embedding = self.model.encode(text, normalize_embeddings=True)
        scores = {}
        for level, anchor_embedding in self.anchors.items():
            scores[level] = float(np.dot(text_embedding, anchor_embedding))
        best_level = max(scores, key=scores.get)
        return best_level, scores[best_level]
```

### Deterministic Pseudonymization

Implement request-scoped pseudonymization that maps the same entity to the same placeholder consistently.

```python
from typing import Any

class Pseudonymizer:
    """In-memory, request-scoped deterministic pseudonymization."""

    def __init__(self):
        self._forward: dict[str, dict[str, str]] = {}   # request_id -> {original: placeholder}
        self._reverse: dict[str, dict[str, str]] = {}   # request_id -> {placeholder: original}
        self._counters: dict[str, dict[str, int]] = {}   # request_id -> {entity_type: count}

    def pseudonymize(
        self, request_id: str, entity_type: str, original_value: str
    ) -> str:
        if request_id not in self._forward:
            self._forward[request_id] = {}
            self._reverse[request_id] = {}
            self._counters[request_id] = {}

        if original_value in self._forward[request_id]:
            return self._forward[request_id][original_value]

        count = self._counters[request_id].get(entity_type, 0) + 1
        self._counters[request_id][entity_type] = count
        placeholder = f"<{entity_type}_{count}>"

        self._forward[request_id][original_value] = placeholder
        self._reverse[request_id][placeholder] = original_value
        return placeholder

    def restore(self, request_id: str, redacted_text: str) -> str:
        if request_id not in self._reverse:
            raise KeyError(f"No mapping found for request {request_id}")
        result = redacted_text
        for placeholder, original in self._reverse[request_id].items():
            result = result.replace(placeholder, original)
        return result

    def clear(self, request_id: str) -> None:
        self._forward.pop(request_id, None)
        self._reverse.pop(request_id, None)
        self._counters.pop(request_id, None)
```

### NeMo Guardrails

Configure NeMo Guardrails with Colang 2.0 syntax for input, retrieval, and output rails.

```yaml
# config.yml for NeMo Guardrails
models:
  - type: main
    engine: openai
    model: qwen-local
    parameters:
      base_url: "http://ollama-qwen36.homelab-maas.svc:11434/v1"

rails:
  input:
    flows:
      - check sensitivity before routing
  output:
    flows:
      - scan response for leaked pii
```

```colang
# input.co -- Colang 2.0 input rail
define flow check sensitivity before routing
    $sensitivity = execute check_sensitivity_classification(text=$user_message)

    if $sensitivity == "NEVER_EGRESS"
        bot refuse and route locally
        stop

    if $sensitivity == "REGULATED"
        bot refuse and route locally
        stop

    if $sensitivity in ["CONFIDENTIAL", "INTERNAL"]
        $redacted = execute invoke_redaction(text=$user_message)
        $user_message = $redacted
```

```python
# actions/sensitivity.py -- Custom NeMo action
from nemoguardrails.actions import action
import httpx

@action()
async def check_sensitivity_classification(text: str) -> str:
    """Call the sensitivity classifier service."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://sensitivity-classifier.semantic-redacted.svc:8080/classify",
            json={"text": text},
            timeout=5.0,
        )
        if response.status_code != 200:
            return "NEVER_EGRESS"  # fail closed
        return response.json()["sensitivity_level"]
```

## Kubernetes / OpenShift Manifest Authoring

### Deployment with Health Checks and Security Context

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redaction-service
  namespace: semantic-redacted
  labels:
    app.kubernetes.io/name: redaction-service
    app.kubernetes.io/part-of: semantic-redacted
    app.kubernetes.io/component: redaction
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: redaction-service
  template:
    metadata:
      labels:
        app.kubernetes.io/name: redaction-service
        app.kubernetes.io/part-of: semantic-redacted
        app.kubernetes.io/component: redaction
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
        - name: redaction-service
          image: image-registry.openshift-image-registry.svc:5000/semantic-redacted/redaction-service:latest
          ports:
            - containerPort: 8080
              protocol: TCP
          env:
            - name: LOG_LEVEL
              valueFrom:
                configMapKeyRef:
                  name: redaction-service-config
                  key: LOG_LEVEL
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 15
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10
```

### NetworkPolicy -- Default Deny Egress

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: semantic-redacted
  labels:
    app.kubernetes.io/part-of: semantic-redacted
    app.kubernetes.io/component: network-policy
spec:
  podSelector: {}
  policyTypes:
    - Egress
```

### NetworkPolicy -- Allow SaaS Egress for Gateway Only

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-saas-egress
  namespace: semantic-redacted
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: egress-gateway
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
```

### Service Definition

```yaml
apiVersion: v1
kind: Service
metadata:
  name: sensitivity-classifier
  namespace: semantic-redacted
  labels:
    app.kubernetes.io/name: sensitivity-classifier
    app.kubernetes.io/part-of: semantic-redacted
    app.kubernetes.io/component: classifier
spec:
  selector:
    app.kubernetes.io/name: sensitivity-classifier
  ports:
    - port: 8080
      targetPort: 8080
      protocol: TCP
  type: ClusterIP
```

### ConfigMap for Service Configuration

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sensitivity-classifier-config
  namespace: semantic-redacted
data:
  LOG_LEVEL: "INFO"
  MODEL_NAME: "all-MiniLM-L6-v2"
  ANCHORS_PATH: "/opt/app-root/data/anchors.yaml"
  CLASSIFIER_THRESHOLD: "0.65"
```

## Container Building

### Multi-Stage UBI9 Dockerfile

```dockerfile
# Stage 1: Install dependencies
FROM registry.access.redhat.com/ubi9/python-311 AS builder
WORKDIR /opt/app-root/src
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime image
FROM registry.access.redhat.com/ubi9/python-311
WORKDIR /opt/app-root/src

# Copy installed packages from builder
COPY --from=builder /opt/app-root/lib /opt/app-root/lib
COPY --from=builder /opt/app-root/bin /opt/app-root/bin

# Copy application code
COPY . .

# Non-root user (OpenShift restricted SCC compatible)
USER 1001

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
```

### Model Pre-Download in Build Stage

For services that load ML models, download them during the build to avoid runtime network dependencies:

```dockerfile
FROM registry.access.redhat.com/ubi9/python-311 AS builder
WORKDIR /opt/app-root/src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the sentence-transformers model
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

FROM registry.access.redhat.com/ubi9/python-311
WORKDIR /opt/app-root/src
COPY --from=builder /opt/app-root/lib /opt/app-root/lib
COPY --from=builder /opt/app-root/bin /opt/app-root/bin
# Copy cached model from builder home directory
COPY --from=builder /opt/app-root/src/.cache /opt/app-root/src/.cache
COPY . .
USER 1001
ENV SENTENCE_TRANSFORMERS_HOME=/opt/app-root/src/.cache
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

## OpenShift CLI Operations

### Namespace and Resource Management

```bash
# Create the namespace
oc new-project semantic-redacted --description="Privacy-preserving semantic routing services"

# Apply manifests in order
oc apply -f manifests/openshift/namespace.yaml
oc apply -f manifests/openshift/sensitivity-classifier/
oc apply -f manifests/openshift/redaction-service/
oc apply -f manifests/openshift/guardrails-service/
oc apply -f manifests/openshift/network-policies/

# Build images using OpenShift BuildConfigs
oc new-build --name=sensitivity-classifier --binary --strategy=docker -n semantic-redacted
oc start-build sensitivity-classifier --from-dir=src/sensitivity-classifier --follow -n semantic-redacted

# Verify deployments
oc get pods -n semantic-redacted
oc get svc -n semantic-redacted

# Check health endpoints
oc exec deploy/sensitivity-classifier -n semantic-redacted -- curl -s localhost:8080/health

# View structured logs
oc logs deploy/redaction-service -n semantic-redacted --tail=50 | python -m json.tool
```

### Troubleshooting NetworkPolicy

```bash
# Verify default-deny is active
oc get networkpolicy -n semantic-redacted

# Test egress from a non-gateway pod (should fail)
oc exec deploy/sensitivity-classifier -n semantic-redacted -- \
    curl -s --max-time 5 https://generativelanguage.googleapis.com/v1/models 2>&1 || echo "BLOCKED (expected)"

# Test egress from the gateway pod (should succeed)
oc exec deploy/egress-gateway -n semantic-redacted -- \
    curl -s --max-time 5 https://generativelanguage.googleapis.com/v1/models | head -c 200
```

## Sensitive Data Handling

### Safe Logging Patterns

```python
# CORRECT: Log entity counts and types, never values
logger.info(
    "Redaction complete",
    extra={
        "extra_fields": {
            "request_id": request_id,
            "entity_counts": {"PERSON": 3, "EMAIL": 1, "EMPLOYEE_ID": 2},
            "total_redacted": 6,
            "processing_time_ms": elapsed_ms,
        }
    },
)

# INCORRECT: Never do this
logger.info(f"Redacted PII: {original_text} -> {redacted_text}")
```

### Request-Scoped Mapping Lifecycle

```python
@app.post("/redact")
async def redact(request: RedactionRequest) -> RedactionResponse:
    request_id = str(uuid.uuid4())
    try:
        result = perform_redaction(request_id, request.text)
        # Mapping lives in memory, keyed by request_id
        # Caller must use request_id to restore later
        return RedactionResponse(
            redacted_text=result.text,
            mapping_id=request_id,
            entity_counts=result.counts,
        )
    except Exception:
        # On error, clean up any partial mapping
        pseudonymizer.clear(request_id)
        raise

@app.post("/restore")
async def restore(request: RestoreRequest) -> RestoreResponse:
    try:
        original = pseudonymizer.restore(request.mapping_id, request.redacted_text)
        return RestoreResponse(restored_text=original)
    finally:
        # Always clean up after restore -- mapping is single-use
        pseudonymizer.clear(request.mapping_id)
```

## Integration Testing

### Testing Against Live Cluster Services

```python
import pytest
import httpx

CLASSIFIER_URL = "http://sensitivity-classifier.semantic-redacted.svc:8080"
REDACTION_URL = "http://redaction-service.semantic-redacted.svc:8080"

@pytest.fixture
def client():
    return httpx.Client(timeout=10.0)

def test_public_prompt_classified_correctly(client):
    """A clearly public prompt should score as PUBLIC."""
    response = client.post(
        f"{CLASSIFIER_URL}/classify",
        json={"text": "What is the capital of France?"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["sensitivity_level"] == "PUBLIC"
    assert result["confidence"] > 0.5

def test_pii_detected_and_redacted(client):
    """Text with PII should have entities detected and pseudonymized."""
    response = client.post(
        f"{REDACTION_URL}/redact",
        json={"text": "Contact Sarah Chen at sarah.chen@acme.com about EMP-12345"},
    )
    assert response.status_code == 200
    result = response.json()
    assert "Sarah Chen" not in result["redacted_text"]
    assert "sarah.chen@acme.com" not in result["redacted_text"]
    assert "EMP-12345" not in result["redacted_text"]
    assert result["entity_counts"]["PERSON"] >= 1
    assert result["entity_counts"]["EMAIL_ADDRESS"] >= 1

def test_redact_restore_roundtrip(client):
    """Redact then restore should return the original text."""
    original = "Employee John Smith (EMP-99001) earned $150,000 in Q3."
    redact_resp = client.post(
        f"{REDACTION_URL}/redact",
        json={"text": original},
    )
    mapping_id = redact_resp.json()["mapping_id"]

    restore_resp = client.post(
        f"{REDACTION_URL}/restore",
        json={
            "mapping_id": mapping_id,
            "redacted_text": redact_resp.json()["redacted_text"],
        },
    )
    assert restore_resp.json()["restored_text"] == original
```

### Egress Policy Verification

```python
import subprocess

def test_non_gateway_pod_cannot_reach_saas():
    """Verify NetworkPolicy blocks direct SaaS access from non-gateway pods."""
    result = subprocess.run(
        [
            "oc", "exec", "deploy/sensitivity-classifier",
            "-n", "semantic-redacted", "--",
            "curl", "-s", "--max-time", "5",
            "https://generativelanguage.googleapis.com/v1/models",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    # Should timeout or connection refused -- not a 200
    assert result.returncode != 0 or "error" in result.stderr.lower()
```

### Demo Scenario Execution

```python
SCENARIOS = [
    {
        "name": "Public baseline",
        "prompt": "Explain the difference between TCP and UDP.",
        "expected_sensitivity": "PUBLIC",
        "expected_route": "saas",
    },
    {
        "name": "Confidential leak prevention",
        "prompt": "What is Sarah Chen's annual salary from the Q3 report?",
        "expected_sensitivity": "CONFIDENTIAL",
        "expected_route": "local",
    },
    {
        "name": "HR sensitivity",
        "prompt": "Summarize the performance review for employee EMP-40921.",
        "expected_sensitivity": "CONFIDENTIAL",
        "expected_route": "local",
    },
    {
        "name": "Redact and route",
        "prompt": "How does Project Phoenix compare to our competitor's approach?",
        "expected_sensitivity": "INTERNAL",
        "expected_route": "redact_then_saas",
    },
    {
        "name": "Financial data",
        "prompt": "What were the Q3 2025 revenue figures for the enterprise division?",
        "expected_sensitivity": "REGULATED",
        "expected_route": "local",
    },
    {
        "name": "Egress enforcement",
        "prompt": None,  # This scenario tests network policy, not a prompt
        "expected_sensitivity": None,
        "expected_route": "blocked",
    },
]
```

## Configuration Management

### Environment Variable Configuration Pattern

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Service configuration loaded from environment variables."""

    log_level: str = "INFO"
    service_name: str = "sensitivity-classifier"
    model_name: str = "all-MiniLM-L6-v2"
    anchors_path: str = "/opt/app-root/data/anchors.yaml"
    classifier_threshold: float = 0.65
    port: int = 8080

    # Upstream service endpoints
    redaction_service_url: str = "http://redaction-service.semantic-redacted.svc:8080"
    router_url: str = "http://semantic-claw-router.homelab-maas.svc:8080"

    class Config:
        env_prefix = ""
        case_sensitive = False
```

### Structured JSON Log Setup

```python
import logging
import json
import sys
from datetime import datetime, timezone

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service_name", record.name),
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception_type"] = record.exc_info[0].__name__
            # Do NOT include full traceback -- it may contain PII from request data
            log_entry["exception_message"] = str(record.exc_info[1])
        return json.dumps(log_entry)

def configure_logging(service_name: str, level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, level.upper()))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
```
