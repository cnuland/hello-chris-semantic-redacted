#!/bin/bash
# Port-forward all semantic-redacted services for local benchmark access.
# Usage: ./benchmarks/port-forward.sh
# Stop:  Ctrl+C (kills all port-forward processes)

set -euo pipefail

NAMESPACE="semantic-redacted"

echo "Port-forwarding services from namespace: $NAMESPACE"
echo "Press Ctrl+C to stop all port-forwards"
echo ""

oc port-forward -n "$NAMESPACE" svc/redaction-service 8000:8000 &
oc port-forward -n "$NAMESPACE" svc/guardrails-service 8001:8001 &
oc port-forward -n "$NAMESPACE" svc/sensitivity-classifier 8002:8002 &
oc port-forward -n "$NAMESPACE" svc/nemo-egress-guard 8003:8003 &
oc port-forward -n "$NAMESPACE" svc/qdrant 6333:6333 &

echo ""
echo "All port-forwards started:"
echo "  redaction-service:      http://localhost:8000"
echo "  guardrails-service:     http://localhost:8001"
echo "  sensitivity-classifier: http://localhost:8002"
echo "  nemo-egress-guard:      http://localhost:8003"
echo "  qdrant:                 http://localhost:6333"
echo ""

wait
