#!/bin/bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-keycloak}"
REALM="${REALM:-trend}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-secret}"
KEYCLOAK_INTERNAL_HOST="${KEYCLOAK_INTERNAL_HOST:-keycloak.keycloak.svc.cluster.local}"
KEYCLOAK_PORT="${KEYCLOAK_PORT:-8080}"

CLIENT_ID="trend-admin"
CLIENT_SECRET="${CLIENT_SECRET:-}"
REDIRECT_URI="${REDIRECT_URI:-}"

if [ -z "$REDIRECT_URI" ]; then
    echo "ERROR: REDIRECT_URI environment variable is required"
    echo "Example: REDIRECT_URI=https://adm.yourdomain.com/callback ./keycloak-create-admin-client.sh"
    exit 1
fi

POD=$(kubectl get pods -n "$NAMESPACE" -l app=keycloak -o jsonpath='{.items[0].metadata.name}')

if [ -z "$POD" ]; then
    echo "Keycloak pod not found in namespace $NAMESPACE"
    exit 1
fi

echo "Found Keycloak pod: $POD"

TOKEN=$(kubectl exec -n "$NAMESPACE" "$POD" -- /opt/keycloak/bin/kcadm.sh config credentials \
    --server "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT" \
    --realm master \
    --user "$ADMIN_USER" \
    --password "$ADMIN_PASS" \
    2>/dev/null | grep -oP 'access_token=\K[^ ]+' || true)

if [ -z "$TOKEN" ]; then
    echo "Failed to obtain admin token"
    exit 1
fi

echo "Admin token obtained"

CLIENT_EXISTS=$(kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -H "Authorization: Bearer $TOKEN" \
    "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/realms/$REALM/clients?clientId=$CLIENT_ID" \
    | grep -o '"[^"]*"' | head -1 | tr -d '"' || true)

if [ -n "$CLIENT_EXISTS" ] && [ "$CLIENT_EXISTS" != "[]" ]; then
    echo "Client '$CLIENT_ID' already exists in realm '$REALM'. Skipping creation."
    echo "To update existing client, manually modify it in Keycloak Admin Console."
    exit 0
fi

if [ -z "$CLIENT_SECRET" ]; then
    CLIENT_SECRET=$(openssl rand -base64 32)
    echo "Generated random client secret"
fi

echo "Creating client '$CLIENT_ID' in realm '$REALM'..."

kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/admin/realms/$REALM/clients" \
    -d '{
        "clientId": "'"$CLIENT_ID"'",
        "name": "Trend Admin Panel",
        "description": "Admin panel for managing LLM models and providers",
        "enabled": true,
        "publicClient": false,
        "protocol": "openid-connect",
        "standardFlowEnabled": true,
        "implicitFlowEnabled": false,
        "directAccessGrantsEnabled": true,
        "serviceAccountsEnabled": false,
        "secret": "'"$CLIENT_SECRET"'",
        "redirectUris": ["'"$REDIRECT_URI"'"],
        "webOrigins": ["+"],
        "attributes": {
            "access.token.lifespan": "3600",
            "client.session.idle.timeout": "1800",
            "client.session.max.lifespan": "86400"
        }
    }'

echo ""
echo "Client '$CLIENT_ID' created successfully!"
echo "Client Secret: $CLIENT_SECRET"
echo ""
echo "Update your adm/config.py with:"
echo "  KEYCLOAK_CLIENT_ID = '$CLIENT_ID'"
echo "  KEYCLOAK_CLIENT_SECRET = '$CLIENT_SECRET'"
