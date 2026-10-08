@echo off
rem build_app.bat -- build CineRank application (points 8/9/10)
rem   app core: g++ 13.2.0 -O2 -std=c++17, WinSock2, links common/ + adaptsort/ + app/
setlocal
cd /d "%~dp0"
if not exist build mkdir build

echo [1/2] Building CineRank app ...
g++ -O2 -std=c++17 -Wall -D__USE_MINGW_ANSI_STDIO=1 -I common -I cpp\adaptsort -I app app\server.cpp -o build\sort_demo_app.exe -lws2_32
if errorlevel 1 ( echo app build FAILED & exit /b 1 )
echo       build\sort_demo_app.exe OK

echo [2/2] Building CineRank CLI driver (selftest/matrix/extsort/topk without server is same binary)
echo ALL BUILDS OK
endlocal
