@echo off
rem prepare_data.bat —— 一键准备全部数据并构建（新队友从这里开始）
rem 用法: prepare_data.bat [代理]   例: prepare_data.bat http://127.0.0.1:7897
rem 代理可选；不传则直连。下载约 1.5GB，支持断点续传（中断后重跑即可）。
cd /d "%~dp0"

if "%~1"=="" (
    python app\pre\download_data.py
) else (
    python app\pre\download_data.py --proxy %1
)
if errorlevel 1 echo [提示] 部分数据源本次未成功（如航班 2-9 月），已到手的场景不受影响，稍后重跑本脚本即可续传。

echo.
echo ==== 预处理：原始数据 -^> JSON 交付物 + int64 排序键 ====
if exist "..\downloads\ml-25m\ml-25m\ratings.csv" python app\preprocess.py "..\downloads\ml-25m" app\data
if exist "..\downloads\ml-25m\ratings.csv" python app\preprocess.py "..\downloads\ml-25m" app\data
python app\pre\pre_amazon.py
python app\pre\pre_bike.py
python app\pre\pre_flight.py "..\downloads\flight"
python app\pre\pre_web.py
python app\pre\pre_gene.py

echo.
echo ==== 构建基准程序与应用 ====
call build.bat
call build_app.bat

echo.
echo ==== 场景计算：PageRank + 后缀数组 ====
if exist app\data\web\postings.dat build\sort_demo_app.exe pagerank app\data\web
if exist app\data\gene\seq.txt build\sort_demo_app.exe sufarr app\data\gene

echo.
echo 全部就绪！运行 build\sort_demo_app.exe server 后浏览器打开 http://127.0.0.1:8080
