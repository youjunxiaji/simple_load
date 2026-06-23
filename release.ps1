#!/usr/bin/env pwsh
#Requires -Version 7.0
$ErrorActionPreference = 'Stop'

# 切到脚本所在目录（仓库根），保证相对路径与 git 命令正确
Set-Location $PSScriptRoot

# ─── 版本号所在的文件（单一事实来源：pyproject.toml）──────────
$Pyproject = 'pyproject.toml'
$BuildBat  = 'build.bat'
$MainPy    = 'main.py'
$Readme    = 'README.md'

# ─── ANSI 颜色 ───────────────────────────────────────────────
$e = [char]27
$GREEN = "$e[0;32m"; $CYAN = "$e[0;36m"; $YELLOW = "$e[1;33m"
$RED = "$e[0;31m"; $DIM = "$e[2m"; $BOLD = "$e[1m"; $NC = "$e[0m"

# ─── 方向键选择菜单（↑↓ 选择，Enter 确认），返回选中索引 ──────
function Invoke-ArrowSelect {
    param([int]$Selected, [string[]]$Items)
    $count = $Items.Count

    try { [Console]::CursorVisible = $false } catch {}

    $render = {
        for ($i = 0; $i -lt $count; $i++) {
            if ($i -eq $Selected) { Write-Host "  $GREEN❯$NC $($Items[$i])" }
            else { Write-Host "    $DIM$($Items[$i])$NC" }
        }
    }
    & $render

    while ($true) {
        $key = [Console]::ReadKey($true)
        if ($key.Key -eq 'UpArrow' -and $Selected -gt 0) { $Selected-- }
        elseif ($key.Key -eq 'DownArrow' -and $Selected -lt ($count - 1)) { $Selected++ }
        elseif ($key.Key -eq 'Enter') { break }
        else { continue }

        # 光标上移 count 行，逐行清除并重绘
        Write-Host "$e[${count}A" -NoNewline
        for ($i = 0; $i -lt $count; $i++) {
            Write-Host "$e[2K" -NoNewline
            if ($i -eq $Selected) { Write-Host "  $GREEN❯$NC $($Items[$i])" }
            else { Write-Host "    $DIM$($Items[$i])$NC" }
        }
    }

    try { [Console]::CursorVisible = $true } catch {}
    return $Selected
}

# ─── 读取当前版本（以 pyproject.toml 的 version 为准）─────────
$content = Get-Content -Raw $Pyproject
if ($content -notmatch '(?m)^version = "([^"]+)"') {
    Write-Host "  $RED✘$NC 无法从 $Pyproject 读取当前版本号"
    exit 1
}
$current = $Matches[1]
$parts = $current.Split('.')
$major = [int]$parts[0]; $minor = [int]$parts[1]; $patch = [int]$parts[2]

$versions = @(
    "$($major + 1).0.0"
    "$major.$($minor + 1).0"
    "$major.$minor.$($patch + 1)"
)

Write-Host ""
Write-Host "  ${BOLD}simple_load Release$NC"
Write-Host ""
Write-Host "  当前版本  $CYAN$current$NC"
Write-Host ""

# ─── 发布前检查：工作区是否干净 ──────────────────────────────
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "  $YELLOW⚠ 工作区有未提交的改动，发布前请先处理：$NC"
    Write-Host ""
    $dirty | ForEach-Object { Write-Host "    $_" }
    Write-Host ""
    exit 1
}

Write-Host "  ${BOLD}选择新版本号$NC  ${DIM}↑↓ 选择, Enter 确认$NC"
Write-Host ""

$idx = Invoke-ArrowSelect -Selected 2 -Items @(
    "$($versions[0])  (major)"
    "$($versions[1])  (minor)"
    "$($versions[2])  (patch)"
)
$new = $versions[$idx]

Write-Host ""
Write-Host "  ${BOLD}确认发布?$NC  ${CYAN}v$new$NC  ${DIM}(将修改版本号、提交、打 tag 并推送触发 CI 打包)$NC"
Write-Host ""

$confirm = Invoke-ArrowSelect -Selected 0 -Items @('是', '否')
if ($confirm -ne 0) {
    Write-Host ""
    Write-Host "  $YELLOW已取消$NC"
    Write-Host ""
    exit 0
}

Write-Host ""

# ─── 1. 同步修改所有出现版本号的位置 ─────────────────────────
function Update-File {
    param([string]$Path, [string]$Pattern, [string]$Replacement)
    $c = Get-Content -Raw $Path
    $c = $c -replace $Pattern, $Replacement
    Set-Content -Path $Path -Value $c -NoNewline -Encoding utf8NoBOM
}

# pyproject.toml:  version = "x.y.z"
Update-File $Pyproject '(?m)^version = "[^"]+"' "version = `"$new`""
# build.bat:       set "APP_VERSION=x.y.z"
Update-File $BuildBat  'set "APP_VERSION=[0-9]+\.[0-9]+\.[0-9]+"' "set `"APP_VERSION=$new`""
# main.py 启动横幅: add_row("版本", "vx.y.z")
Update-File $MainPy    'add_row\("版本", "v[0-9]+\.[0-9]+\.[0-9]+"\)' "add_row(`"版本`", `"v$new`")"
# README.md:       **版本**: vx.y.z
Update-File $Readme    '\*\*版本\*\*: v[0-9]+\.[0-9]+\.[0-9]+' "**版本**: v$new"
Write-Host "  $GREEN✔$NC 已更新版本号 ${DIM}(pyproject / build.bat / main.py / README)$NC"

# ─── 2. 同步 uv.lock 里的项目版本 ────────────────────────────
uv lock --quiet
Write-Host "  $GREEN✔$NC 已同步 uv.lock"

# ─── 校验：确认不再残留旧版本号 ──────────────────────────────
$leftover = Select-String -Path $Pyproject, $BuildBat, $Readme -SimpleMatch -Pattern $current
if ($leftover) {
    Write-Host "  $YELLOW⚠ 仍有文件残留旧版本号 $current，请检查后再继续$NC"
    $leftover | ForEach-Object { Write-Host "    $($_.Path):$($_.LineNumber)" }
    exit 1
}

# ─── 3. 提交 ─────────────────────────────────────────────────
git add $Pyproject $BuildBat $MainPy $Readme uv.lock
git commit -m "chore: bump version to $new" --quiet
Write-Host "  $GREEN✔$NC 已提交"

# ─── 4. 打 tag ───────────────────────────────────────────────
git tag "v$new"
Write-Host "  $GREEN✔$NC 已创建 tag ${CYAN}v$new$NC"

# ─── 5. 推送（推送 tag 会触发 GitHub Actions 打包发布）───────
git push --quiet
git push --tags --quiet
Write-Host "  $GREEN✔$NC 已推送"

Write-Host ""
Write-Host "  $GREEN${BOLD}发布完成$NC  ${CYAN}v$new$NC  ${DIM}CI 正在构建安装包...$NC"
Write-Host "  ${DIM}查看进度: gh run watch  或  https://github.com/youjunxiaji/simple_load/actions$NC"
Write-Host ""
