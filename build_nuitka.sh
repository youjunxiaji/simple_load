#!/bin/bash

# 设置版本号
APP_VERSION="1.0.8"
echo "当前版本号: $APP_VERSION"

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "项目目录: $SCRIPT_DIR"

# 创建输出目录
OUTPUT_DIR="$SCRIPT_DIR/output_nuitka"
mkdir -p "$OUTPUT_DIR"

echo ""
echo "================================"
echo "🚀 开始 Nuitka 编译..."
echo "================================"
echo ""

# 执行 Nuitka 命令
python -m nuitka \
    --standalone \
    --output-dir="$OUTPUT_DIR" \
    --output-filename="simple_load" \
    --include-package=app_simpleLoad \
    --include-package=my_websockets \
    --include-package=uvicorn \
    --include-package=fastapi \
    --include-package=pandas \
    --include-package=polars \
    --include-package=numpy \
    --include-package=loguru \
    --include-package=psutil \
    --enable-plugin=numpy \
    --follow-imports \
    --assume-yes-for-downloads \
    --progress-bar=auto \
    --show-memory \
    "$SCRIPT_DIR/main.py"

if [ $? -ne 0 ]; then
    echo "❌ Nuitka 编译失败"
    exit 1
fi

echo ""
echo "================================"
echo "✅ Nuitka 编译完成！"
echo "输出目录: $OUTPUT_DIR"
# 显示输出文件大小
if [ -d "$OUTPUT_DIR/main.dist" ]; then
    SIZE=$(du -sh "$OUTPUT_DIR/main.dist" | cut -f1)
    echo "输出大小: $SIZE"
fi
echo "================================"

