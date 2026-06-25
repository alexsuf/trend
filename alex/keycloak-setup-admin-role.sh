#!/bin/bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-keycloak}"
REALM="${REALM:-trend}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-secret}"
KEYCLOAK_INTERNAL_HOST="${KEYCLOAK_INTERNAL_HOST:-keycloak.keycloak.svc.cluster.local}"
KEYCLOAK_PORT="${KEYCLOAK_PORT:-8080}"
TARGET_USERNAME="${TARGET_USERNAME:-admin}"

POD=$(kubectl get pods -n "$NAMESPACE" -l app=keycloak -o jsonpath='{.items[0].metadata.name}')

if [ -z "$POD" ]; then
    echo "Keycloak pod not found in namespace $NAMESPACE"
    exit 1
fi

echo "Found Keycloak pod: $POD"

get_token() {
    kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -X POST \
        "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/realms/master/protocol/openid-connect/token" \
        -d "grant_type=password" \
        -d "client_id=admin-cli" \
        -d "username=$ADMIN_USER" \
        -d "password=$ADMIN_PASS" | jq -r '.access_token'
}

TOKEN=$(get_token)
echo "Admin token obtained"

create_role() {
    local role_name="$1"
    local exists
    exists=$(kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -H "Authorization: Bearer $TOKEN" \
        "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/admin/realms/$REALM/roles/$role_name" \
        -o /dev/null -w "%{http_code}")

    if [ "$exists" = "404" ]; then
        echo "Creating realm role '$role_name'..."
        kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -X POST \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/admin/realms/$REALM/roles" \
            -d "{\"name\":\"$role_name\",\"description\":\"Admin role for trend applications\"}"
        echo "Role '$role_name' created."
    else
        echo "Role '$role_name' already exists."
    fi
}

assign_role_to_user() {
    local username="$1"
    local role_name="$2"

    local user_id
    user_id=$(kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -H "Authorization: Bearer $TOKEN" \
        "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/admin/realms/$REALM/users?username=$username" \
        | jq -r '.[0].id // empty')

    if [ -z "$user_id" ]; then
        echo "User '$username' not found in realm '$REALM'"
        return 1
    fi

    local role_id
    role_id=$(kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -H "Authorization: Bearer $TOKEN" \
        "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/admin/realms/$REALM/roles/$role_name" \
        | jq -r '.id')

    local already_has
    already_has=$(kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -H "Authorization: Bearer $TOKEN" \
        "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/admin/realms/$REALM/users/$user_id/role-mappings/realm" \
        | jq --arg rid "$role_id" '[.[] | select(.id == $rid)] | length')

    if [ "$already_has" -gt 0 ]; then
        echo "User '$username' already has role '$role_name'."
        return 0
    fi

    echo "Assigning role '$role_name' to user '$username'..."
    kubectl exec -n "$NAMESPACE" "$POD" -- curl -s -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        "http://$KEYCLOAK_INTERNAL_HOST:$KEYCLOAK_PORT/admin/realms/$REALM/users/$user_id/role-mappings/realm" \
        -d "[{\"id\":\"$role_id\",\"name\":\"$role_name\"}]"
    echo "Role '$role_name' assigned to user '$username'."
}

create_role "admin"
assign_role_to_user "$TARGET_USERNAME" "admin"

echo ""
echo "Done! User '$TARGET_USERNAME' now has 'admin' role in realm '$REALM'."
echo "You may need to re-login in the application."
