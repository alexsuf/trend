# Установка приложения на новый кластер Kubernetes (Docker Desktop, Windows)

Эта инструкция описывает перенос приложения (система генерации исследовательских отчётов Trend Research: Flask-приложение, админка, воркер, Keycloak, PostgreSQL, SearXNG) на другой одноузловой кластер Kubernetes под Windows Docker Desktop. Ingress удалён, сервисы переведены на NodePort — доступ через localhost.

## Что входит в приложение

- **3 namespace**: `keycloak`, `search`, `trend`
- **Инфраструктура**:
  - Keycloak 26.3 (`quay.io/keycloak/keycloak:26.3`) в namespace `keycloak`
  - PostgreSQL на кастомном образе `alexsuf/postgres` в namespace `keycloak`
  - SearXNG (`searxng/searxng:latest`) в namespace `search`
  - Веб-приложение (Flask, образ `alexsuf/trend`) — deployment `trend-research` в `trend`
  - Админка (образ `alexsuf/trend-adm`) — deployment `trend-adm` в `trend`
  - Воркер (`alexsuf/trend`, запускает `worker.py`) в `trend`
- **Доступ**: Ingress удалён, сервисы на NodePort (Docker Desktop пробрасывает автоматически):
  - `localhost:30001` — веб-приложение
  - `localhost:30002` — админка
  - `localhost:30003` — Keycloak
  - `localhost:30004` — SearXNG
  - `localhost:30050` — PostgreSQL
