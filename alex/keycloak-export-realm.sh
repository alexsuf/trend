#!/bin/bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-keycloak}"
REALM="${REALM:-trend}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-secret}"
KEYCLOAK_HOST="${KEYCLOAK_HOST:-auth.trend-app}"
OUTPUT_DIR="${OUTPUT_DIR:-.}"

POD=$(kubectl get pods -n "$NAMESPACE" -l app=keycloak -o jsonpath='{.items[0].metadata.name}')

if [ -z "$POD" ]; then
    echo "Keycloak pod not found in namespace $NAMESPACE"
    exit 1
fi

echo "Found Keycloak pod: $POD"
echo "Exporting realm '$REALM' to $OUTPUT_DIR/$REALM-realm-export.json"

kubectl exec -n "$NAMESPACE" "$POD" -- /opt/keycloak/bin/kc.sh export \
    --dir /tmp/export \
    --realm "$REALM" \
    --users realm_file \
    --admin-user "$ADMIN_USER" \
    --admin-password "$ADMIN_PASS"

kubectl cp "$NAMESPACE/$POD:/tmp/export/$REALM-realm-export.json" "$OUTPUT_DIR/$REALM-realm-export.json"
kubectl exec -n "$NAMESPACE" "$POD" -- rm -rf /tmp/export

echo "Export saved to $OUTPUT_DIR/$REALM-realm-export.json"
