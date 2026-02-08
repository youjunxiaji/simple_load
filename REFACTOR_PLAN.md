# 重构计划

> 基于 [PROBLEM.md](./PROBLEM.md) 中记录的问题，制定分阶段重构方案。

---

## 重构目标

1. **内存占用降低 50%+**：2GB TXT 处理时峰值内存控制在 3GB 以内
2. **事件循环不阻塞**：文件处理期间 WebSocket 进度推送保持流畅
3. **依赖声明完整**：新环境一键安装即可运行
4. **代码结构清晰**：模块职责明确，便于维护

---

## 阶段零：代码结构重组

**解决**: 代码职责混乱，637 行大文件难以维护

### 目标结构

```
simple_load/
├── main.py                           # 入口，只负责 FastAPI 创建和启动
├── pyproject.toml
│
├── app_simpleLoad/
│   ├── routes.py                     # API 路由（保持不变）
│   ├── schemas.py                    # [新] Pydantic 请求/响应模型
│   │
│   ├── core/                         # [新] 核心基础模块
│   │   ├── __init__.py
│   │   ├── config.py                 # 配置管理（转换系数、路径等）
│   │   └── progress.py               # 进度推送器（封装 WebSocket 进度逻辑）
│   │
│   ├── services/                     # [新] 业务服务层
│   │   ├── __init__.py
│   │   ├── file_reader.py            # 文件读取服务（异步读 TXT + Excel）
│   │   ├── data_processor.py         # 数据预处理（单位转换、工况占比）
│   │   ├── interval_analyzer.py      # 区间划分 & 直方图生成
│   │   └── load_reducer.py           # 载荷缩减核心算法
│   │
│   └── module/
│       └── cal_simpleLoad.py         # [重构] 变成编排层，调用各 service
│
├── my_websockets/                    # WebSocket 模块（基本保持不变）
│   ├── global_ws.py
│   ├── socket_manager.py
│   └── socket_routes.py
```

### 模块职责拆分

| 模块 | 职责 | 从原代码提取的功能 |
|------|------|------------------|
| `schemas.py` | API 请求体/响应体类型定义 | routes.py 中的 Dict 类型替换为 Pydantic 模型 |
| `config.py` | 配置数据类 | `setInit()` 中的参数 → `PathConfig` + `ConversionConfig` |
| `progress.py` | 进度推送封装 | `_update_progress_smoothly()` + WebSocket 调用 |
| `file_reader.py` | 异步文件读取 | `_process_single_file_sync()` + `simple_Pre_processing()` 的读取部分 |
| `data_processor.py` | 数据预处理 | 单位转换、工况占比计算、参考表合并 |
| `interval_analyzer.py` | 区间与直方图 | `simple_load1()` + `savePic()` |
| `load_reducer.py` | 载荷缩减核心 | `simple_load2()` 全部逻辑 |
| `cal_simpleLoad.py` | 编排层 | 组合调用各 service，管理流程状态 |

### 设计原则

1. **单一职责** - 每个文件只做一件事，不超过 200 行
2. **依赖注入** - `ProgressReporter` 通过参数传入，不硬编码全局 WebSocket
3. **类型安全** - 用 Pydantic 定义 API 类型，用 `dataclass` 定义内部配置
4. **异步优先** - 文件读取用 `async/await`，不阻塞事件循环
5. **纯函数优先** - 计算逻辑尽量写成纯函数/静态方法，便于测试

**预估工作量**: 2-3 小时（含后续阶段的代码迁移）  
**风险**: 中（需要同步修改 routes.py 的调用方式）

---

## 阶段一：修复依赖与清理 (低风险)

**解决**: P3（依赖不完整）、P5（不必要的 import）

### 1.1 补全 pyproject.toml 依赖

```toml
dependencies = [
    "fastapi>=0.128.4",
    "loguru>=0.7.3",
    "polars>=1.38.1",
    "uvicorn>=0.40.0",
    "pandas>=2.2.0",
    "pyarrow>=17.0",
    "numpy>=2.0.0",
    "psutil>=6.0.0",
    "openpyxl>=3.1.0",
]
```

### 1.2 清理 main.py 多余 import

移除 `main.py` 中的 `import pandas` 和 `import pyarrow`，将 `copy_on_write` 配置移至 `cal_simpleLoad.py` 中实际使用 pandas 的位置。

**预估工作量**: 10 分钟  
**风险**: 极低

---

## 阶段二：消除内存翻倍问题 (中风险)

**解决**: P1.2（df_dic + df_all 双重存储）

### 2.1 重构数据存储策略

**核心思路**：不再同时保留 `df_dic` 和 `df_all`，而是：

- 读取时直接 concat 为 `df_all`，给每行标记文件名
- `savePic()` 中用 `group_by('文件名')` 代替遍历 `df_dic`

```python
# 重构前
self.df_dic = {name: df for name, df in results}        # 占内存
self.df_all = pl.concat(list(self.df_dic.values()))      # 又占内存

# 重构后
frames = [df for _, df, _ in results]
self.df_all = pl.concat(frames)     # 只保留一份
del frames
# df_dic 不再需要
```

### 2.2 重构 savePic() 方法

