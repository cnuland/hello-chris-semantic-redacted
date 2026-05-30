# Egress Policy: NetworkPolicy Enforcement

## Design Principle

Do not rely on the application behaving correctly. Enforce the trust boundary at the platform level.

The redaction service, guardrails service, and classifier all prevent sensitive data from leaving the cluster through application logic. But application logic can have bugs. NetworkPolicy provides a defense-in-depth layer that makes bypass impossible even if every application component fails.

## Policy Architecture

```
┌─────────────────────────────────────────────────────────┐
│              semantic-redacted namespace                  │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │  NetworkPolicy: default-deny-egress             │    │
│  │  Effect: No pod can make ANY outbound connection │    │
│  │          unless explicitly allowed               │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │  NetworkPolicy: allow-internal                   │    │
│  │  Effect: All pods can reach:                     │    │
│  │    - DNS (kube-dns, port 53)                     │    │
│  │    - Kubernetes API (port 6443)                  │    │
│  │    - homelab-maas namespace (Qwen, router)       │    │
│  │    - Other pods in semantic-redacted             │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │  NetworkPolicy: allow-sanitized-egress           │    │
│  │  Effect: ONLY redaction-service pods can reach:  │    │
│  │    - External HTTPS (port 443)                   │    │
│  │    - This is the ONLY path to SaaS endpoints     │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  Pod: redaction-service     ──── CAN call Gemini ────▶ ☁️  │
│  Pod: guardrails-service   ──── CANNOT call Gemini ──▶ ✗  │
│  Pod: nemo-egress-guard    ──── CANNOT call Gemini ──▶ ✗  │
│  Pod: sensitivity-classifier── CANNOT call Gemini ──▶ ✗  │
│  Pod: qdrant               ──── CANNOT call Gemini ──▶ ✗  │
│  Pod: demo-runner          ──── CANNOT call Gemini ──▶ ✗  │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## NetworkPolicy Manifests

### 1. Default Deny Egress

This MUST be applied FIRST, before any pods are created in the namespace.

```yaml
# manifests/openshift/network-policy/default-deny-egress.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: semantic-redacted
  labels:
    app.kubernetes.io/part-of: semantic-redacted
    policy-type: baseline
spec:
  podSelector: {}
  policyTypes:
    - Egress
```

This policy selects ALL pods (empty `podSelector`) and declares an Egress policy type with NO egress rules — meaning all outbound traffic is denied by default.

### 2. Allow Internal Communication

Permits pods to reach DNS, Kubernetes API, and internal services.

```yaml
# manifests/openshift/network-policy/allow-internal.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-internal
  namespace: semantic-redacted
  labels:
    app.kubernetes.io/part-of: semantic-redacted
    policy-type: internal
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    # Allow DNS resolution
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53

    # Allow intra-namespace communication
    - to:
        - podSelector: {}

    # Allow communication to homelab-maas namespace (Qwen, router)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: homelab-maas
      ports:
        - protocol: TCP
          port: 11434  # Ollama
        - protocol: TCP
          port: 8080   # llama-server / semantic-claw-router

    # Allow Kubernetes API access
    - to:
        - ipBlock:
            cidr: 172.30.0.1/32  # Kubernetes API service IP (verify on cluster)
      ports:
        - protocol: TCP
          port: 6443
```

### 3. Allow Sanitized Egress (Redaction Service Only)

Only the redaction-service pod can make external HTTPS calls.

```yaml
# manifests/openshift/network-policy/allow-sanitized-egress.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-sanitized-egress
  namespace: semantic-redacted
  labels:
    app.kubernetes.io/part-of: semantic-redacted
    policy-type: egress-gateway
spec:
  podSelector:
    matchLabels:
      app: redaction-service
      role: egress-gateway
  policyTypes:
    - Egress
  egress:
    # Allow HTTPS to external SaaS endpoints
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

This policy:
- Selects ONLY pods with labels `app: redaction-service` AND `role: egress-gateway`
- Allows outbound HTTPS (port 443) to external IPs (excluding private ranges)
- Does NOT allow HTTP (port 80) — SaaS endpoints should always use TLS

## Pod Labeling Requirements

For the NetworkPolicy to work correctly, pods MUST have these labels:

| Pod | Required Labels |
|-----|----------------|
| redaction-service | `app: redaction-service`, `role: egress-gateway` |
| guardrails-service | `app: guardrails-service` |
| nemo-egress-guard | `app: nemo-egress-guard` (NO `role: egress-gateway`) |
| sensitivity-classifier | `app: sensitivity-classifier` |
| qdrant | `app: qdrant` |
| demo-runner | `app: demo-runner` |

