Write-Host @"

  Trend Research — точки входа (NodePort)
  ========================================
    Веб-приложение : http://localhost:30001/
    Админка        : http://localhost:30002/
    Keycloak       : http://localhost:30003/
    SearXNG        : http://localhost:30004/
    PostgreSQL     : localhost:30050 (trend / secret / trend)

  Port-forward больше не нужен — сервисы переведены на NodePort.
"@ -ForegroundColor Cyan