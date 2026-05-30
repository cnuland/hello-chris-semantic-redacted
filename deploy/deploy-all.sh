#!/bin/bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-user-cnuland}"
QWEN_SVC="qwen35-9b-kserve-workload-svc.prelude-maas.svc.cluster.local:8000"
REGISTRY="image-registry.openshift-image-registry.svc:5000/${NAMESPACE}"

echo "=== Deploying Semantic Redaction Demo to ${NAMESPACE} ==="
echo "Using Qwen at: ${QWEN_SVC}"

# Step 1: Switch to namespace
oc project "${NAMESPACE}"

# Step 2: Apply NetworkPolicies
echo ""
echo "--- Step 2: Applying NetworkPolicies ---"
cat <<'POLICY_EOF' | sed "s/NAMESPACE_PLACEHOLDER/${NAMESPACE}/g" | oc apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sr-default-deny-egress
  labels:
    app.kubernetes.io/part-of: semantic-redacted
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/part-of: semantic-redacted
  policyTypes:
    - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sr-allow-internal
  labels:
    app.kubernetes.io/part-of: semantic-redacted
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/part-of: semantic-redacted
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - podSelector:
            matchLabels:
              app.kubernetes.io/part-of: semantic-redacted
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: prelude-maas
      ports:
        - protocol: TCP
          port: 8000
    - to:
        - ipBlock:
            cidr: 172.30.0.0/16
      ports:
        - protocol: TCP
          port: 443
        - protocol: TCP
          port: 6443
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sr-allow-sanitized-egress
  labels:
    app.kubernetes.io/part-of: semantic-redacted
spec:
  podSelector:
    matchLabels:
      app: redaction-service
      role: egress-gateway
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
POLICY_EOF
echo "NetworkPolicies applied."

# Step 3: Deploy Qdrant
echo ""
echo "--- Step 3: Deploying Qdrant ---"
cat <<'EOF' | oc apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: qdrant-storage
  labels:
    app: qdrant
    app.kubernetes.io/part-of: semantic-redacted
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qdrant
  labels:
    app: qdrant
    app.kubernetes.io/name: qdrant
    app.kubernetes.io/part-of: semantic-redacted
spec:
  replicas: 1
  selector:
    matchLabels:
      app: qdrant
  template:
    metadata:
      labels:
        app: qdrant
        app.kubernetes.io/name: qdrant
        app.kubernetes.io/part-of: semantic-redacted
    spec:
      containers:
        - name: qdrant
          image: docker.io/qdrant/qdrant:v1.14.1
          ports:
            - containerPort: 6333
              name: http
            - containerPort: 6334
              name: grpc
          volumeMounts:
            - name: storage
              mountPath: /qdrant/storage
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
          readinessProbe:
            httpGet:
              path: /healthz
              port: 6333
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: 6333
            initialDelaySeconds: 10
            periodSeconds: 15
      volumes:
        - name: storage
          persistentVolumeClaim:
            claimName: qdrant-storage
---
apiVersion: v1
kind: Service
metadata:
  name: qdrant
  labels:
    app: qdrant
    app.kubernetes.io/part-of: semantic-redacted
spec:
  selector:
    app: qdrant
  ports:
    - port: 6333
      targetPort: 6333
      name: http
    - port: 6334
      targetPort: 6334
      name: grpc
EOF
echo "Qdrant deployed. Waiting for readiness..."
oc rollout status deployment/qdrant --timeout=120s || echo "WARNING: Qdrant not ready yet"

# Step 4: Build and deploy redaction service
echo ""
echo "--- Step 4: Building Redaction Service ---"
cd "$(dirname "$0")/.."

# Create BuildConfig if it doesn't exist
oc get bc redaction-service 2>/dev/null || \
  oc new-build --binary --name=redaction-service --strategy=docker -l app.kubernetes.io/part-of=semantic-redacted

oc start-build redaction-service --from-dir=src/redaction-service --follow

# Create ConfigMap from config
oc create configmap redaction-service-config \
  --from-file=config.yaml=src/redaction-service/config.yaml \
  --dry-run=client -o yaml | oc apply -f -

# Deploy
cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redaction-service
  labels:
    app: redaction-service
    role: egress-gateway
    app.kubernetes.io/name: redaction-service
    app.kubernetes.io/part-of: semantic-redacted
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redaction-service
  template:
    metadata:
      labels:
        app: redaction-service
        role: egress-gateway
        app.kubernetes.io/name: redaction-service
        app.kubernetes.io/part-of: semantic-redacted
    spec:
      containers:
        - name: redaction-service
          image: ${REGISTRY}/redaction-service:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 8000
              name: http
          env:
            - name: GEMINI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: redaction-service-secret
                  key: GEMINI_API_KEY
                  optional: true
          volumeMounts:
            - name: config
              mountPath: /config
              readOnly: true
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 2Gi
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 60
            periodSeconds: 15
            timeoutSeconds: 5
      volumes:
        - name: config
          configMap:
            name: redaction-service-config
---
apiVersion: v1
kind: Service
metadata:
  name: redaction-service
  labels:
    app: redaction-service
    app.kubernetes.io/part-of: semantic-redacted
spec:
  selector:
    app: redaction-service
  ports:
    - port: 8000
      targetPort: 8000
      name: http
EOF
echo "Redaction service deployed."

# Step 5: Build and deploy guardrails service
echo ""
echo "--- Step 5: Building Guardrails Service ---"
oc get bc guardrails-service 2>/dev/null || \
  oc new-build --binary --name=guardrails-service --strategy=docker -l app.kubernetes.io/part-of=semantic-redacted

oc start-build guardrails-service --from-dir=src/guardrails-service --follow

cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: guardrails-service
  labels:
    app: guardrails-service
    app.kubernetes.io/name: guardrails-service
    app.kubernetes.io/part-of: semantic-redacted
spec:
  replicas: 1
  selector:
    matchLabels:
      app: guardrails-service
  template:
    metadata:
      labels:
        app: guardrails-service
        app.kubernetes.io/name: guardrails-service
        app.kubernetes.io/part-of: semantic-redacted
    spec:
      containers:
        - name: guardrails-service
          image: ${REGISTRY}/guardrails-service:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 8001
              name: http
          env:
            - name: REDACTION_SERVICE_URL
              value: "http://redaction-service:8000"
            - name: QWEN_ENDPOINT
              value: "http://${QWEN_SVC}"
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: /health
              port: 8001
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 8001
            initialDelaySeconds: 15
            periodSeconds: 15
---
apiVersion: v1
kind: Service
metadata:
  name: guardrails-service
  labels:
    app: guardrails-service
    app.kubernetes.io/part-of: semantic-redacted
spec:
  selector:
    app: guardrails-service
  ports:
    - port: 8001
      targetPort: 8001
      name: http
EOF
echo "Guardrails service deployed."

echo ""
echo "=== Deployment Complete ==="
echo "Services:"
oc get pods -l app.kubernetes.io/part-of=semantic-redacted
echo ""
echo "Run demo: python src/demo/run_demo.py --all"