The `role: egress-gateway` label is the key differentiator. Only the redaction-service gets this label.

## Verification Procedures

### Test 1: Verify Default Deny

From any non-gateway pod, attempt to reach an external endpoint:

```bash
# Exec into the guardrails pod
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s --connect-timeout 5 https://generativelanguage.googleapis.com/v1/models

# Expected: Connection timeout (exit code 28) or connection refused
# If this succeeds, the NetworkPolicy is not enforced
```

### Test 2: Verify Egress Gateway Works

From the redaction-service pod:

```bash
# Exec into the redaction service pod
oc exec -it deploy/redaction-service -n semantic-redacted -- \
  curl -s --connect-timeout 10 https://generativelanguage.googleapis.com/v1/models

# Expected: HTTP 200 or 401 (auth error, but connection succeeded)
# This proves the egress gateway can reach external endpoints
```

### Test 3: Verify Internal Communication

From any pod, reach internal services:

```bash
# Reach Qwen in homelab-maas
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://ollama-qwen36.homelab-maas.svc.cluster.local:11434/api/tags

# Expected: JSON response with model list

# Reach redaction service within namespace
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  curl -s http://redaction-service.semantic-redacted.svc:8000/health

# Expected: JSON health response
```

### Test 5: Verify Egress Guard Cannot Reach SaaS

The NeMo egress guard evaluates redacted content but must NOT be able to reach external endpoints directly:

```bash
oc exec -it deploy/nemo-egress-guard -n semantic-redacted -- \
  curl -v --connect-timeout 5 https://generativelanguage.googleapis.com/v1/models

# Expected: Connection timeout (exit code 28) or connection refused
# The egress guard lives inside the trust zone -- no direct SaaS access
```

### Test 4: Verify DNS Resolution

```bash
oc exec -it deploy/guardrails-service -n semantic-redacted -- \
  nslookup generativelanguage.googleapis.com

# Expected: DNS resolves (name resolution is allowed)
# But the actual TCP connection should be blocked
```

## OpenShift-Specific Considerations

### SDN Plugin Compatibility

NetworkPolicy behavior depends on the OpenShift networking plugin:
- **OVN-Kubernetes** (default in OCP 4.x+): Full NetworkPolicy support
- **OpenShift SDN**: Requires `networkpolicy` mode (not `multitenant` or `subnet`)

Verify with:
```bash
oc get network.config/cluster -o jsonpath='{.spec.networkType}'
```

### Namespace Labels

The `allow-internal` policy uses `namespaceSelector` with `kubernetes.io/metadata.name`. This label is automatically applied by Kubernetes 1.21+. Verify:

```bash
oc get namespace homelab-maas -o jsonpath='{.metadata.labels.kubernetes\.io/metadata\.name}'
# Expected: homelab-maas
```

### OpenShift Routes

If any service in `semantic-redacted` needs an OpenShift Route (external ingress), that's INGRESS policy, not egress. The default-deny-egress policy does not affect incoming traffic.

## Failure Modes

| Scenario | What Happens | Detection |
|----------|-------------|-----------|
| NetworkPolicy not enforced (wrong SDN mode) | All pods can reach external | Test 1 succeeds (should fail) |
| Missing `role: egress-gateway` label | Redaction service can't reach SaaS | Redaction fails, demo scenario 4 fails |
| Wrong namespace label on homelab-maas | Can't reach Qwen | All local routing fails |
| DNS resolution blocked | Nothing works | All services fail health checks |

## Red Hat Alignment

This is where Red Hat's Summit 2026 sovereignty messaging lands hardest:

> The platform is not just where the model runs; it is where the egress contract is enforced.

NetworkPolicy is a Kubernetes-native construct. On OpenShift, it's backed by OVN-Kubernetes with enterprise support. For production:
- **Red Hat Advanced Cluster Security (ACS)** can alert on unexpected egress paths
- **OpenShift Compliance Operator** can verify baseline network controls exist
- **Service Mesh (Istio/Envoy)** can add mTLS and fine-grained egress gateway controls

The demo uses vanilla NetworkPolicy because it's the simplest, most portable enforcement mechanism. The Red Hat story is that OpenShift provides the platform controls to make this enforceable, auditable, and compliant.
