# Simple Load - 载荷简化计算系统

> 风电齿轮箱时序载荷简化工具，将大量时序载荷数据缩减为少量等效载荷工况。

**版本**: v1.2.3  
**开发团队**: Lei Gu & Hengshan Liu  
**Python**: >= 3.11

---

## 目录

- [项目简介](#项目简介)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [输入数据格式](#输入数据格式)
- [计算流程与算法](#计算流程与算法)
- [API 接口](#api-接口)
- [WebSocket 通信协议](#websocket-通信协议)
- [构建与部署](#构建与部署)

---

## 项目简介

Simple Load 是一套面向风电齿轮箱设计的载荷简化系统，核心功能是将数百个工况的时序载荷数据（每个工况数万行），通过统计聚合和等效变换，缩减为数百个等效载荷工况，用于 Romax 等传动系统仿真软件的疲劳分析输入。

### 主要流程

```
时序载荷文件（.txt） ──┐
                      ├──→ 加载 & 预处理 ──→ 划分区间 ──→ 载荷缩减 ──→ Excel 输出
频次表（.xlsx）  ──────┘
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 实时通信 | WebSocket（进度推送） |
| 数据处理 | Polars（主力）+ Pandas（Excel I/O）+ NumPy |
| 日志 | 标准 logging + Rich（彩色控制台） |
| 打包 | Nuitka（编译为原生机器码）+ Inno Setup（Windows 安装包） |
| 包管理 | uv |

---

## 项目结构

```
simple_load/
├── main.py                          # 应用入口，FastAPI 实例创建与启动
├── pyproject.toml                   # 项目配置与依赖声明
├── .python-version                  # Python 版本锁定 (3.11)
│
├── app_simpleLoad/                  # 核心业务模块
│   ├── routes.py                    # API 路由定义（3 个接口）
│   ├── core/                        # 核心基础模块
│   │   ├── config.py                # 配置数据类（PathConfig, ConversionConfig 等）
│   │   ├── memory.py                # 内存监控工具（可通过 --debug 启用）
│   │   └── progress.py              # WebSocket 进度推送封装
│   ├── services/                    # 业务服务层
│   │   └── file_reader.py           # 异步文件读取（ThreadPool + asyncio）
│   └── module/
│       └── cal_simpleLoad.py        # 载荷简化计算编排层
│
├── my_websockets/                   # WebSocket 模块
│   ├── global_ws.py                 # 全局 WebSocket 单例（进度推送）
│   ├── socket_manager.py            # 连接管理器（连接/断开/命令处理）
│   └── socket_routes.py             # WebSocket 路由
│
├── static/                          # 静态资源
│   └── app_icon.ico                 # 应用图标
│
├── build.bat                        # Windows 打包脚本（Nuitka 编译）
├── inno_setup.iss                   # Inno Setup 安装包配置
└── release.ps1                      # 一键发版脚本（改版本号→打 tag→推送触发 CI）
```

---

## 快速开始

### 环境要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装与运行

```bash
# 克隆项目
git clone <repo-url>
cd simple_load

# 安装依赖
uv sync

# 启动服务
uv run python main.py
```

启动后：

- HTTP 服务：`http://localhost:9000`
- WebSocket：`ws://localhost:9000/ws/{client_id}`
- API 文档：`http://localhost:9000/docs`

#### Debug 模式

添加 `--debug` 参数启用内存监控日志：

```bash
uv run python main.py --debug
```

---

## 输入数据格式

系统需要两类输入文件：**时序载荷文件**和**频次表**。

### 1. 时序载荷文件（.txt）

位于同一文件夹下的多个 `.txt` 文件，每个文件代表一个工况的时序载荷数据。

**格式要求**：

- 以空格分隔的纯文本（支持多空格/Tab）
- 每行代表一个时间步的载荷数据
- 数据类型：浮点数

**必需列**（通过前端 `header` 配置映射）：

| 列名 | 含义 | 单位 |
|------|------|------|
| `Mx[KNm]` | X 方向弯矩 | KN·m |
| `My[KNm]` | Y 方向弯矩 | KN·m |
| `Mz[KNm]` | Z 方向扭矩 | KN·m |
| `Fx[KN]` | X 方向力 | KN |
| `Fy[KN]` | Y 方向力 | KN |
| `Fz[KN]` | Z 方向力 | KN |
| `speed[rpm]` | 转速 | rpm |

**可选列**：

| 列名 | 含义 | 说明 |
|------|------|------|
| `Time[s]` | 时间 | 若存在则自动计算仿真时间和采样间隔 |

> 支持 `占位符` 列名跳过不需要的数据列。

**示例**：

```
  0.000   123.45  -67.89   45.67  -12.34   89.01  -56.78   15.2
  0.001   124.56  -66.78   46.78  -11.23   90.12  -55.67   15.3
  ...
```

### 2. 频次表（.xlsx）

Excel 文件，记录每个工况的发生频次和仿真时长。

| 列名 | 类型 | 含义 |
|------|------|------|
| `文件名` | 字符串 | 对应 `.txt` 文件名（不含扩展名） |
| `全寿命发生次数` | 数值 | 该工况在整个设计寿命内的发生次数 |
| `仿真时间（s）` | 数值 | 仿真持续时间（秒），若 txt 中包含 `Time[s]` 列则自动计算 |

**示例**：

| 文件名 | 全寿命发生次数 | 仿真时间（s） |
|--------|-------------|-------------|
| DLC1.1_case01 | 52560 | 600 |
| DLC1.1_case02 | 26280 | 600 |
| DLC1.3_case01 | 8760 | 600 |

### 数据拼接逻辑

```
                    多进程读取
时序载荷 .txt ──────────────────→ df_dic（字典: 文件名 → DataFrame）
                                          │
                                          ├──→ 合并为 df_all（全量数据）
                                          │
                                          └──→ 提取每个文件的行数/时间信息
                                                       │
频次表 .xlsx ─────→ df_ref（参考表）──── JOIN ───────────┘
                                     （按文件名关联）
```

---

## 计算流程与算法

### 步骤一：加载与预处理

#### 1.1 单位转换

读取原始数据后，按配置的转换系数进行单位统一：

$$M_i = \frac{M_{i,\text{raw}}}{k_{\text{moment}}}, \quad F_i = \frac{F_{i,\text{raw}}}{k_{\text{force}}}, \quad n = n_{\text{raw}} \times k_{\text{speed}}$$

其中 $k_{\text{moment}}$、$k_{\text{force}}$、$k_{\text{speed}}$ 为用户配置的单位转换系数。

#### 1.2 工况占比计算

将频次表与载荷数据按文件名关联后，计算每个工况的时间占比：

$$w_j = \frac{T_j \times N_j}{\sum_{k=1}^{K} T_k \times N_k}$$

其中：
- $w_j$：工况 $j$ 的时间占比
- $T_j$：工况 $j$ 的仿真时间（s）
- $N_j$：工况 $j$ 的全寿命发生次数
- $K$：总工况数

#### 1.3 采样间隔

若数据中不含 `Time[s]` 列，则根据频次表中的仿真时间自动计算：

$$\Delta t_j = \frac{T_j}{n_{\text{rows},j} - 1}$$

### 步骤二：划分区间与直方图

#### 2.1 区间划分

对每个力/力矩分量，根据全局最值创建 200 个均匀区间：

- **跨零情况**（$\min \times \max < 0$）：负值侧 100 个区间 + 正值侧 100 个区间
- **同号情况**：整体 200 个均匀区间

$$\text{bins} = \begin{cases} \text{linspace}\!\left(\lfloor \frac{\min}{100}\rfloor \times 100,\; 0,\; 100\right) \cup \text{linspace}\!\left(0,\; \lceil \frac{\max}{100}\rceil \times 100,\; 100\right) & \text{if } \min \times \max < 0 \\ \text{linspace}\!\left(\lfloor \frac{\min}{100}\rfloor \times 100,\; \lceil \frac{\max}{100}\rceil \times 100,\; 200\right) & \text{otherwise} \end{cases}$$

#### 2.2 加权直方图

对每个分量，按工况加权计算归一化频次分布：

$$H(b) = \sum_{j=1}^{K} w_j \cdot \frac{c_j(b)}{n_{\text{rows},j}}$$

其中：
- $H(b)$：区间 $b$ 的加权频次
- $c_j(b)$：工况 $j$ 中落入区间 $b$ 的数据点数
- $n_{\text{rows},j}$：工况 $j$ 的总数据行数

### 步骤三：载荷缩减

#### 3.1 区间标签化

根据用户在前端定义的分区边界，将每个时间步的力/力矩值映射到离散标签：

$$\ell_i^{(c)} = \text{digitize}(F_i^{(c)},\; \text{bins}^{(c)})$$

其中 $c$ 为分量名（如 $F_x$、$M_y$ 等），排除与 Romax Z 轴对应的力矩分量。

#### 3.2 等效时间与转速

$$\Delta t_{\text{life},j} = \Delta t_j \times N_j$$

$$R_i = \Delta t_{\text{life},j} \times |n_i|$$

$$T_i = \Delta t_{\text{life},j}$$

其中：
- $\Delta t_{\text{life},j}$：工况 $j$ 中每个时间步的等效全寿命时间
- $R_i$：第 $i$ 个时间步的转速加权量（格子转速）
- $T_i$：第 $i$ 个时间步的等效时间（格子时间）

#### 3.3 幂等效变换

对载荷进行 S-N 曲线斜率相关的幂变换：

$$\tilde{F}_i = \text{sgn}(F_i) \cdot |F_i|^m$$

其中 $m$ 为 `translate_factor`（与材料 S-N 曲线斜率相关）。

#### 3.4 聚合与缩减

按标签组合进行分组聚合：

$$\bar{R}_g = \sum_{i \in g} R_i, \quad \overline{\tilde{F}}_g = \sum_{i \in g} \tilde{F}_i \cdot R_i$$

$$\hat{F}_g = \frac{\overline{\tilde{F}}_g}{\bar{R}_g}$$

过滤条件：

$$p_g = \frac{\bar{R}_g}{\sum_g \bar{R}_g} > \text{tol}$$

其中 $\text{tol}$ 为用户设定的容差阈值。

#### 3.5 逆幂变换还原

$$F_g = \text{sgn}(\hat{F}_g) \cdot |\hat{F}_g|^{1/m}$$

#### 3.6 等效时间计算

每个缩减工况的持续时间（小时）：

$$t_g = p_g \times \frac{\sum_{j=1}^{K} T_j \times N_j}{3600}$$

### 输出结果

系统生成两个 Excel 文件：

| 文件 | 说明 |
|------|------|
| `Load_Reduction_GL-{name}.xlsx` | 通用格式，按标签索引，包含时间、转速、各分量载荷 |
| `Load_Reduction_Romax-{name}.xlsx` | Romax 格式，包含工况表格定义、载荷矩阵（含转置），力/力矩方向按 Romax 坐标系转换 |

---

## API 接口

所有接口均以 `/api` 为前缀，使用 `POST` 方法。

### POST `/api/load_file`

加载时序载荷文件并进行预处理。

**请求体**：

```json
{
  "file_path": {
    "result_folder_save_path": "/path/to/output",
    "load_file_folder_path": "/path/to/txt/files",
    "freq_table_path": "/path/to/freq.xlsx"
  },
  "draggableElements": [
    {"name": "Time[s]"},
    {"name": "Mx[KNm]"},
    {"name": "My[KNm]"},
    {"name": "Mz[KNm]"},
    {"name": "Fx[KN]"},
    {"name": "Fy[KN]"},
    {"name": "Fz[KN]"},
    {"name": "speed[rpm]"}
  ],
  "conversion_factors": {
    "title_row": 0,
    "unit_moment": 1000,
    "unit_force": 1000,
    "unit_speed": 1.0,
    "translate_factor": 4,
    "temperature": 40,
    "tol": 1e-6
  }
}
```

**响应**：

```json
{"message": "读取文件完成", "status": "success"}
```

### POST `/api/divide_interval`

划分区间并生成直方图数据。

**请求体**：

```json
{
  "romax_origin": []
}
```

**响应**：

```json
{
  "message": "划分区间完成",
  "min_max": "{...}",
  "echarts_data": "{...}",
  "status": "success"
}
```

### POST `/api/reduce_load`

执行载荷缩减计算并输出 Excel。

**请求体**：

```json
{
  "tableData": [
    {"0": "-500", "1": "0", "2": "500"},
    {"0": "-300", "1": "0", "2": "300"}
  ],
  "romax_origin": [
    {"romax": "x", "origin": "x"},
    {"romax": "y", "origin": "-z"},
    {"romax": "z", "origin": "y"}
  ]
}
```

**响应**：

```json
{"message": "载荷简化处理全部完成", "count": 256}
```

---

## WebSocket 通信协议

### 连接

```
ws://localhost:9000/ws/{client_id}
```

固定 `client_id` 为 `simple_load`。

### 服务端推送消息格式

```json
{"type": "text", "message": "已处理 5/20 个文件"}
{"type": "progress", "message": "25.0"}
```

| type | 说明 |
|------|------|
| `text` | 状态文本消息 |
| `progress` | 进度百分比（0-100） |

### 客户端命令格式

```json
{"type": "command", "command": "reset_instance"}
{"type": "broadcast", "message": "hello"}
```

---

## 构建与部署

### Windows 打包

项目使用 Nuitka 将 Python 编译为原生机器码，再通过 Inno Setup 生成安装包。

```powershell
# 在 Windows 环境下执行
.\build.bat
```

打包流程：

1. **Nuitka**（`--standalone`）→ 将 `main.py` 及业务模块编译为原生代码，生成 `output/simple_load/` 目录
2. **Inno Setup** → 打包生成 `software/simple_load-{version}.exe` 安装包

> Nuitka 直接编译为机器码，业务代码（`app_simpleLoad`、`my_websockets`）被编入主 exe，自带源码保护，无需额外加密步骤。

安装包支持注册自定义 URL 协议 `tmb-app://`，允许前端通过浏览器直接启动本地服务。

### 发版（自动构建 + 发布 Release）

推送 `v*` tag 会触发 GitHub Actions 自动用 Nuitka 编译、打包并发布到 Release。推荐用发版脚本一键完成：

```powershell
.\release.ps1   # ↑↓ 选 major/minor/patch → 自动改版本号、提交、打 tag、推送触发 CI
```

---

## 许可证

内部项目，未公开发布。
