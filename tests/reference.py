"""参考实现 —— 按 README「计算流程与算法」一节独立重写的算法

这是测试的「第二套实现」：只依赖 numpy/pandas，按文档公式直译，不复用
`app_simpleLoad` 的任何计算代码。用它和生产代码对跑同一份数据，可以在
重构（改 Polars 写法 / 换 LazyFrame / 拆 service）时立刻发现数值漂移。

公式编号与 README 保持一致，便于对照：
    1.1 单位转换      1.2 工况占比      1.3 采样间隔
    2.1 区间划分      2.2 加权直方图
    3.1 区间标签化    3.2 等效时间与转速   3.3 幂等效变换
    3.4 聚合与缩减    3.5 逆幂变换        3.6 等效时间
"""
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

MOMENT_COLUMNS = ("Mx[KNm]", "My[KNm]", "Mz[KNm]")
FORCE_COLUMNS = ("Fx[KN]", "Fy[KN]", "Fz[KN]")

#: 与 cal_simpleLoad.simple_load2 中 all_components 一致的分量清单
ALL_COMPONENTS: tuple[tuple[str, str, str], ...] = (
    ("fx", "Fx[KN]", "x"),
    ("fy", "Fy[KN]", "y"),
    ("fz", "Fz[KN]", "z"),
    ("mx", "Mx[KNm]", "x"),
    ("my", "My[KNm]", "y"),
    ("mz", "Mz[KNm]", "z"),
)


# ─── 1.1 单位转换 ────────────────────────────────────────────

def convert_units(raw: np.ndarray, column: str, config) -> np.ndarray:
    """M = M_raw / k_moment,  F = F_raw / k_force,  n = n_raw × k_speed"""
    values = np.asarray(raw, dtype=np.float64)
    if column in MOMENT_COLUMNS:
        return values / float(config.unit_moment)
    if column in FORCE_COLUMNS:
        return values / float(config.unit_force)
    if column == "speed[rpm]":
        return values * float(config.unit_speed)
    return values


# ─── 1.2 工况占比 / 1.3 采样间隔 ──────────────────────────────

def case_weights(sim_times: Sequence[float], occurrences: Sequence[float]) -> np.ndarray:
    """w_j = T_j·N_j / Σ(T_k·N_k)"""
    products = np.asarray(sim_times, dtype=np.float64) * np.asarray(occurrences, dtype=np.float64)
    return products / products.sum()


def sample_interval(sim_time: float, row_count: int) -> float:
    """Δt_j = T_j / (n_rows,j - 1)"""
    return float(sim_time) / (row_count - 1)


# ─── 2.1 区间划分 ────────────────────────────────────────────

def create_bins(min_val: float, max_val: float) -> np.ndarray:
    """跨零 → 负侧 100 段 + 正侧 100 段；同号 → 整体 200 段。

    注意生产代码用 `dtype=int` 生成边界，linspace 的浮点结果被**截断**
    （不是四舍五入），这里如实复刻，否则末尾几个边界会对不上。
    """
    if min_val * max_val < 0:
        return np.concatenate(
            [
                np.linspace(np.floor(min_val / 100) * 100, 0, 100, endpoint=False, dtype=int),
                np.linspace(0, np.ceil(max_val / 100) * 100, 100, endpoint=True, dtype=int),
            ]
        )
    return np.linspace(
        np.floor(min_val / 100) * 100,
        np.ceil(max_val / 100) * 100,
        200,
        endpoint=True,
        dtype=int,
    )


# ─── 2.2 加权直方图 ──────────────────────────────────────────

def weighted_histogram(
    values_by_case: Mapping[str, np.ndarray],
    bins: np.ndarray,
    weights: Mapping[str, float],
) -> np.ndarray:
    """H(b) = Σ_j w_j · c_j(b) / n_rows,j

    落点用 np.digitize（左闭右开）而不是 np.histogram，与生产代码一致。
    """
    hist = np.zeros(len(bins) - 1, dtype=np.float64)
    for name, values in values_by_case.items():
        indices = np.digitize(np.asarray(values, dtype=np.float64), bins) - 1
        counts = np.bincount(indices, minlength=len(bins) - 1)
        hist += counts / len(values) * weights[name]
    return hist


# ─── 3. 载荷缩减 ─────────────────────────────────────────────

def selected_components(romax_origin: Sequence[Mapping[str, str]]) -> list[tuple[str, str, str]]:
    """排除「与 Romax z 轴对应的原始轴」上的力矩分量。"""
    z_axis = romax_origin[2]["origin"].replace("-", "")
    return [
        comp for comp in ALL_COMPONENTS
        if not (comp[0].startswith("m") and comp[2] == z_axis)
    ]


def digitize_labels(values: np.ndarray, bins: Sequence[float]) -> np.ndarray:
    """ℓ = clip(digitize(F, bins) - 1, 0, len(bins) - 1)"""
    indices = np.digitize(np.asarray(values, dtype=np.float64), np.asarray(bins, dtype=np.float64)) - 1
    return np.clip(indices, 0, len(bins) - 1)


def power_transform(values: np.ndarray, m: float) -> np.ndarray:
    """F̃ = sgn(F)·|F|^m"""
    values = np.asarray(values, dtype=np.float64)
    return np.where(values < 0, -np.abs(values) ** m, values ** m)


def inverse_power_transform(values: np.ndarray, m: float) -> np.ndarray:
    """F = sgn(F̂)·|F̂|^(1/m)，0 保持 0"""
    values = np.asarray(values, dtype=np.float64)
    out = np.where(values < 0, -np.abs(values) ** (1.0 / m), np.abs(values) ** (1.0 / m))
    return np.where(values == 0, 0.0, out)


