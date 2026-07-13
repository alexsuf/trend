# Дамп DDL из работающей базы (если нет pg_dump)
kubectl exec -n keycloak deploy/postgres -- bash -c "
PGPASSWORD=secret pg_dump -U keycloak -h localhost -d trend --schema-only --no-owner --no-acl
" > trend-schema-dump.sql

# Или применить готовый DDL в пустую БД:
kubectl exec -n keycloak deploy/postgres -- bash -c "
PGPASSWORD=secret psql -U keycloak -h localhost -d trend
" < sql/001-init-schema.sql

# Для внешнего PostgreSQL:
PGPASSWORD=secret psql -h pg-host -U keycloak -d trend < sql/001-init-schema.sql

Обязательно выполнить применение keycloak-postgres-pv.yaml
sudo chown -R 999:999 /data/postgres

Очистка kubernets:
kubectl delete namespace keycloak
kubectl delete namespace search
kubectl delete namespace trend

kubectl delete pv postgres-pv
sudo rm -rf /data/postgres