# Deployment Runbook: OpenShift

## Prerequisites

1. OpenShift cluster access (`oc` CLI authenticated)
2. Qwen 3.6 running in `homelab-maas` namespace
3. Semantic Claw Router running in `homelab-maas` namespace
4. Gemini API key available as a secret in the cluster
5. Container registry access (quay.io/cnuland or cluster internal registry)

## Login

```bash
oc login --token=sha256~<token> --server=https://api.ironman.cjlabs.dev:6443
```

## Deployment Sequence

Services deploy in dependency order. NetworkPolicies apply FIRST.

### Step 1: Create Namespace

```bash
oc apply -f manifests/openshift/namespace.yaml
```

```yaml
# manifests/openshift/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: semantic-redacted
  labels:
    app.kubernetes.io/part-of: semantic-redacted
    kubernetes.io/metadata.name: semantic-redacted
```

### Step 2: Apply NetworkPolicies

Apply BEFORE any pods exist. This ensures the default-deny is active from the start.

```bash
oc apply -f manifests/openshift/network-policy/default-deny-egress.yaml
oc apply -f manifests/openshift/network-policy/allow-internal.yaml
oc apply -f manifests/openshift/network-policy/allow-sanitized-egress.yaml
```

Verify:
```bash
oc get networkpolicy -n semantic-redacted
# Should show 3 policies: default-deny-egress, allow-internal, allow-sanitized-egress
```

### Step 3: Create Secrets

Copy the Gemini API key from homelab-maas (or create a new reference):

```bash
# Option A: Copy existing secret
GEMINI_KEY=$(oc get secret semantic-claw-router-secret -n homelab-maas -o jsonpath='{.data.GEMINI_API_KEY}')

oc create secret generic redaction-service-secret \
  -n semantic-redacted \
  --from-literal=GEMINI_API_KEY="$(echo $GEMINI_KEY | base64 -d)"

# Option B: Create from known value
oc create secret generic redaction-service-secret \
  -n semantic-redacted \
  --from-literal=GEMINI_API_KEY="<your-key>"
```

### Step 4: Deploy Qdrant (Vector Store)

```bash
oc apply -f manifests/openshift/rag-store/pvc.yaml
oc apply -f manifests/openshift/rag-store/deployment.yaml
oc apply -f manifests/openshift/rag-store/service.yaml
```

Wait for readiness:
```bash
oc rollout status deployment/qdrant -n semantic-redacted --timeout=120s
```

Verify:
```bash
oc exec -it deploy/qdrant -n semantic-redacted -- \
  curl -s http://localhost:6333/healthz
# Expected: {"title":"qdrant","version":"..."}
```

### Step 5: Build and Deploy Redaction Service

```bash
# Build the container image
oc new-build --binary --name=redaction-service \
  --image-stream=python:3.11-ubi9 \
  -n semantic-redacted

oc start-build redaction-service \
  --from-dir=src/redaction-service \
  -n semantic-redacted --follow

# Deploy
oc apply -f manifests/openshift/redaction-service/configmap.yaml
oc apply -f manifests/openshift/redaction-service/deployment.yaml
oc apply -f manifests/openshift/redaction-service/service.yaml
```

Wait for readiness:
```bash
oc rollout status deployment/redaction-service -n semantic-redacted --timeout=180s
```

Verify health:
```bash
oc exec -it deploy/redaction-service -n semantic-redacted -- \
  curl -s http://localhost:8000/health
```

Verify egress (this pod SHOULD be able to reach external):
```bash
oc exec -it deploy/redaction-service -n semantic-redacted -- \
  curl -s --connect-timeout 10 https://generativelanguage.googleapis.com/v1/models
# Expected: HTTP response (connection succeeds)
```

### Step 6: Build and Deploy Guardrails Service

```bash
oc new-build --binary --name=guardrails-service \
  --image-stream=python:3.11-ubi9 \
  -n semantic-redacted

oc start-build guardrails-service \
  --from-dir=src/guardrails-service \
  -n semantic-redacted --follow

oc apply -f manifests/openshift/guardrails-service/configmap.yaml
oc apply -f manifests/openshift/guardrails-service/deployment.yaml
oc apply -f manifests/openshift/guardrails-service/service.yaml
```

Wait for readiness:
```bash
oc rollout status deployment/guardrails-service -n semantic-redacted --timeout=180s
```

Verify health:
```bash
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://localhost:8001/health
```

