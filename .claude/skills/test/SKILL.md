---
name: test
description: Simple Load 项目的测试工作流：运行测试、为改动补测试、排查失败用例。涉及 tests/ 目录、pytest、回归验证、重构前后对拍时使用。
argument-hint: "[run|add|debug] [模块或关键词]"
---

# Simple Load 测试

`tests/` 是重构的安全网：先跑测试确认基线，再改代码，改完再跑。

## 命令

```bash
uv run pytest                          # 全量（~5 秒，231 个用例，覆盖率 99%）
uv run pytest tests/test_reduce_load.py            # 单模块
uv run pytest -k "工况占比"                         # 按用例名过滤（中文可直接搜）
uv run pytest --cov                    # 覆盖率（source 已配在 pyproject）
uv run pytest -x --tb=long             # 首次失败即停、打全栈
```

配置在 `pyproject.toml` 的 `[tool.pytest.ini_options]`：`asyncio_mode = "auto"`（async 用例不用加 marker）、
`pythonpath = ["."]`（直接 import `app_simpleLoad` / `my_websockets` / `main`）。

## 目录

| 文件 | 覆盖对象 |
|------|---------|
| `conftest.py` | 公共 fixture；全局单例的隔离与还原 |
| `factories.py` | 合成数据集：txt + 频次表现场生成 |
| `reference.py` | **参考实现**：按 README 公式独立重写的算法 |
| `excel_io.py` | 读回导出的 Excel、逐列近似比较 |
| `fakes.py` | FakeWebSocket / StubCalSimpleLoad / InstantSleepAsyncio |
| `test_config.py` | 配置数据类与 FileParseError |
| `test_file_reader.py` | 文件名归一化、频次表校验、单文件解析、并发读取 |
| `test_preprocessing.py` | 步骤一：setInit 校验、工况占比、采样间隔 |
| `test_divide_interval.py` | 步骤二：区间划分、加权直方图 |
| `test_reduce_load.py` | 步骤三：标签化、幂等效、聚合、tol 过滤 |
| `test_excel_output.py` | GL / Romax 两个 Excel 的结构与坐标映射 |
| `test_routes.py` | 三个 HTTP 接口的全部分支 + 完整链路 |
| `test_websockets.py` | 全局连接单例、连接管理器、ws 路由 |
| `test_main.py` | 应用装配（路由前缀、CORS、lifespan）与启动横幅 |
| `test_progress.py` `test_memory.py` `test_logger.py` | core 下的三个基础模块 |
| `test_real_case.py` | 真实 `测试案例/` 冒烟（目录不在时自动跳过） |

## 常用 fixture

- `dataset` —— 两工况各 4 行的确定性数据集（占比 1/3 与 2/3）；`dataset_no_time` 无 Time 列
- `instance` → `loaded` → `divided` —— 分别是「已 setInit」「已预处理」「已划分区间」的 `CalSimpleLoad`
- `config` / `header` / `romax_origin` —— 典型换算参数与坐标映射（z←y，即排除 My）
- `connected_ws` —— 注册成 `simple_load` 的假连接，`.texts` / `.progresses` 直接断言推送内容
- `instant_sleep(module)` —— 把某模块里的 `asyncio.sleep` 变成瞬时返回（进度条、等待重连）
- `api_client` —— 带 lifespan 的 `TestClient`
- `real_case_dir` —— 本地 `测试案例/`，缺失则 skip

## 加测试的套路

**改了算法** → 先改 `tests/reference.py` 里对应的公式，再改生产代码；
`test_reduce_load.py::test_与参考实现逐格一致` 会把两套实现对拍。数值有意的变更必须两边同时改，
这是防止重构漂移的核心机制。

**改了读文件 / 校验** → 在 `test_file_reader.py` 加用例，用 `write_txt` / `write_freq_table`
造畸形输入，断言 `FileParseError`（文件问题）或 `ValueError`（配置/匹配问题）的**消息文本**，
这些文本会原样弹给前端。

**改了导出格式** → `test_excel_output.py`。列顺序、sheet 名、坐标轴正负号都是下游 Romax 的硬约定。

**改了接口** → `test_routes.py` 用 `StubCalSimpleLoad` 覆盖分支；只有 `TestFullFlowThroughApi`
跑真实计算。

## 坑（踩过的）

0. **测试绝不连真实服务器**。`TestClient` 在进程内直接调 ASGI 应用，不开端口、不走网络；
   任何情况下都不要去连远程机器（如 `10.0.3.198`）。万一要验真实 WebSocket，
   只能临时在本机起服务连 `localhost`，且不要放进测试套件。
   相应地，`socket_routes.py:42-44`（握手阶段的兜底 except）没有覆盖 ——
   让 `connect` 抛错会让 TestClient 的 websocket 握手死等，不值得为覆盖率冒挂起的风险。
1. **不要用精确相等比数值**。单位换算是 float32 除法（Polars 内部按倒数乘），
   `100000/1000` 得到 `100.0000076`。一律 `pytest.approx(rel=1e-5)`。
2. **不要用 `DataFrame.equals` 比结果表**。Polars 聚合求和次序不稳定，同参数跑两次末位可能不同；
   用 `excel_io.assert_frames_close`。
3. **GL Excel 是 MultiIndex + merge_cells**，读回来重复的标签是 NaN；用 `excel_io.read_gl`（已 ffill）。
4. **默认不要建 WebSocket 连接**。没有连接时第一次推送就失败并中断 sleep 循环，整套流水线才是毫秒级；
   要断言推送内容时才用 `connected_ws`，并且必须配 `instant_sleep`。
5. **全局单例会串味**：`GlobalWebSocket._connections`、root logger handler、内存监控开关，
   conftest 里有 autouse fixture 复位，新增全局状态时记得一起加。
6. **文件读取是并发的**，`file_results` 与 `df_all` 的行顺序不确定，断言前先排序。
7. **`工况占比` 等派生列依赖频次表列顺序**，不依赖列名 —— 造数据时别指望改列名生效。

## 收尾检查

- `uv run pytest` 全绿；新增用例名用中文，能直接读出断言的是什么
- 断言写在「行为」上（返回值、导出内容、错误消息），不要断言内部实现细节
- 不提交 `git add/commit`，交给用户手动执行
