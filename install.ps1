<#
    install.ps1 — Автоматическая установка Trend Research на новую машину
    (Windows + Docker Desktop с включённым Kubernetes)

    Проходит все шаги из install.md:
      1. Проверка предусловий (Docker, kubectl, git, права админа)
      2. Сборка/загрузка Docker-образов (imagePullPolicy: Never — нужны локально)
      3. Создание namespace'ов
      4. Подготовка hostPath PV (/data/postgres внутри VM Docker Desktop)
      5. Применение инфраструктурных манифестов (Keycloak, Postgres, SearXNG)
      6. Инициализация БД trend + схемы (Job init-trend-db)
      7. Применение приложения, админки и воркера
      8. Настройка Keycloak (realm, клиенты, роли, пользователь)
      9. Вывод точек входа (NodePort)

    Запуск (от имени Администратора, в корне репозитория):
      powershell -ExecutionPolicy Bypass -File install.ps1

    Полезные ключи:
      -SkipImageBuild          не собирать/тянуть образы (уже есть локально)
      -SkipKeycloakSetup       не настраивать realm/клиентов/пользователя
      -SetupAdminClient        также создать клиента trend-admin для админки
#>

[CmdletBinding()]
param(
    [switch]$SkipImageBuild,
    [switch]$SkipKeycloakSetup,
    [switch]$SetupAdminClient,
    [switch]$SkipHosts
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
$RepoRoot = $PSScriptRoot
$K8sDir   = Join-Path $RepoRoot 'k8s'
$LlmDir   = Join-Path $RepoRoot 'llm'
$AppDir   = Join-Path $RepoRoot 'app'
$AdmDir   = Join-Path $RepoRoot 'adm'

$KeycloakNamespace = 'keycloak'
$SearchNamespace   = 'search'
$TrendNamespace    = 'trend'

# Keycloak defaults (совпадают с k8s-манифестами и setup-keycloak.sh)
$KCAdminUser     = 'admin'
$KCAdminPassword = 'secret'
$Realm           = 'trend'
$WebClientId     = 'trend-web'
$WebClientSecret = 'bbWGIugaSj9ithjybqoNR5hXI9acjEel'
$AdminClientId   = 'trend-admin'
$AdminClientSecret = 'ZePGCk9losJjuOtQxBLcHK64RgAF5MfNDaqpnS7b3V'
$AppUser         = 'alex'
$AppPassword     = 'secret'

$HostsEntries = @(
    '127.0.0.1 web.trend-app'
    '127.0.0.1 auth.trend-app'
    '127.0.0.1 adm.trend-app'
    '127.0.0.1 search.trend-app'
)

# Port-forward настройки (для доступа через localhost:PORT)
$PortForwards = @(
    @{Service='trend-research'; Namespace='trend'; LocalPort=3001; ServicePort=80; Description='Веб-приложение'}
    @{Service='trend-adm';      Namespace='trend';    LocalPort=3002; ServicePort=80; Description='Админка'}
    @{Service='keycloak';       Namespace='keycloak'; LocalPort=3003; ServicePort=80; Description='Keycloak (auth)'}
    @{Service='searxng';        Namespace='search';   LocalPort=3004; ServicePort=80; Description='SearXNG (поиск)'}
)

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
function Write-Step($n, $text) {
    Write-Host "`n================================================================" -ForegroundColor Cyan
    Write-Host "  Шаг $n. $text" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan
}

function Test-Command($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Error "Команда '$name' не найдена в PATH. Установите её и повторите запуск."
    }
}

function Invoke-Kubectl {
    & kubectl @args
    if ($LASTEXITCODE -ne 0) { throw "kubectl завершился с кодом $LASTEXITCODE: $args" }
}

function Wait-Rollout($ns, $deploy) {
    Write-Host "  Ожидание rollout $deploy в namespace $ns ..." -ForegroundColor Gray
    Invoke-Kubectl rollout status deployment/$deploy -n $ns --timeout=300s
}

function Wait-PodReady($ns, $label) {
    Write-Host "  Ожидание готовности pod ($label) в namespace $ns ..." -ForegroundColor Gray
    Invoke-Kubectl wait --for=condition=ready pod -l $label -n $ns --timeout=600s
}

function Wait-KeycloakReady($pod) {
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        $out = & kubectl exec -n $KeycloakNamespace $pod -- curl -s http://localhost:8080/realms/master 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $ready = $true; break }
        Write-Host "  Keycloak ещё не готов, ждём 5с..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { throw "Keycloak не поднялся за отведенное время." }
}