```python
# 重构前：遍历 df_dic
for file_name, df in self.df_dic.items():
    col_data = df[column].to_numpy()
    ...

# 重构后：用 group_by
for (file_name,), group_df in self.df_all.group_by('文件名'):
    col_data = group_df[column].to_numpy()
    ...
```

**预估内存节省**: ~40%（消除完整的 df_dic 副本）  
**预估工作量**: 30 分钟  
**风险**: 中（需要验证 savePic 输出一致性）

---

## 阶段三：替换多进程为异步线程 (中风险)

**解决**: P1.1（序列化开销）、P2（事件循环阻塞）

### 3.1 替换 ProcessPoolExecutor 为 asyncio + ThreadPoolExecutor

```python
# 重构前
with ProcessPoolExecutor(max_workers=mp.cpu_count()) as executor:
    future_to_file = {
        executor.submit(self._process_single_file_sync, args): path
        for path in file_paths
    }
    for future in as_completed(future_to_file):  # 阻塞事件循环
        result = future.result()
        ...

# 重构后
import asyncio
from concurrent.futures import ThreadPoolExecutor

loop = asyncio.get_event_loop()
semaphore = asyncio.Semaphore(8)  # 控制并发数

async def process_file(file_path):
    async with semaphore:
        result = await loop.run_in_executor(
            thread_pool,
            self._process_single_file_sync,
            (file_path, self.header, self.conversion_factors, have_time)
        )
        return result

tasks = [process_file(fp) for fp in file_paths]

# 使用 as_completed 的异步版本
for coro in asyncio.as_completed(tasks):
    result = await coro
    processed_files += 1
    # WebSocket 推送不会被阻塞
    await self._update_progress_smoothly(...)
```

### 3.2 优势

- **无序列化开销**：线程共享内存，DataFrame 不需要 pickle
- **不阻塞事件循环**：`await` 让出控制权，WebSocket 推送实时
- **内存更省**：无多进程副本

**预估内存节省**: ~20%（消除 pickle 序列化开销）  
**预估工作量**: 1 小时  
**风险**: 中（需确认线程安全性）

---

## 阶段四：优化 Pandas → Polars 转换 (低风险)

**解决**: P1.3（中间拷贝）、P4（混用问题）

### 4.1 探索 Polars 原生 CSV 读取

Polars 的 `pl.read_csv()` 对空格分隔符支持有限，但可以尝试：

```python
# 方案 A：Polars 原生（如果支持）
df = pl.read_csv(file_path, separator="\t", has_header=False, new_columns=valid_cols)

# 方案 B：先用 Python 预处理为标准 CSV
# 适用于格式特别复杂的情况

# 方案 C：保留 pandas 读取但优化类型
df_pd = pd.read_csv(file_path, sep=r'\s+', dtype=np.float32)  # float32 省一半内存
df = pl.from_pandas(df_pd)
```

### 4.2 使用 float32 代替 float64

如果精度允许（载荷数据通常不需要 float64 的精度）：

```python
# float64: 每个值 8 字节
# float32: 每个值 4 字节 → 内存直接减半
df_pd = pd.read_csv(..., dtype=np.float32)
```

**预估内存节省**: 最高 50%（如果切换到 float32）  
**预估工作量**: 1 小时  
**风险**: 低（需验证精度是否满足要求）

---

## 阶段五：流式处理与惰性计算 (高风险, 可选)

**解决**: P1 整体优化

### 5.1 Polars LazyFrame 延迟执行

将 `df_all` 改为 `LazyFrame`，延迟到真正需要时才执行计算：

```python
# 当前：立即计算
self.df_all = pl.concat([...])
df_des = self.df_all.describe()       # 触发完整扫描

# 优化：惰性执行
self.df_all_lazy = pl.concat([...]).lazy()
# 只在需要时 .collect()
```

### 5.2 分块读取大文件

对于特别大的文件，可以考虑分块读取：

```python
# 分块读取 + 流式聚合
reader = pl.read_csv_batched(file_path, batch_size=100000)
for batch in reader:
    # 增量更新统计信息
    ...
```

**预估内存节省**: 取决于具体实现  
**预估工作量**: 3-5 小时  
**风险**: 高（需要大幅重构计算逻辑）

---

## 重构优先级与排期

| 阶段 | 解决问题 | 内存节省 | 工作量 | 优先级 |
|------|---------|---------|-------|--------|
| 阶段一 | P3, P5 | 微量 | 10min | ★★★★★ 立即做 |
| 阶段二 | P1.2 | ~40% | 30min | ★★★★★ 立即做 |
| 阶段三 | P1.1, P2 | ~20% | 1h | ★★★★ 优先做 |
| 阶段四 | P1.3, P4 | ~10-50% | 1h | ★★★ 建议做 |
| 阶段五 | P1 整体 | 额外优化 | 3-5h | ★★ 可选 |

**建议**：先做阶段一 + 阶段二（效果最大、风险最低），然后做阶段三，最后看情况决定阶段四和五。

---

## 验证方案

每个阶段完成后，用以下方式验证：

1. **功能测试**：使用现有测试数据运行完整流程，对比输出 Excel 是否一致
2. **内存测试**：使用 `psutil` 记录各阶段内存，与重构前对比
3. **性能测试**：记录各阶段耗时，确保不出现性能退化
