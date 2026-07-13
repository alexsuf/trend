# Keycloak Configuration Backup & Restore

## Экспорт конфигурации realm

```bash
cd k8s
export NAMESPACE=keycloak
export REALM=trend
export ADMIN_USER=admin
export ADMIN_PASS=secret
export OUTPUT_DIR=.

bash keycloak-export-realm.sh
```

Результат: `trend-realm-export.json` в текущем каталоге.

## Создание клиента trend-admin

### Вариант A: bash-скрипт (через kcadm.sh в pod)

```bash
cd k8s
export NAMESPACE=keycloak
export REALM=trend
export ADMIN_USER=admin
export ADMIN_PASS=secret
export REDIRECT_URI=https://adm.yourdomain.com/callback

bash keycloak-create-admin-client.sh
```

Скрипт автоматически сгенерирует секрет и выведет его на экран.

### Вариант B: Python-скрипт (через Admin REST API)

```bash
cd k8s
python keycloak_setup_admin_client.py \
    --host auth.trend-app \
    --realm trend \
    --admin-user admin \
    --admin-pass secret \
    --redirect-uri https://adm.yourdomain.com/callback
```

## Восстановление конфигурации на другом кластере

```bash
# 1. Применить манифесты Keycloak
kubectl apply -f keycloak-namespace.yaml
kubectl apply -f keycloak-postgres-pvc.yaml
kubectl apply -f keycloak-postgres-deployment.yaml
kubectl apply -f keycloak-postgres-service.yaml
kubectl apply -f keycloak-deployment.yaml
kubectl apply -f keycloak-service.yaml
kubectl apply -f keycloak-ingress.yaml

# 2. Дождаться запуска
kubectl rollout status deployment/keycloak -n keycloak

# 3. Импортировать realm
kubectl cp trend-realm-export.json keycloak/$(kubectl get pod -n keycloak -l app=keycloak -o jsonpath='{.items[0].metadata.name}'):/tmp/
kubectl exec -n keycloak $(kubectl get pod -n keycloak -l app=keycloak -o jsonpath='{.items[0].metadata.name}') -- \
    /opt/keycloak/bin/kc.sh import --file /tmp/trend-realm-export.json --admin-user admin --admin-password secret

# 4. Создать клиента trend-admin
bash keycloak-create-admin-client.sh

# 5. Назначить роль 'administrator' пользователю (если используется встроенная роль)
# Или создать роль 'admin' через скрипт ниже, если нужно отдельную роль
python keycloak_setup_admin_role.py \
    --host localhost \
    --port 8080 \
    --realm trend \
    --admin-user admin \
    --admin-pass secret \
    --target-user admin \
    --role-name administrator
```
