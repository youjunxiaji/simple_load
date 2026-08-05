# CLAUDE.md

> 给 Claude Code 的项目工作指引。
> 算法公式与接口报文见 [README.md](./README.md)，已知问题见 [docs/PROBLEM.md](./docs/PROBLEM.md)，
> 重构路线见 [docs/REFACTOR_PLAN.md](./docs/REFACTOR_PLAN.md)，测试怎么写见 `.claude/skills/test/SKILL.md`（`/test`）。

## 这是什么

风电齿轮箱**载荷简化**工具：把几百个工况、上千万行的时序载荷（txt）+ 频次表（xlsx），
按等效损伤缩减成几百条等效工况，导出给 Romax 做疲劳分析。
本地起 FastAPI 服务（`localhost:9000`），前端通过 HTTP 调三步接口，WebSocket 收进度。

技术栈：FastAPI + Uvicorn / Polars（算）+ Pandas（Excel、CSV I/O）/ Nuitka 打包 / uv 管依赖。

## 常用命令

```bash
uv sync                        # 装依赖（含 dev 组）
uv run python main.py          # 起服务；加 --debug 开内存监控日志
uv run pytest                  # 全量测试，约 4 秒
.\packaging\build.bat          # Windows 下 Nuitka 编译 + Inno Setup 打包
.\packaging\release.ps1        # 改版本号 → 打 tag → 推送触发 CI 发 Release
```

打包脚本都放在 `packaging/`，但**产物固定落在仓库根**：`output/`（Nuitka 中间产物）、
`software/`（安装包）；`inno_setup.iss` 里的相对路径以自身所在目录为基准，所以写成 `..\output`、`..\software`。
版本号有四处（`pyproject.toml` / `packaging/build.bat` / `main.py` 横幅 / `README.md`），
由 `release.ps1` 统一改，别手改单处。

## 代码地图

| 文件 | 职责 | 动它之前先知道 |
|------|------|--------------|
| `main.py` | FastAPI 实例、CORS、启动横幅 | 版本号在 `show_startup_banner` 里写死了一份，改版本要同步 `pyproject.toml` |
| `app_simpleLoad/routes.py` | 三个接口：加载 / 划分区间 / 缩减 | 没有 Pydantic 校验，请求体是裸 `Dict` |
| `app_simpleLoad/module/cal_simpleLoad.py` | 三个步骤的实现，全部状态在这个实例上 | 最大的一个文件，重构主战场 |
| `app_simpleLoad/services/file_reader.py` | 异步读 txt（线程池）、读频次表、一致性校验 | 面向用户的错误文案都在这里 |
| `app_simpleLoad/core/` | 配置数据类 / 进度推送 / 日志 / 内存监控 | `memory` 默认关闭，`--debug` 才打开 |
| `my_websockets/` | 全局连接单例、连接管理器、ws 路由 | `GlobalWebSocket._connections` 是类属性级全局状态 |
| `tests/` | pytest 测试与合成数据 | 改代码前后各跑一次 |
| `packaging/` | Windows 打包与发版脚本 | 路径改动要同步 `.github/workflows/build.yml` |
| `docs/` | PROBLEM / REFACTOR_PLAN | 重构进展记在这里 |

## 关键约定

### 计算实例挂在 WebSocket 连接上

`ConnectionManager.cal_instance` 在客户端连上 `simple_load` 时创建，断开时清空。
所以三个接口都要先判 `instance is None`；`/api/load_file` 在实例为空且连接断开时，
会等待前端重连最多 10 秒（20 × 0.5s）再放弃。改这块要同步改 `tests/test_routes.py`。

### 业务错误不抛 HTTP 错误码

所有可预期的失败都返回 `200` + `{"message": "...", "status": "error"}`，
消息文本直接显示给用户，**属于对外契约**，改文案要一起改测试。
区分：`FileParseError` = 文件本身有问题，`ValueError` = 配置/匹配不上。

### 频次表按「列序」识别，不认列名

`read_freq_table` 用 `names=["文件名", "全寿命发生次数", "仿真时间（s）"]` 强行覆盖表头，
所以 Excel 里叫什么都行，但**顺序必须是这三列**。
文件名会做归一化（去空格、去 `.txt` 后缀），再和实际 txt 文件做一一匹配校验。

### `title_row` 是「标题行的行号」，不是「有没有标题行」

真实载荷文件第 0 行是列名、第 1 行是 `END_OF_HEADER`，所以前端传 `title_row=1`
（pandas 把第 1 行当表头，之前的行全丢掉）。`None` 表示没有标题行。

### 数值精度：全链路 float32

读文件时 `dtype=np.float32`（内存减半，工程精度足够）。
注意 Polars 的 f32 除法内部按「乘倒数」实现，`100000/1000` 会得到 `100.0000076`，
所以任何和期望值的比较都要带容差，别写精确相等。

### GL 导出的排序与工况编号

`Load_Reduction_GL-*.xlsx` 由 `simple_load2()` 生成，顺序不能乱：

1. 聚合后的 `df_pivot` **先按 `index_cols`（数字标签）排序**；
2. 再按排序后的行号生成 `工况` 列，格式 `loc001`、`loc002`；
3. 最后才把数字标签转成展示值 `fx1`、`fy2`；
4. 导出用 `set_index(index_cols)` 形成 MultiIndex，`merge_cells=True`
   让 Excel 里连续相同的层级合并成一格。

### Romax 坐标映射

前端传 `romax_origin`（`[{"romax":"x","origin":"x"}, ...]`）描述 Romax 轴 ← 原始轴的对应关系：

- `romax_origin[2]['origin']` 指定 Romax z 对应的原始轴，**该轴上的力矩分量被排除**
  （例：z←y 时排除 `My[KNm]`，结果表里就没有 `my_label`）；
- 导出 Romax 文件时按映射取列，`origin` 带负号则整列取反；
- 结果表里没有的源列，Romax 对应列填 0。

### 进度推送协议

只有两种消息：`{"type":"text","message":...}` 和 `{"type":"progress","message":"0~100"}`。
`ProgressReporter.update_smoothly` 每 0.1 秒推一格，推送失败（连接断开）就立刻停，不阻塞计算。

## 测试

`tests/` 是这个项目重构的安全网，**改代码前先跑一遍确认基线**：

```bash
uv run pytest
```

数据全部由 `tests/factories.py` 现场合成（两工况各 4 行，几行就能看出问题），
不依赖未入库的 `测试案例/`。`tests/reference.py` 是按 README 公式独立重写的**参考实现**，
和生产代码对拍 —— 改算法时两边一起改，数值漂移会立刻暴露。

接口测试走 `TestClient`，**在进程内调 ASGI 应用，不开端口也不连网络**；
任何测试都不要去连远程服务器，要验真实 WebSocket 只能临时在本机起服务连 `localhost`。

细节和套路见 `/test` skill。

## 约定

- 依赖用 `uv add` / `uv add --dev` 管，不手改 `pyproject.toml` 里的依赖列表。
- 注释、文档、用例名一律中文；类型注解用 3.11 原生写法（`str | None`、`list[str]`），
  不要写 `from __future__ import annotations`。
- 项目级配置写在项目里（`.claude/`、`pyproject.toml`），不要塞到用户全局。
- commit message 用中文；**不要自动执行 `git add` / `git commit` / `git push`**，交给用户。
