@echo off
rem CineRank launcher - run from project root (auto-located via %~dp0)
cd /d "%~dp0"
if not exist build\sort_demo_app.exe (
    echo [ERROR] build\sort_demo_app.exe not found. Run build_app.bat first.
    pause
    exit /b 1
)
echo [CineRank] starting server at http://127.0.0.1:8080  (close this window or Ctrl+C to stop)
build\sort_demo_app.exe server
echo.
echo [CineRank] server exited with code %ERRORLEVEL%.
pause
