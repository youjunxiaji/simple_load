@echo off

REM 切换到 UTF-8 编码
chcp 65001

REM 设置版本号环境变量
set "APP_VERSION=v1.1.0"
echo 当前版本号: %APP_VERSION%

REM 获取当前批处理文件所在的目录
set "current_dir=%~dp0"

REM 创建输出目录（如果不存在）
if not exist "%current_dir%output" mkdir "%current_dir%output"

REM 执行PyInstaller命令
pyinstaller ^
    --noconfirm ^
    --onedir ^
    --console ^
    --distpath "%current_dir%output" ^
    --name "simple_load" ^
    --icon "%current_dir%static/配置数据处理.ico" ^
    --add-data "%current_dir%app_simpleLoad;app_simpleLoad/" ^
    --add-data "%current_dir%my_websockets;my_websockets/" ^
    "%current_dir%main.py"

REM 删除 build 文件夹和 .spec 文件
if exist "build" rmdir /s /q "build"
if exist "*.spec" del /f /q "*.spec"

if %errorlevel% neq 0 (
    echo PyInstaller 执行失败，退出脚本
    goto :EOF
)



REM 加密代码
py2pyd -f "%current_dir%output" -d

if %errorlevel% neq 0 (
    echo 代码加密失败，退出脚本
    goto :EOF
)

REM 打包成安装文件
"D:\Inno Setup 6\ISCC.exe" "%current_dir%inno_setup.iss"

if %errorlevel% neq 0 (
    echo Inno Setup 打包失败，退出脚本
    goto :EOF
)

