@echo off
setlocal enabledelayedexpansion

set NAMESPACE=trend
set DEPLOYMENT=jupyter-notebook
set POD_DIR=/home/jovyan/work
set PORT=8888

pushd %~dp0
set "LOCAL_DIR=."

echo Looking for jupyter pod...
for /f "delims=" %%a in ('kubectl get pods -n %NAMESPACE% -l app^=%DEPLOYMENT% --no-headers -o name') do (
    set POD=%%a
    goto :found
)
echo No jupyter pod found in namespace %NAMESPACE%
popd
pause
exit /b 1

:found
set POD=!POD:~4!
echo Found pod: !POD!
echo.

echo [1/2] Copying local files from "%LOCAL_DIR%" to pod !POD!:%POD_DIR% ...
kubectl cp %LOCAL_DIR% %NAMESPACE%/!POD!:%POD_DIR%
if errorlevel 1 (
    echo Failed to copy files to pod
    popd
    pause
    exit /b 1
)

echo.
echo [2/2] Starting port-forward on http://localhost:%PORT%/lab?token=debug-token
echo       Press Ctrl+C to stop.
echo.

kubectl port-forward deployment/%DEPLOYMENT% -n %NAMESPACE% %PORT%:%PORT%

echo.
echo Port-forward stopped.
popd
pause
