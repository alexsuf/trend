@echo off
setlocal enabledelayedexpansion

set NAMESPACE=trend
set DEPLOYMENT=jupyter-notebook
set POD_DIR=/home/jovyan/work

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
echo Copying files from pod !POD!:%POD_DIR% to "%LOCAL_DIR%" ...

kubectl cp %NAMESPACE%/!POD!:%POD_DIR% %LOCAL_DIR%
if errorlevel 1 (
    echo Failed to copy files from pod
    popd
    pause
    exit /b 1
)

echo Done.
popd
pause