Verify egress block (this pod should NOT reach external):
```bash
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -v --connect-timeout 5 https://generativelanguage.googleapis.com/v1/models
# Expected: Connection timeout
```

### Step 7: Load Sensitive RAG Document

```bash
# Run the document loader as a Job
oc apply -f manifests/openshift/rag-store/load-job.yaml

# Or run locally against the port-forwarded Qdrant
oc port-forward svc/qdrant 6333:6333 -n semantic-redacted &
python src/demo/load_rag_doc.py
```

Verify:
```bash
oc exec -it deploy/qdrant -n semantic-redacted -- \
  curl -s http://localhost:6333/collections/sensitive_docs
# Expected: {"result":{"status":"green","points_count":15,...}}
```

### Step 8: Update Router Config

Apply the updated router config with sensitivity signals:

```bash
oc apply -f manifests/openshift/router-update/configmap.yaml -n homelab-maas
```

Restart the router to pick up the new config:
```bash
oc rollout restart deployment/semantic-claw-router -n homelab-maas
oc rollout status deployment/semantic-claw-router -n homelab-maas --timeout=120s
```

### Step 9: Run Demo Scenarios

```bash
# Port-forward the router for local testing
oc port-forward svc/semantic-claw-router 8080:8080 -n homelab-maas &

# Run all 6 scenarios
python src/demo/run_demo.py --all

# Or run individually
python src/demo/run_demo.py --scenario 1  # Public query
python src/demo/run_demo.py --scenario 4  # Redact-and-route
python src/demo/run_demo.py --scenario 6  # Bypass attempt
```

## Verification Checklist

After deployment, verify each component:

```bash
# 1. All pods running
oc get pods -n semantic-redacted
# Expected: qdrant, redaction-service, guardrails-service all Running

# 2. NetworkPolicies active
oc get networkpolicy -n semantic-redacted
# Expected: 3 policies

# 3. Services reachable
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://redaction-service.semantic-redacted.svc:8000/health
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://qdrant.semantic-redacted.svc:6333/healthz

# 4. Cross-namespace access (Qwen)
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://ollama-qwen36.homelab-maas.svc.cluster.local:11434/api/tags

# 5. Egress gateway works
oc exec -it deploy/redaction-service -n semantic-redacted -- \
  curl -s --connect-timeout 10 https://generativelanguage.googleapis.com/v1/models

# 6. Egress blocked for non-gateway pods
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -v --connect-timeout 5 https://generativelanguage.googleapis.com/v1/models
# Must timeout

# 7. RAG document loaded
oc exec -it deploy/qdrant -n semantic-redacted -- \
  curl -s http://localhost:6333/collections/sensitive_docs | python3 -m json.tool
```

## Rollback

If anything goes wrong, the new namespace is completely isolated:

```bash
# Remove everything without affecting existing services
oc delete namespace semantic-redacted

# Revert router config if it was updated
oc rollout undo deployment/semantic-claw-router -n homelab-maas
```

## Troubleshooting

### Pod stuck in CrashLoopBackOff
```bash
oc logs deploy/<service-name> -n semantic-redacted --previous
```

### NetworkPolicy not blocking egress
```bash
# Verify SDN type supports NetworkPolicy
oc get network.config/cluster -o jsonpath='{.spec.networkType}'
# Must be: OVNKubernetes

# Verify policies exist
oc get networkpolicy -n semantic-redacted -o yaml
```

### Cannot reach Qwen cross-namespace
```bash
# Verify namespace label exists
oc get namespace homelab-maas -o jsonpath='{.metadata.labels}'

# Verify Qwen service exists
oc get svc -n homelab-maas | grep ollama
```

### Redaction service cannot reach Gemini
```bash
# Verify pod labels include role: egress-gateway
oc get pod -l app=redaction-service -n semantic-redacted -o jsonpath='{.items[0].metadata.labels}'

# Verify allow-sanitized-egress policy selects this pod
oc describe networkpolicy allow-sanitized-egress -n semantic-redacted
```

### GLiNER model download fails
The GLiNER model downloads from Hugging Face on first startup. If the pod has no internet access:
1. Pre-download the model in the container build step
2. Or mount a PVC with the cached model

### NeMo Guardrails cannot reach Qwen
```bash
# Verify cross-namespace DNS resolution
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  nslookup ollama-qwen36.homelab-maas.svc.cluster.local

# Verify port is open
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://ollama-qwen36.homelab-maas.svc.cluster.local:11434/api/tags
```
