Минимальный набор для восстановления на другом кластере:

k8s/*.yaml — развертывание инфраструктуры (Keycloak, Postgres, приложения)
k8s/keycloak-export-realm.sh — экспорт realm trend из текущего кластера (даёт trend-realm-export.json)
k8s/keycloak-create-admin-client.sh — создание клиента trend-admin на новом кластере
k8s/KEYCLOAK_SETUP.md — инструкция по восстановлению


На новом кластере нужно будет:
Создать realm trend
Клиенты trend-web и trend-admin
Роли и пользователи