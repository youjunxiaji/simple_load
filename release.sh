#!/usr/bin/env bash
set -euo pipefail

# 切到脚本所在目录（仓库根），保证相对路径与 git 命令正确
cd "$(cd "$(dirname "$0")" && pwd)"

# ─── 版本号所在的文件（单一事实来源：pyproject.toml）──────────
PYPROJECT="pyproject.toml"
BUILD_BAT="build.bat"
MAIN_PY="main.py"
README="README.md"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

# ─── 读取当前版本（以 pyproject.toml 的 version 为准）─────────
current_version=$(sed -n 's/^version = "\([^"]*\)".*/\1/p' "$PYPROJECT" | head -1)
if [ -z "$current_version" ]; then
    echo -e "  ${RED}✘${NC} 无法从 $PYPROJECT 读取当前版本号"
    exit 1
fi
IFS='.' read -r major minor patch <<< "$current_version"

versions=("$((major + 1)).0.0" "${major}.$((minor + 1)).0" "${major}.${minor}.$((patch + 1))")

ARROW_RESULT=0

# ─── 方向键选择菜单（↑↓ 选择，Enter 确认）────────────────────
arrow_select() {
    local selected=$1
    shift
    local items=("$@")
    local count=${#items[@]}

    tput civis 2>/dev/null || true

    local i
    for ((i = 0; i < count; i++)); do
        if [ $i -eq $selected ]; then
            printf "  \033[0;32m❯\033[0m %s\n" "${items[$i]}"
        else
            printf "    \033[2m%s\033[0m\n" "${items[$i]}"
        fi
    done

    while true; do
        read -rsn1 key
        if [ "$key" = $'\x1b' ]; then
            read -rsn2 key
            if [ "$key" = "[A" ] && [ $selected -gt 0 ]; then
                selected=$((selected - 1))
            elif [ "$key" = "[B" ] && [ $selected -lt $((count - 1)) ]; then
                selected=$((selected + 1))
            fi
        elif [ "$key" = "" ]; then
            break
        fi

        printf '\033[%dA' "$count"

        for ((i = 0; i < count; i++)); do
            printf '\033[2K'
            if [ $i -eq $selected ]; then
                printf "  \033[0;32m❯\033[0m %s\n" "${items[$i]}"
            else
                printf "    \033[2m%s\033[0m\n" "${items[$i]}"
            fi
        done
    done

    tput cnorm 2>/dev/null || true
    ARROW_RESULT=$selected
}

echo ""
echo -e "  ${BOLD}simple_load Release${NC}"
echo ""
echo -e "  当前版本  ${CYAN}${current_version}${NC}"
echo ""

# ─── 发布前检查：工作区是否干净 ──────────────────────────────
if [ -n "$(git status --porcelain)" ]; then
    echo -e "  ${YELLOW}⚠ 工作区有未提交的改动，发布前请先处理：${NC}"
    echo ""
    git status --short | sed 's/^/    /'
    echo ""
    exit 1
fi

echo -e "  ${BOLD}选择新版本号${NC}  ${DIM}↑↓ 选择, Enter 确认${NC}"
echo ""

arrow_select 2 \
    "${versions[0]}  (major)" \
    "${versions[1]}  (minor)" \
    "${versions[2]}  (patch)"
new_version="${versions[$ARROW_RESULT]}"

echo ""
echo -e "  ${BOLD}确认发布?${NC}  ${CYAN}v${new_version}${NC}  ${DIM}(将修改版本号、提交、打 tag 并推送触发 CI 打包)${NC}"
echo ""

arrow_select 0 "是" "否"

if [ $ARROW_RESULT -ne 0 ]; then
    echo ""
    echo -e "  ${YELLOW}已取消${NC}"
    echo ""
    exit 0
fi

echo ""

# ─── 1. 同步修改所有出现版本号的位置 ─────────────────────────
# pyproject.toml:  version = "x.y.z"
sed -i -E "s/^version = \"[^\"]+\"/version = \"${new_version}\"/" "$PYPROJECT"
# build.bat:       set "APP_VERSION=x.y.z"
sed -i -E "s/(APP_VERSION=)[0-9]+\.[0-9]+\.[0-9]+/\1${new_version}/" "$BUILD_BAT"
# main.py 启动横幅: add_row("版本", "vx.y.z")
sed -i -E "s/(add_row\(\"版本\", \"v)[0-9]+\.[0-9]+\.[0-9]+/\1${new_version}/" "$MAIN_PY"
# README.md:       **版本**: vx.y.z
sed -i -E "s/(\*\*版本\*\*: v)[0-9]+\.[0-9]+\.[0-9]+/\1${new_version}/" "$README"
echo -e "  ${GREEN}✔${NC} 已更新版本号 ${DIM}(pyproject / build.bat / main.py / README)${NC}"

# ─── 2. 同步 uv.lock 里的项目版本 ────────────────────────────
uv lock --quiet
echo -e "  ${GREEN}✔${NC} 已同步 uv.lock"

# ─── 校验：确认 4 处都已是新版本号 ───────────────────────────
if grep -rq "${current_version}" "$PYPROJECT" "$BUILD_BAT" "$README" 2>/dev/null; then
    echo -e "  ${YELLOW}⚠ 仍有文件残留旧版本号 ${current_version}，请检查后再继续${NC}"
    grep -rn "${current_version}" "$PYPROJECT" "$BUILD_BAT" "$README" | sed 's/^/    /'
    exit 1
fi

# ─── 3. 提交 ─────────────────────────────────────────────────
git add "$PYPROJECT" "$BUILD_BAT" "$MAIN_PY" "$README" uv.lock
git commit -m "chore: bump version to ${new_version}" --quiet
echo -e "  ${GREEN}✔${NC} 已提交"

# ─── 4. 打 tag ───────────────────────────────────────────────
git tag "v${new_version}"
echo -e "  ${GREEN}✔${NC} 已创建 tag ${CYAN}v${new_version}${NC}"

# ─── 5. 推送（推送 tag 会触发 GitHub Actions 打包发布）───────
git push --quiet && git push --tags --quiet
echo -e "  ${GREEN}✔${NC} 已推送"

echo ""
echo -e "  ${GREEN}${BOLD}发布完成${NC}  ${CYAN}v${new_version}${NC}  ${DIM}CI 正在构建安装包...${NC}"
echo -e "  ${DIM}查看进度: gh run watch  或  https://github.com/youjunxiaji/simple_load/actions${NC}"
echo ""
