@echo off

REM 切换到 UTF-8 编码
chcp 65001

REM 设置版本号环境变量
set "APP_VERSION=1.2.3"
echo 当前版本号: %APP_VERSION%

REM 获取当前批处理文件所在的目录
set "current_dir=%~dp0"

REM 创建输出目录（如果不存在）
if not exist "%current_dir%output" mkdir "%current_dir%output"

REM 清理上一次的编译产物，避免残留
if exist "%current_dir%output\main.dist" rmdir /s /q "%current_dir%output\main.dist"
if exist "%current_dir%output\simple_load" rmdir /s /q "%current_dir%output\simple_load"

REM ============================================================
REM 使用 Nuitka 编译（standalone 单目录模式）
REM Nuitka 直接把 Python 编译为原生 C 代码，自带源码保护，
REM 因此不再需要 PyInstaller + py2pyd(.pyd) 那套加密流程。
REM ============================================================
uv run python -m nuitka ^
    --standalone ^
    --output-dir="%current_dir%output" ^
    --output-filename="simple_load.exe" ^
    --windows-icon-from-ico="%current_dir%static\app_icon.ico" ^
    --company-name="gulei" ^
    --product-name="simple_load" ^
    --product-version=%APP_VERSION% ^
    --file-version=%APP_VERSION% ^
    --file-description="Load Calculator" ^
    --include-package=app_simpleLoad ^
    --include-package=my_websockets ^
    --include-package=fastapi ^
    --include-package=uvicorn ^
    --include-package=websockets ^
    --include-package=anyio ^
    --include-package=rich ^
    --include-package=numpy ^
    --include-package=pandas ^
    --include-package=polars ^
    --include-package=openpyxl ^
    --assume-yes-for-downloads ^
    --remove-output ^
    --progress-bar=auto ^
    --jobs=%NUMBER_OF_PROCESSORS% ^
    "%current_dir%main.py"

if %errorlevel% neq 0 (
    echo Nuitka 编译失败，退出脚本
    goto :EOF
)

REM 将 Nuitka 默认产物目录 main.dist 重命名为 simple_load
if exist "%current_dir%output\main.dist" (
    move /y "%current_dir%output\main.dist" "%current_dir%output\simple_load"
)

if %errorlevel% neq 0 (
    echo 重命名输出目录失败，退出脚本
    goto :EOF
)

REM 打包成安装文件
"D:\Inno Setup 6\ISCC.exe" "%current_dir%inno_setup.iss"

if %errorlevel% neq 0 (
    echo Inno Setup 打包失败，退出脚本
    goto :EOF
)
