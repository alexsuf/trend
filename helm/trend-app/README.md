# Trend App Helm Chart

A Helm chart for deploying the Trend application stack including SearXNG, Keycloak, and Trend services.

## Prerequisites

- Kubernetes 1.20+
- Helm 3.x
- Ingress Controller (nginx)

## Installing the Chart

```bash
helm install trend-app .
```

## Configuration

The following table lists the configurable parameters of the trend-app chart and their default values:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `searxng.replicas` | Number of SearXNG replicas | `1` |
| `keycloak.replicas` | Number of Keycloak replicas | `1` |
| `postgres.replicas` | Number of Postgres replicas | `1` |
| `flask.replicas` | Number of Flask replicas | `1` |
| `worker.replicas` | Number of Worker replicas | `1` |
| `adm.replicas` | Number of ADM replicas | `1` |

## Values

Configuration is managed through subchart values files:
- `charts/searxng/values.yaml` - SearXNG configuration
- `charts/keycloak/values.yaml` - Keycloak and Postgres configuration
- `charts/trend/values.yaml` - Trend application (Flask, Worker, ADM) configuration

Override values with:

```bash
helm install trend-app . -f custom-values.yaml
```

## Subcharts

- **searxng** - SearXNG search engine
- **keycloak** - Keycloak authentication server with Postgres
- **trend** - Trend application (Flask, Worker, ADM)

## Uninstall

```bash
helm uninstall trend-app
```