function Add-HostsEntry {
    param([string]$Entry)
    $hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
    $ip, $name = $Entry -split '\s+'
    $content = Get-Content $hosts -ErrorAction Stop
    $exists = $content | Where-Object { $_ -match "^\s*$ip\s+$name\s*$" }
    if ($exists) {
        Write-Host "  Запись '$Entry' уже есть в hosts." -ForegroundColor Gray
        return
    }
    Add-Content -Path $hosts -Value "`n$Entry" -Encoding ASCII
    Write-Host "  Добавлено в hosts: $Entry" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Начало
# ---------------------------------------------------------------------------
Write-Host @"

  ████████╗███████╗██╗    ██╗██╗
  ╚══██╔══╝██╔════╝██║    ██║██║
     ██║   █████╗  ██║ █╗ ██║██║
     ██║   ██╔══╝  ██║███╗██║██║
     ██║   ███████╗╚███╔███╔╝██║
     ╚═╝   ╚══════╝ ╚══╝╚══╝ ╚═╝
     Автоустановка на Windows + Docker Desktop (Kubernetes)
"@ -ForegroundColor Magenta

# ---------------------------------------------------------------------------
# Шаг 1. Проверка предусловий
# ---------------------------------------------------------------------------
Write-Step 1 "Проверка предусловий"
Test-Command docker
Test-Command kubectl
Test-Command git

# Права администратора (нужны для правки hosts и управления Docker)
$isAdmin = ([Security.Principal.WindowsPrincipal]`
    [Security.Principal.WindowsIdentity]::GetCurrent()`
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning "Скрипт запущен НЕ от имени Администратора. Правка hosts и некоторые операции могут не сработать."
}

# Docker запущен?
Write-Host "  Проверка Docker..." -ForegroundColor Gray
& docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) { throw "Docker не запущен. Запустите Docker Desktop и включите Kubernetes." }

# Kubernetes включён?
Write-Host "  Проверка Kubernetes в Docker Desktop..." -ForegroundColor Gray
& kubectl cluster-info > $null 2>&1
if ($LASTEXITCODE -ne 0) { throw "Kubernetes недоступен. В Docker Desktop → Settings → Kubernetes включите 'Enable Kubernetes' и дождитесь готовности." }

$ctx = & kubectl config current-context
Write-Host "  Текущий контекст kubectl: $ctx" -ForegroundColor Green
if ($ctx -notmatch 'docker-desktop') {
    Write-Warning "Контекст не похож на docker-desktop ($ctx). Убедитесь, что выбран кластер Docker Desktop."
}

Write-Host "  Предусловия выполнены." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Шаг 2. Образы (imagePullPolicy: Never — должны быть локально)
# ---------------------------------------------------------------------------
if ($SkipImageBuild) {
    Write-Host "`n[пропущено] Сборка/загрузка образов (-SkipImageBuild)" -ForegroundColor Yellow
} else {
    Write-Step 2 "Сборка и загрузка Docker-образов"
    Write-Host "  Сборка alexsuf/trend (./app)..." -ForegroundColor Gray
    & docker build -t alexsuf/trend $AppDir
    if ($LASTEXITCODE -ne 0) { throw "Сборка alexsuf/trend не удалась." }

    Write-Host "  Сборка alexsuf/trend-adm (./adm)..." -ForegroundColor Gray
    if (Test-Path $AdmDir) {
        & docker build -t alexsuf/trend-adm $AdmDir
        if ($LASTEXITCODE -ne 0) { throw "Сборка alexsuf/trend-adm не удалась." }
    } else {
        Write-Warning "Каталог ./adm не найден — образ alexsuf/trend-adm не собран."
    }

    Write-Host "  Загрузка alexsuf/postgres..." -ForegroundColor Gray
    & docker pull alexsuf/postgres
    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("alexsuf/postgres недоступен в реестре. " +
            "Перенесите его со старого кластера: docker save alexsuf/postgres -o postgres.tar / docker load -i postgres.tar")
    }

    Write-Host "  Загрузка публичных образов..." -ForegroundColor Gray
    & docker pull quay.io/keycloak/keycloak:26.3
    & docker pull searxng/searxng:latest
    Write-Host "  Образы подготовлены." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Шаг 3. Namespace'ы
