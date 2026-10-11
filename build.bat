@echo off
rem build.bat -- build both language versions from the same core sources
rem   C   build: gcc  13.2.0 -O2 -std=c11   (8 algorithms + qsort baseline)
rem   C++ build: g++  13.2.0 -O2 -std=c++17 (8 algorithms + AdaptSort + std::sort baseline)
rem Note: -D__USE_MINGW_ANSI_STDIO=1 makes old MinGW printf handle %lld
setlocal
cd /d "%~dp0"
if not exist build mkdir build

echo [1/2] Building C version ...
gcc -O2 -std=c11 -Wall -D__USE_MINGW_ANSI_STDIO=1 -I common c\main_c.c -o build\sort_demo_c.exe
if errorlevel 1 ( echo C build FAILED & exit /b 1 )
echo       build\sort_demo_c.exe OK

echo [2/2] Building C++ version ...
g++ -O2 -std=c++17 -Wall -I common -I cpp\adaptsort cpp\main_cpp.cpp -o build\sort_demo_cpp.exe
if errorlevel 1 ( echo C++ build FAILED & exit /b 1 )
echo       build\sort_demo_cpp.exe OK

echo ALL BUILDS OK
endlocal
