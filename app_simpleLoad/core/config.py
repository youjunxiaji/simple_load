"""配置数据类 — 用 dataclass 替代 Dict，提供类型安全和 IDE 补全"""

from dataclasses import dataclass
import polars as pl


@dataclass
class PathConfig:
    """路径配置"""
    result_folder_save_path: str   # 结果文件夹保存路径
    load_file_folder_path: str     # 时序载荷文件夹
    freq_table_path: str           # 频次表路径


@dataclass
class ConversionConfig:
    """单位转换与计算参数"""
    title_row: int | None = 0       # 标题行索引（None 表示无标题行）
    unit_moment: float = 1000.0     # 力矩单位转换系数
    unit_force: float = 1000.0      # 力单位转换系数
    unit_speed: float = 1.0         # 转速单位转换系数
    translate_factor: float = 4.0   # S-N 曲线斜率（幂变换指数）
    temperature: float = 40.0       # 温度 (°C)
    tol: float = 1e-6               # 容差阈值


@dataclass
class FileResult:
    """单文件解析结果"""
    name: str                             # 文件名（不含 .txt 后缀）
    df: pl.DataFrame                      # 解析后的 DataFrame
    row_count: int                        # 数据行数
    sim_time: float | None = None         # 仿真时间（s），无 Time 列时为 None
    sample_interval: float | None = None  # 采样间隔（s），无 Time 列时为 None
