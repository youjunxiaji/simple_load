# 已知问题与待优化项

> 记录当前代码中发现的问题，作为后续重构的依据。

---

## P1: 内存占用过高 ✅ 已优化

**严重程度**: 高  
**表现**: 处理 4.8GB 的 TXT 文件集合（699 个文件，8,120,604 行 × 9 列）时，进程内存峰值过高

### 优化结果

| 指标 | 优化前 | 优化后 | 改善 |
|------|-------|-------|------|
| 预处理峰值 | ~3-4GB | 718MB | ~75% |
| 载荷缩减峰值 | 6GB+ | 2.9GB | ~52% |
| 最终内存 | - | 1.7GB | - |

**已实施的优化措施**：
1. ProcessPoolExecutor → ThreadPoolExecutor + asyncio（消除 pickle 序列化开销）
2. 去掉 df_dic 双重存储（只保留 df_all）
3. join 后立刻释放 df_all（避免与 df_final 共存）
4. float32 替代 float64（数据内存减半）

### 原因分析

#### 1.1 多进程序列化开销

**位置**: `cal_simpleLoad.py` → `simple_Pre_processing()` → `ProcessPoolExecutor`

当前使用 `ProcessPoolExecutor` 多进程处理文件，子进程处理完的 DataFrame 通过 pickle 序列化传回主进程。每个 DataFrame 在传输过程中存在 2-3 份拷贝：

```
子进程: 创建 DataFrame (内存占用 1)
     ↓ pickle 序列化
子进程: 字节流缓冲区 (内存占用 2)
     ↓ 进程间传输
主进程: 字节流缓冲区 (内存占用 3)
     ↓ pickle 反序列化
主进程: DataFrame (内存占用 4)
```

#### 1.2 双重存储: df_dic 与 df_all 同时存在

**位置**: `cal_simpleLoad.py` → `simple_Pre_processing()` L216-253

```python
self.df_dic = {r[0]: r[1] for r in results}            # 字典: 文件名 → DataFrame
self.df_all = pl.concat(list(self.df_dic.values()))     # 合并后的全量 DataFrame
```

两者包含完全相同的数据，但以不同结构同时驻留在内存中，直接导致内存翻倍。

- `df_dic` 用于后续 `savePic()` 中按文件分组计算加权直方图
- `df_all` 用于后续 `simple_load1()` 统计分析和 `simple_load2()` 载荷缩减

#### 1.3 Pandas ↔ Polars 转换的中间拷贝

**位置**: `cal_simpleLoad.py` → `_process_single_file_sync()` L101-114

```python
df_pd = pd.read_csv(...)         # Pandas DataFrame
df = pl.from_pandas(df_pd)       # Polars DataFrame（数据拷贝）
del df_pd                        # 删除 Pandas 副本
```

虽然 `del df_pd` 释放了引用，但在多进程环境下，返回的 Polars DataFrame 还会经历 pickle 序列化/反序列化，产生额外拷贝。

#### 1.4 载荷缩减阶段的 join 再次膨胀

**位置**: `cal_simpleLoad.py` → `simple_load2()` L451-455

```python
df_final = self.df_all.join(self.df_ref, on='文件名', how='left')
```

`join` 操作基于 `df_all` 创建新的 `df_final`，增加了 `df_ref` 中的列，此时 `df_all` 和 `df_final` 同时存在。

### 内存峰值时间线

```
                    内存 (GB)
                    │
               6+GB │                          ╭──── df_all + df_final + 中间计算
                    │                         ╱
               4GB  │            ╭────────────╯  df_dic + df_all
                    │           ╱
               3GB  │     ╭────╯  多进程 pickle 开销
                    │    ╱
               1GB  │───╯  读取开始
                    │
                    └──────────────────────────────→ 时间
                     加载文件    预处理完成   载荷缩减
```

---

## P2: 事件循环阻塞 ✅ 已修复

**严重程度**: 中  
**表现**: 文件处理期间 WebSocket 进度推送可能延迟或卡顿

> **已修复**：使用 asyncio + ThreadPoolExecutor 替代 ProcessPoolExecutor，`await` 让出控制权，WebSocket 推送不再被阻塞。

### 原因分析

**位置**: `cal_simpleLoad.py` → `simple_Pre_processing()` L199

```python
for future in as_completed(future_to_file):   # ← 同步阻塞调用
    ...
    await self._update_progress_smoothly(...)  # ← 在阻塞间隙才能推送
```

`concurrent.futures.as_completed()` 是同步迭代器，在等待下一个 future 完成时会阻塞 asyncio 事件循环，导致：

- WebSocket 消息推送延迟
- 其他 API 请求被阻塞
- 进度条更新不流畅

---

## P3: pyproject.toml 依赖不完整 ✅ 已修复

**严重程度**: 低  
**表现**: 新环境安装依赖后无法运行

> **已修复**：补全 numpy、openpyxl、websockets 等缺失依赖。

### 原因分析

**位置**: `pyproject.toml`

当前声明的依赖：

```toml
dependencies = [
    "fastapi>=0.128.4",
    "loguru>=0.7.3",
    "polars>=1.38.1",
    "uvicorn>=0.40.0",
]
```

实际代码中使用但未声明的包：

| 包名 | 使用位置 | 说明 |
|------|---------|------|
| `pandas` | `main.py`, `cal_simpleLoad.py` | Excel 读写、CSV 读取、IntervalIndex |
| `pyarrow` | `main.py` | pandas Excel 引擎 |
| `numpy` | `cal_simpleLoad.py` | 数值计算 |
| `psutil` | `cal_simpleLoad.py` | 内存监控 |
| `openpyxl` | 隐式依赖 | pandas 读写 Excel 所需 |
| `websockets` | 隐式依赖 | FastAPI WebSocket 底层 |

---

## P4: Polars 和 Pandas 混用

**严重程度**: 低  
**表现**: 代码风格不统一，增加维护成本

### 原因分析

文件头注释标注"使用 Polars 替代 Pandas"，但实际仍大量使用 Pandas：

| 操作 | 当前使用 | 原因 |
|------|---------|------|
| 读取空格分隔的 TXT | `pd.read_csv()` | Polars 对 `\s+` 正则分隔支持不完善 |
| 读取 Excel | `pd.read_excel()` | Polars 的 Excel 读取功能较新 |
| 写入 Excel | `pd.to_excel()` | Polars 的 Excel 写入需要额外依赖 |
| `IntervalIndex` | `pd.IntervalIndex` | Polars 无等价功能 |
| JSON 输出 | `df.to_pandas().to_json()` | 需要 pandas 特定的 JSON 格式 |

---

## P5: main.py 中不必要的 import ✅ 已修复

**严重程度**: 低  
**表现**: 启动时加载不必要的包，增加启动时间

> **已修复**：移除 main.py 中的 `import pandas` 和 `import pyarrow`，Pandas 3.0+ 已默认开启 Copy-on-Write，无需手动配置。

### 原因分析

**位置**: `main.py` L12-17

```python
import pandas as pd
import pyarrow

pd.options.mode.copy_on_write = True
```

`main.py` 作为 FastAPI 入口，不直接使用 pandas/pyarrow 进行数据处理。这些 import 会在启动时强制加载这两个大型库（约增加 200-300ms 启动时间和 100MB+ 内存）。

`pd.options.mode.copy_on_write = True` 的配置应放在实际使用 pandas 的模块中。
