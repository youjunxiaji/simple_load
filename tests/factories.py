"""合成测试数据构造器

所有测试都不依赖 `测试案例/`（该目录未纳入版本管理），而是用本模块在
临时目录里现场生成「时序载荷 txt + 频次表 xlsx」，规模小到毫秒级，
但结构与真实输入完全一致。

用法::

    ds = build_dataset(tmp_path, [
        CaseSpec("c1", {"speed[rpm]": [10, 12], "Fx[KN]": [1000, 2000], ...}),
    ])
    inst.setInit(paths=ds.path_config(), header=ds.header, config=cfg)
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

# ─── 常量 ────────────────────────────────────────────────────

#: 前端 draggableElements 的典型顺序（txt 各列的含义）
DEFAULT_HEADER: list[str] = [
    "Time[s]",
    "speed[rpm]",
    "Mx[KNm]",
    "My[KNm]",
    "Mz[KNm]",
    "Fx[KN]",
    "Fy[KN]",
    "Fz[KN]",
]

#: 六个力/力矩分量（区间划分与直方图的对象）
LOAD_COLUMNS: list[str] = ["Mx[KNm]", "My[KNm]", "Mz[KNm]", "Fx[KN]", "Fy[KN]", "Fz[KN]"]

#: 频次表默认表头（read_freq_table 只认列顺序，不认列名）
FREQ_HEADER: tuple[str, str, str] = ("文件名", "全寿命发生次数", "仿真时间（s）")

#: Romax 坐标系映射的**报文形态**（前端传上来的样子）：romax z ← 原始 y，romax y ← 原始 -z
ROMAX_ORIGIN: list[dict[str, str]] = [
    {"romax": "x", "origin": "x"},
    {"romax": "y", "origin": "-z"},
    {"romax": "z", "origin": "y"},
]


def axis_mappings(payload: Sequence[Mapping[str, str]] = ROMAX_ORIGIN) -> list:
    """把报文形态的坐标映射转成内部的 AxisMapping（计算层收的类型）。"""
    from app_simpleLoad.core.config import AxisMapping

    return [AxisMapping(**dict(item)) for item in payload]


# ─── 数据类 ──────────────────────────────────────────────────

@dataclass
class CaseSpec:
    """一个工况（一个 txt 文件）的输入规格。

    Attributes:
        name:        文件名（不含 .txt）
        data:        列名 → 原始列值（**未做单位换算**，即 txt 里的字面值）
        occurrences: 频次表中的「全寿命发生次数」
        sim_time:    频次表中的「仿真时间（s）」；None 时由 Time[s] 列推断
    """

    name: str
    data: Mapping[str, Sequence[float]]
    occurrences: float = 100.0
    sim_time: float | None = None

    @property
    def row_count(self) -> int:
        return len(next(iter(self.data.values())))

    def raw(self, column: str) -> np.ndarray:
        """该列在 txt 中的原始值。"""
        return np.asarray(self.data[column], dtype=np.float64)

    def resolved_sim_time(self) -> float:
        """频次表里应写入的仿真时间。"""
        if self.sim_time is not None:
            return float(self.sim_time)
        if "Time[s]" not in self.data:
            raise ValueError(f"工况 {self.name} 既没有 sim_time 也没有 Time[s] 列")
        t = self.raw("Time[s]")
        return float(t[-1] - t[0])


@dataclass
class Dataset:
    """build_dataset 的产物：一份可直接喂给 CalSimpleLoad 的输入数据。"""

    root: Path
    load_dir: Path
    out_dir: Path
    freq_path: Path
    header: list[str]
    cases: list[CaseSpec]
    _by_name: dict[str, CaseSpec] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_name = {c.name: c for c in self.cases}

    # ── 便捷访问 ──────────────────────────────────────────
    def case(self, name: str) -> CaseSpec:
        return self._by_name[name]

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.cases]

    def txt_path(self, name: str) -> Path:
        return self.load_dir / f"{self.case(name).name}.txt"

    def path_config(self, *, keep_torque_component: bool = False):
        """转成 PathConfig（延迟 import，避免 factories 依赖业务模块）。"""
        from app_simpleLoad.core.config import PathConfig

        return PathConfig(
            result_folder_save_path=str(self.out_dir),
            load_file_folder_path=str(self.load_dir),
            freq_table_path=str(self.freq_path),
            keep_torque_component=keep_torque_component,
        )

    @property
    def excel_stem(self) -> str:
        """导出文件名中的 `{name}`，取自结果目录的 basename。"""
        return os.path.basename(str(self.out_dir))

    def gl_excel(self) -> Path:
        return self.out_dir / f"Load_Reduction_GL-{self.excel_stem}.xlsx"

    def romax_excel(self) -> Path:
        return self.out_dir / f"Load_Reduction_Romax-{self.excel_stem}.xlsx"

    # ── 期望值（供断言用，均按 README 公式手算） ─────────────
    def weights(self) -> dict[str, float]:
        """工况占比 w_j = T_j·N_j / Σ(T_k·N_k)"""
        products = {c.name: c.resolved_sim_time() * c.occurrences for c in self.cases}
        total = sum(products.values())
        return {name: value / total for name, value in products.items()}

    def total_weighted_time(self) -> float:
        """Σ(T_j·N_j)，单位秒（time(h) 的分子）。"""
        return sum(c.resolved_sim_time() * c.occurrences for c in self.cases)


# ─── 构造函数 ────────────────────────────────────────────────

def write_txt(
    path: Path,
    header: Sequence[str],
    data: Mapping[str, Sequence[float]],
    *,
    title_line: bool = True,
    extra_lines: Sequence[str] = (),
    fmt: str = "{:.6f}",
    sep: str = "\t",
) -> Path:
    """写一个时序载荷 txt。

    Args:
        header:      列顺序；data 中缺失的列（如 占位符）会填充 0
        title_line:  是否写标题行（对应 ConversionConfig.title_row=0）
        extra_lines: 标题行之后、数据行之前额外插入的行（模拟 END_OF_HEADER）
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n_rows = len(next(iter(data.values())))
    columns = [np.asarray(data.get(col, np.zeros(n_rows)), dtype=np.float64) for col in header]

    lines: list[str] = []
    if title_line:
        lines.append(sep.join(header))
    lines.extend(extra_lines)
    for i in range(n_rows):
        lines.append(sep.join(fmt.format(col[i]) for col in columns))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_freq_table(
    path: Path,
    names: Sequence[object],
    occurrences: Sequence[float],
    sim_times: Sequence[float] | None,
    *,
    columns: Sequence[str] = FREQ_HEADER,
) -> Path:
    """写频次表 xlsx。sim_times=None 时只写两列（用于测试列数不匹配）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Sequence[object]] = {
        columns[0]: list(names),
        columns[1]: list(occurrences),
    }
    if sim_times is not None:
        payload[columns[2]] = list(sim_times)
    pd.DataFrame(payload).to_excel(path, index=False)
    return path


def build_dataset(
    tmp_path: Path,
    cases: Sequence[CaseSpec],
    *,
    header: Sequence[str] = DEFAULT_HEADER,
    out_dir_name: str = "案例A",
    freq_names: Sequence[object] | None = None,
) -> Dataset:
    """在 tmp_path 下生成完整输入：load/*.txt + freq.xlsx + 结果目录。

    Args:
        freq_names: 覆盖频次表第一列（用于测试文件名不匹配 / 重复 / 空值）
    """
    root = Path(tmp_path)
    load_dir = root / "load"
    out_dir = root / out_dir_name
    load_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    for case in cases:
        write_txt(load_dir / f"{case.name}.txt", header, case.data)

    freq_path = root / "freq.xlsx"
    write_freq_table(
        freq_path,
        names=list(freq_names) if freq_names is not None else [c.name for c in cases],
        occurrences=[c.occurrences for c in cases],
        sim_times=[c.resolved_sim_time() for c in cases],
    )

    return Dataset(
        root=root,
        load_dir=load_dir,
        out_dir=out_dir,
        freq_path=freq_path,
        header=list(header),
        cases=list(cases),
    )


# ─── 常用工况模板 ────────────────────────────────────────────

def ramp_case(
    name: str,
    *,
    n_rows: int = 4,
    occurrences: float = 100.0,
    dt: float = 0.1,
    speed: float = 10.0,
    offset: float = 0.0,
    sign: float = 1.0,
    with_time: bool = True,
) -> CaseSpec:
    """线性递增的确定性工况：值好算、无随机性，便于手算期望值。

    第 i 行第 k 个分量的原始值 = sign × (1000·(k+1) + 100·i + offset)。
    """
    idx = np.arange(n_rows, dtype=np.float64)
    data: dict[str, Sequence[float]] = {"speed[rpm]": speed + idx}
    if with_time:
        data["Time[s]"] = idx * dt
    for k, col in enumerate(LOAD_COLUMNS):
        data[col] = sign * (1000.0 * (k + 1) + 100.0 * idx + offset)
    return CaseSpec(name=name, data=data, occurrences=occurrences)
