"""配置数据类 — 用 dataclass 替代 Dict，提供类型安全和 IDE 补全"""

from dataclasses import dataclass
import polars as pl


# ─── 自定义异常 ──────────────────────────────────────────────

class FileParseError(Exception):
    """文件解析失败异常（携带文件名和原始错误信息）"""

    def __init__(self, filename: str, reason: str):
        self.filename = filename
        self.reason = reason
        super().__init__(f"文件 {filename} 解析失败: {reason}")


# ─── 数据类 ─────────────────────────────────────────────────

@dataclass
class PathConfig:
    """路径配置"""
    result_folder_save_path: str   # 结果文件夹保存路径
    load_file_folder_path: str     # 时序载荷文件夹
    freq_table_path: str           # 频次表路径

    # 是否保留「与 Romax z 轴对应的原始轴」上的力矩分量（即绕转轴的扭矩）。
    # False（默认）= 维持现状把它排除；True = 六个分量一视同仁参与分组与输出。
    # 注意：置 True 时 tableData 需要给满 6 行区间，行序为 fx/fy/fz/mx/my/mz。
    keep_torque_component: bool = False


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
class AxisMapping:
    """Romax 轴 ← 原始轴的对应关系

    例：`AxisMapping(romax='y', origin='-z')` 表示 Romax 的 y 轴取原始 z 轴的反向。
    """
    romax: str    # Romax 坐标轴：x / y / z
    origin: str   # 对应的原始轴，带负号表示反向，如 '-z'

    @property
    def axis(self) -> str:
        """去掉负号后的原始轴名"""
        return self.origin.replace("-", "")

    @property
    def inverted(self) -> bool:
        """取值时是否需要反号"""
        return "-" in self.origin


@dataclass
class FileResult:
    """单文件解析结果"""
    name: str                             # 文件名（不含 .txt 后缀）
    df: pl.DataFrame                      # 解析后的 DataFrame
    row_count: int                        # 数据行数
    sim_time: float | None = None         # 仿真时间（s），无 Time 列时为 None
    sample_interval: float | None = None  # 采样间隔（s），无 Time 列时为 None