@dataclass
class RefCase:
    """参考实现的单工况输入（列值均为**已完成单位换算**的物理量）。"""

    name: str
    values: Mapping[str, np.ndarray]
    occurrences: float
    sim_time: float
    sample_interval: float | None = None

    def __post_init__(self) -> None:
        if self.sample_interval is None:
            self.sample_interval = sample_interval(self.sim_time, self.row_count)

    @property
    def row_count(self) -> int:
        return len(next(iter(self.values.values())))


@dataclass
class ReductionResult:
    """参考实现的输出，字段与 GL Excel 对齐。"""

    frame: pd.DataFrame
    count_before_tol: int
    label_columns: list[str] = field(default_factory=list)
    load_columns: list[str] = field(default_factory=list)


def reduce_loads(
    cases: Iterable[RefCase],
    component_bins: Sequence[tuple[str, Sequence[float]]],
    *,
    translate_factor: float,
    tol: float,
) -> ReductionResult:
    """按 README 步骤三做载荷缩减，返回与 GL 导出同构的表。

    Args:
        cases:           各工况已换算后的列数据
        component_bins:  [(列名, 用户分区边界), ...]，顺序即标签列顺序
        translate_factor: S-N 斜率 m
        tol:             时间占比阈值（严格大于才保留）
    """
    cases = list(cases)
    load_columns = [col for col, _ in component_bins]
    label_columns = [f"{col[:2].lower()}_label" for col in load_columns]

    # 3.1~3.3：逐工况计算标签、格子转速/时间、幂变换后的载荷
    buckets: dict[tuple[int, ...], dict[str, float]] = {}
    for case in cases:
        speed = np.abs(np.asarray(case.values["speed[rpm]"], dtype=np.float64))
        interval_life = case.sample_interval * case.occurrences        # Δt_life,j
        grid_speed = interval_life * speed                             # R_i
        grid_time = np.full(case.row_count, interval_life)             # T_i

        labels = np.stack(
            [digitize_labels(case.values[col], bins) for col, bins in component_bins],
            axis=1,
        )
        transformed = {
            col: power_transform(case.values[col], translate_factor) * grid_speed
            for col in load_columns
        }
        weighted_speed = speed * grid_speed

        for i in range(case.row_count):
            key = tuple(int(v) for v in labels[i])
            bucket = buckets.setdefault(
                key,
                {"格子转速": 0.0, "格子时间": 0.0, "处理后_speed[rpm]": 0.0,
                 **{f"处理后_{col}": 0.0 for col in load_columns}},
            )
            bucket["格子转速"] += grid_speed[i]
            bucket["格子时间"] += grid_time[i]
            bucket["处理后_speed[rpm]"] += weighted_speed[i]
            for col in load_columns:
                bucket[f"处理后_{col}"] += transformed[col][i]

    # 3.4：丢弃任一聚合列为 0 的分组（生产代码的 filter_expr）
    kept = {key: agg for key, agg in buckets.items() if all(v != 0 for v in agg.values())}
    count_before_tol = len(kept)

    # 时间占比 p_g（分母是过滤零值之后、tol 过滤之前的总量）
    total_grid_speed = sum(agg["格子转速"] for agg in kept.values())
    rows: list[dict[str, object]] = []
    total_time = sum(c.sim_time * c.occurrences for c in cases)

    for key, agg in kept.items():
        ratio = agg["格子转速"] / total_grid_speed
        if not ratio > tol:                                   # 3.4 过滤条件
            continue
        row: dict[str, object] = {name: key[i] for i, name in enumerate(label_columns)}
        row["speed[rpm]"] = agg["处理后_speed[rpm]"] / agg["格子转速"]
        for col in load_columns:                              # 3.5 逆幂变换
            row[col] = float(
                inverse_power_transform(
                    np.array([agg[f"处理后_{col}"] / agg["格子转速"]]), translate_factor
                )[0]
            )
        row["时间占比"] = ratio
        row["格子转速"] = agg["格子转速"]
        row["time(h)"] = ratio * total_time / 3600.0          # 3.6 等效时间
        rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return ReductionResult(frame, count_before_tol, label_columns, load_columns)

    # 排序 → 生成工况号 → 标签转成展示值（fx1/fy2/...），与导出约定一致
    frame = frame.sort_values(label_columns, kind="stable").reset_index(drop=True)
    frame["工况"] = [f"loc{i + 1:03d}" for i in range(len(frame))]
    for name in label_columns:
        prefix = name[:2]
        frame[name] = [f"{prefix}{int(v) + 1}" for v in frame[name]]

    ordered = label_columns + ["time(h)", "speed[rpm]"] + load_columns + ["时间占比", "格子转速", "工况"]
    return ReductionResult(frame[ordered], count_before_tol, label_columns, load_columns)


# ─── Romax 输出映射 ──────────────────────────────────────────

def build_ref_cases(dataset, config) -> list[RefCase]:
    """把合成数据集（原始值）换算成参考实现的输入。"""
    from tests import factories

    columns = [*factories.LOAD_COLUMNS, "speed[rpm]"]
    return [
        RefCase(
            name=case.name,
            values={col: convert_units(case.raw(col), col, config) for col in columns},
            occurrences=case.occurrences,
            sim_time=case.resolved_sim_time(),
        )
        for case in dataset.cases
    ]


def romax_column_source(column: str, romax_origin: Sequence[Mapping[str, str]]) -> tuple[str, bool]:
    """Romax 某列取自哪一原始列、是否需要反号。

    例：romax y ← 原始 -z，则 Romax 的 Fy[KN] = -Fz[KN]。
    """
    axis = column[1]
    origin = [item["origin"] for item in romax_origin if item["romax"] == axis][0]
    source = column.replace(axis, origin).replace("-", "")
    return source, "-" in origin
