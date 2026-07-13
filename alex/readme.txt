C:\Windows\System32\drivers\etc\hosts (run as Administrator):

На ubuntu нужно установить presistent volume:
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml


Минимальный набор для восстановления на другом кластере:

k8s/*.yaml — развертывание инфраструктуры (Keycloak, Postgres, приложения)
k8s/keycloak-export-realm.sh — экспорт realm trend из текущего кластера (даёт trend-realm-export.json)
k8s/keycloak-create-admin-client.sh — создание клиента trend-admin на новом кластере
k8s/KEYCLOAK_SETUP.md — инструкция по восстановлению


На новом кластере нужно будет:
Создать realm trend
Клиенты trend-web и trend-admin
Роли и пользователи

Удалить весь кластер:
kubectl delete namespace trend keycloak search

127.0.0.1 web.trend-app
127.0.0.1 auth.trend-app
127.0.0.1 adm.trend-app
212.233.94.233 alex
212.233.93.103 az-kube