- **База данных**: единый PostgreSQL, БД `keycloak` (для Keycloak) и БД `trend` (для приложения, создаётся Job'ом `init-trend-db`).

## ⚠️ Главные подводные камни при переносе на новый кластер

1. **`imagePullPolicy: Never`** у `trend`, `trend-adm`, `postgres`. Образы должны уже лежать локально на ноде. Их надо собрать/затянуть заранее (шаг 2).
2. **PV `hostPath: /data/postgres`** — на Docker Desktop под Windows это путь **внутри WSL2/LinuxKit VM**, а не на диске `C:\`. Нужно создать каталог и выставить права внутри VM (шаг 5).
3. **Ingress удалён** — сервисы на NodePort, доступ через localhost (см. шаг 9).

> **Примечание**: Ingress-манифесты удалены, сервисы переведены на NodePort.
> Весь server-to-server трафик (обмен токенами, admin API Keycloak, PostgreSQL, SearXNG)
> идёт через внутрикластерные DNS-имена (`keycloak.keycloak.svc.cluster.local`,
> `postgres.keycloak.svc.cluster.local`, `searxng.search.svc.cluster.local`).
> Внешний доступ — через NodePort: `localhost:30001` (app), `localhost:30002` (adm), `localhost:30003` (auth), `localhost:30004` (search), `localhost:30050` (postgres).

---

## Шаг 1. Подготовить целевой кластер

В Docker Desktop → Settings → Kubernetes → включите Enable Kubernetes. Дождитесь готовности.

```powershell
kubectl get nodes   # должен быть в статусе Ready
```

## Шаг 2. Собрать / загрузить Docker-образы (локально!)

Так как `imagePullPolicy: Never`, образы должны присутствовать в локальном кэше Docker на этой машине.

```powershell
# Приложение и админка — собираем из исходников:
docker build -t alexsuf/trend ./app/
docker build -t alexsuf/trend-adm ./adm/

# Postgres (кастомный образ с инициализацией) — затяните из реестра:
docker pull alexsuf/postgres

# Публичные образы (для надёжности тоже подготовьте):
docker pull quay.io/keycloak/keycloak:26.3
docker pull searxng/searxng:latest
```

Если `alexsuf/postgres` недоступен в публичном реестре, перенесите его со старого кластера:

```powershell
# На СТАРОМ кластере:
docker save alexsuf/postgres -o postgres.tar

# Перенесите postgres.tar на новую машину и загрузите:
docker load -i postgres.tar
```

## Шаг 3. Создать namespace `trend`

```powershell
kubectl apply -f k8s/keycloak-namespace.yaml
kubectl apply -f k8s/search-namespace.yaml
kubectl apply -f k8s/trend-namespace.yaml
```

## Шаг 4. Подготовить хранилище PostgreSQL

На Docker Desktop путь `/data/postgres` (из `keycloak-postgres-pv.yaml`) находится внутри LinuxKit-VM. Создайте каталог и выставьте права пользователя Postgres (uid 999):

```powershell
# Самый надёжный способ на Docker Desktop — через временный привилегированный pod:
kubectl run fix-pv --image=alpine -n keycloak --restart=Never --command -- sh -c "mkdir -p /data/postgres && chown -R 999:999 /data/postgres"
kubectl delete pod fix-pv -n keycloak
```

Затем примените PV/PVC:

```powershell
kubectl apply -f k8s/keycloak-postgres-pv.yaml
kubectl apply -f k8s/keycloak-postgres-pvc.yaml
```

## Шаг 5. Применить инфраструктурные манифесты (по порядку)

```powershell
kubectl apply -f k8s/keycloak-postgres-deployment.yaml
kubectl apply -f k8s/keycloak-postgres-service.yaml
kubectl apply -f k8s/keycloak-deployment.yaml
kubectl apply -f k8s/keycloak-service.yaml
kubectl apply -f k8s/postgres-nodeport.yaml

kubectl rollout status deployment/keycloak -n keycloak
kubectl rollout status deployment/postgres -n keycloak

kubectl apply -f k8s/searxng-settings.yaml
kubectl apply -f k8s/searxng-deployment.yaml
kubectl apply -f k8s/searxng-service.yaml
```

## Шаг 6. Создать БД `trend` и применить схему (Job)

```powershell
kubectl apply -f k8s/init-trend-db.yaml
kubectl wait --for=condition=complete job/init-trend-db -n trend --timeout=120s
```

Job `init-trend-db` создаёт БД `trend`, пользователя `trend`, и накатывает схему из ConfigMap `trend-db-schema` (таблицы `users`, `research_tasks`, `research_reports`, `llm_*`, `agent_events` и т.д.).

## Шаг 7. Приложение и воркер

```powershell
kubectl apply -f k8s/flask-deployment.yaml
kubectl apply -f k8s/flask-service.yaml

kubectl apply -f k8s/adm-deployment.yaml
kubectl apply -f k8s/adm-service.yaml

kubectl apply -f k8s/worker-deployment.yaml
```

## Шаг 8. Настроить Keycloak (realm, клиенты, роли, пользователь)

Используйте скрипт `llm/setup-keycloak.sh` — он создаёт realm `trend`, клиента `trend-web` с секретом `bbWGIugaSj9ithjybqoNR5hXI9acjEel`, роли `user`/`analyst`/`administrator` и пользователя `alex` / `secret`:

```powershell
bash llm/setup-keycloak.sh
```

Если нужен клиент `trend-admin` (для админки; секрет `ZePGCk9losJjuOtQxBLcHK64RgAF5MfNDaqpnS7b3V` из `adm-deployment.yaml:51`), создайте его через `alex/keycloak-create-admin-client.sh` либо вручную в админке Keycloak (`http://localhost:3003/`, логин `admin`/`secret`).

При восстановлении с экспорта realm со старого кластера см. `alex/KEYCLOAK_SETUP.md` (экспорт `trend-realm-export.json` и импорт через `kc.sh import`).

## Шаг 9. Проверить доступ (NodePort)

Сервисы переведены на NodePort. Docker Desktop автоматически пробрасывает порты на localhost:

| Сервис | Адрес |
|---|---|
| Веб-приложение | `http://localhost:30001/` |
| Админка | `http://localhost:30002/` |
| Keycloak admin | `http://localhost:30003/` (`admin` / `secret`) |
| SearXNG | `http://localhost:30004/` |
| PostgreSQL | `localhost:30050` (trend / secret / trend) |

Проверка:

```powershell
kubectl get pods -n trend
kubectl get pods -n keycloak
kubectl get pods -n search
curl http://localhost:30001/
```

---

## Краткий чек-лист переноса

- [ ] образы `alexsuf/trend`, `alexsuf/trend-adm`, `alexsuf/postgres` собраны/загружены локально
- [ ] namespace `trend` создан (`kubectl apply -f k8s/trend-namespace.yaml`)
- [ ] port-forward запущен для всех сервисов
- [ ] БД и схема накачены (Job `init-trend-db` завершён)
- [ ] Keycloak realm/клиенты/пользователь созданы

## Точки входа и учётные данные

- Веб-приложение: `http://localhost:30001/`
- Админка: `http://localhost:30002/`
- Keycloak admin: `http://localhost:30003/` (`admin` / `secret`)
- SearXNG: `http://localhost:30004/`
- PostgreSQL: `localhost:30050` (`trend` / `secret` / `trend`)
- Пользователь приложения: `alex` / `secret` (роли: user, analyst, administrator)
