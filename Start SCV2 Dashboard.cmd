@echo off
setlocal

set "DASHBOARD_EXE=%~dp0SCV2-Dashboard.exe"
set "DASHBOARD_PY=%~dp0.venv\Scripts\python.exe"
set "DASHBOARD_SOURCE=%~dp0src\scv2_dashboard.py"

if exist "%DASHBOARD_EXE%" (
  "%DASHBOARD_EXE%" %*
  exit /b %ERRORLEVEL%
)

if not exist "%DASHBOARD_PY%" (
  echo SCV2-Dashboard.exe and the local Python environment were not found.
  echo See README.md for setup and release download instructions.
  pause
  exit /b 1
)

"%DASHBOARD_PY%" "%DASHBOARD_SOURCE%" %*
set "DASHBOARD_EXIT=%ERRORLEVEL%"

if not "%DASHBOARD_EXIT%"=="0" (
  echo.
  echo The dashboard exited with error code %DASHBOARD_EXIT%.
  echo See README.md for troubleshooting guidance.
  pause
)

exit /b %DASHBOARD_EXIT%