# ---------------------------------------------------------------------------
Write-Step 3 "Создание namespace'ов"
Invoke-Kubectl apply -f (Join-Path $K8sDir 'keycloak-namespace.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'search-namespace.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'trend-namespace.yaml')
Write-Host "  Namespace'ы созданы." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Шаг 4. Хранилище PostgreSQL (hostPath внутри VM)
# ---------------------------------------------------------------------------
Write-Step 4 "Подготовка хранилища PostgreSQL (/data/postgres)"
Write-Host "  Создание каталога и выставление прав uid:gid 999 через временный pod..." -ForegroundColor Gray
& kubectl run fix-pv --image=alpine -n $KeycloakNamespace --restart=Never --command -- sh -c "mkdir -p /data/postgres && chown -R 999:999 /data/postgres" 2>$null
& kubectl wait --for=condition=complete pod/fix-pv -n $KeycloakNamespace --timeout=120s 2>$null
& kubectl delete pod fix-pv -n $KeycloakNamespace 2>$null

Invoke-Kubectl apply -f (Join-Path $K8sDir 'keycloak-postgres-pv.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'keycloak-postgres-pvc.yaml')
Write-Host "  PV/PVC применены." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Шаг 5. Инфраструктура (Keycloak, Postgres, SearXNG)
# ---------------------------------------------------------------------------
Write-Step 5 "Применение инфраструктурных манифестов"
Invoke-Kubectl apply -f (Join-Path $K8sDir 'keycloak-postgres-deployment.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'keycloak-postgres-service.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'keycloak-deployment.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'keycloak-service.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'postgres-nodeport.yaml')

Wait-Rollout $KeycloakNamespace keycloak
Wait-Rollout $KeycloakNamespace postgres

Invoke-Kubectl apply -f (Join-Path $K8sDir 'searxng-settings.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'searxng-deployment.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'searxng-service.yaml')
Write-Host "  Инфраструктура развёрнута." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Шаг 6. БД trend + схема (Job)
# ---------------------------------------------------------------------------
Write-Step 6 "Создание БД trend и применение схемы (Job init-trend-db)"
Invoke-Kubectl apply -f (Join-Path $K8sDir 'init-trend-db.yaml')
& kubectl wait --for=condition=complete job/init-trend-db -n $TrendNamespace --timeout=120s
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Job init-trend-db не завершился успешно. Проверьте логи: kubectl logs -n $TrendNamespace job/init-trend-db"
} else {
    Write-Host "  БД trend и схема накачены." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Шаг 7. Приложение, админка, воркер
# ---------------------------------------------------------------------------
Write-Step 7 "Применение приложения, админки и воркера"
Invoke-Kubectl apply -f (Join-Path $K8sDir 'flask-deployment.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'flask-service.yaml')

Invoke-Kubectl apply -f (Join-Path $K8sDir 'adm-deployment.yaml')
Invoke-Kubectl apply -f (Join-Path $K8sDir 'adm-service.yaml')

Invoke-Kubectl apply -f (Join-Path $K8sDir 'worker-deployment.yaml')

Wait-Rollout $TrendNamespace trend-research
Write-Host "  Приложение развёрнуто." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Шаг 8. Настройка Keycloak
# ---------------------------------------------------------------------------
if ($SkipKeycloakSetup) {
    Write-Host "`n[пропущено] Настройка Keycloak (-SkipKeycloakSetup)" -ForegroundColor Yellow
} else {
    Write-Step 8 "Настройка Keycloak (realm, клиенты, роли, пользователь)"

    $kcPod = & kubectl get pods -n $KeycloakNamespace -l app=keycloak -o jsonpath='{.items[0].metadata.name}'
    if (-not $kcPod) { throw "Не найден pod Keycloak (label app=keycloak)." }
    Write-Host "  Keycloak pod: $kcPod" -ForegroundColor Gray

    Wait-KeycloakReady $kcPod

    function Invoke-Kcadm {
        param([string[]]$Args, [switch]$IgnoreErrors)
        & kubectl exec -n $KeycloakNamespace $kcPod -- /opt/keycloak/bin/kcadm.sh @Args 2>$null
        if ($LASTEXITCODE -ne 0 -and -not $IgnoreErrors) {
            Write-Warning "kcadm команда не удалась: $Args"
        }
    }

    $kcBase = @('--server', 'http://localhost:8080', '--realm', 'master',
                '--user', $KCAdminUser, '--password', $KCAdminPassword)

    # Realm
    Invoke-Kcadm -Args (@('create', 'realms', '-s', "realm=$Realm", '-s', 'enabled=true') + $kcBase) -IgnoreErrors

    # Roles
    foreach ($role in @('user', 'analyst', 'administrator')) {
        Invoke-Kcadm -Args (@('create', 'roles', '-r', $Realm, '-s', "name=$role") + $kcBase) -IgnoreErrors
    }

    # Web client
    Invoke-Kcadm -Args (@('create', 'clients', '-r', $Realm, '-s', "clientId=$WebClientId",
        '-s', "secret=$WebClientSecret", '-s', 'enabled=true') + $kcBase) -IgnoreErrors

    $clientIdVal = (& kubectl exec -n $KeycloakNamespace $kcPod -- /opt/keycloak/bin/kcadm.sh get clients -r $Realm @kcBase 2>$null) `
        | Select-String '"id" : "([^"]*)"' | Select-Object -First 1
    if ($clientIdVal) {
        $clientIdVal = $clientIdVal.Matches.Groups[1].Value
        Invoke-Kcadm -Args (@('update', "clients/$clientIdVal", '-r', $Realm,
            '-s', 'redirectUris=["http://localhost:30001/*","http://localhost:30002/*","http://localhost:30003/*"]',
            '-s', 'webOrigins=["http://localhost:30001","http://localhost:30002","http://localhost:30003"]',
            '-s', 'standardFlowEnabled=true', '-s', 'directAccessGrantsEnabled=true',
            '-s', 'publicClient=true', '-s', 'enabled=true') + $kcBase) -IgnoreErrors
    }

    # Admin client (опционально, для админки)
    if ($SetupAdminClient) {
        Invoke-Kcadm -Args (@('create', 'clients', '-r', $Realm, '-s', "clientId=$AdminClientId",
            '-s', "secret=$AdminClientSecret", '-s', 'enabled=true', '-s', 'publicClient=true',
            '-s', 'standardFlowEnabled=true', '-s', 'directAccessGrantsEnabled=true') + $kcBase) -IgnoreErrors
        Write-Host "  Создан клиент админки: $AdminClientId" -ForegroundColor Gray
    }

    # User
    Invoke-Kcadm -Args (@('create', 'users', '-r', $Realm, '-s', "username=$AppUser",
        '-s', 'enabled=true', '-s', 'firstName=Alex', '-s', 'lastName=User',
        '-s', 'email=alex@example.com') + $kcBase) -IgnoreErrors
    & kubectl exec -n $KeycloakNamespace $kcPod -- /opt/keycloak/bin/kcadm.sh set-password -r $Realm `
        --username $AppUser --new-password $AppPassword @kcBase 2>$null
    Invoke-Kcadm -Args (@('add-roles', '-r', $Realm, '--uusername', $AppUser,
        '--rolename', 'user', '--rolename', 'analyst', '--rolename', 'administrator') + $kcBase) -IgnoreErrors

    Write-Host "  Keycloak настроен." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Шаг 9. Точки входа (NodePort)
# ---------------------------------------------------------------------------
if (-not $SkipHosts) {
    Write-Step 9 "Точки входа NodePort"

    Write-Host @"
  Сервисы переведены на NodePort — port-forward не требуется.
  Доступ через localhost (Docker Desktop пробрасывает порты автоматически):

    http://localhost:30001/  — веб-приложение (app)
    http://localhost:30002/  — админка (adm)
    http://localhost:30003/  — Keycloak (auth)
    http://localhost:30004/  — SearXNG (search)
    localhost:30050          — PostgreSQL (trend / secret / trend)
"@ -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# Финал + проверка
# ---------------------------------------------------------------------------
Write-Step "ФИНАЛ" "Проверка состояния"
& kubectl get pods -n $TrendNamespace
& kubectl get pods -n $KeycloakNamespace
& kubectl get pods -n $SearchNamespace

Write-Host @"

------------------------------------------------------------------
  Установка завершена.
  Сервисы на NodePort — доступ через localhost:

    Веб-приложение : http://localhost:30001/
    Админка        : http://localhost:30002/
    Keycloak admin : http://localhost:30003/  (admin / secret)
    SearXNG        : http://localhost:30004/
    PostgreSQL     : localhost:30050          (trend / secret / trend)
  Пользователь     : $AppUser / $AppPassword  (роли: user, analyst, administrator)
------------------------------------------------------------------
"@ -ForegroundColor Green
