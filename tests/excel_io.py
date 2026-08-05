"""导出结果的读取与比较工具

导出用了 MultiIndex + merge_cells=True，读回来时被合并的单元格是 NaN，
这里统一 ffill 还原，免得每个测试都写一遍。
"""

from pathlib import Path

import pandas as pd
import pytest


def read_gl(path: Path | str) -> pd.DataFrame:
    """读 `Load_Reduction_GL-*.xlsx`，标签列回填为完整值。"""
    frame = pd.read_excel(path)
    label_columns = [c for c in frame.columns if str(c).endswith("_label")]
    if label_columns:
        frame[label_columns] = frame[label_columns].ffill()
    return frame


def read_romax(path: Path | str) -> dict[str, pd.DataFrame]:
    """读 `Load_Reduction_Romax-*.xlsx` 的四个 sheet。

    「载荷」「已转置」是转置视图（无表头），按原样读成位置索引的表。
    """
    with pd.ExcelFile(path) as xl:
        return {
            "工况表格定义": pd.read_excel(xl, sheet_name="工况表格定义"),
            "载荷": pd.read_excel(xl, sheet_name="载荷", header=None),
            "未转置": pd.read_excel(xl, sheet_name="未转置", index_col=0),
            "已转置": pd.read_excel(xl, sheet_name="已转置", header=None),
        }


def assert_frames_close(actual: pd.DataFrame, expected: pd.DataFrame, *, rel: float = 1e-5) -> None:
    """逐列比较两张结果表：数值列按相对误差比，文本列（标签/工况）要求完全一致。

    不能用 `DataFrame.equals`：聚合求和的次序在 Polars 里不保证稳定，
    连续两次同参数计算也可能在最后一位有效数字上有差异。
    """
    assert list(actual.columns) == list(expected.columns)
    assert len(actual) == len(expected)
    for column in expected.columns:
        if pd.api.types.is_numeric_dtype(expected[column]):
            assert actual[column].tolist() == pytest.approx(
                expected[column].tolist(), rel=rel
            ), column
        else:
            assert actual[column].tolist() == expected[column].tolist(), column


def romax_load_matrix(sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """把「载荷」sheet 整理成 index=分量名、columns=工况号 的矩阵。"""
    raw = sheets["载荷"]
    matrix = raw.set_index(0)
    matrix.columns = list(raw.iloc[0, 1:])
    matrix.index.name = None
    return matrix.iloc[1:]
