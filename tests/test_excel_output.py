"""导出契约 —— GL 与 Romax 两个 Excel 的结构

这些格式是下游（Romax 仿真）直接消费的，重构时结构不能变：
文件名、sheet 名、列顺序、坐标轴映射与正负号。
"""

import pytest

from tests.excel_io import read_gl, read_romax, romax_load_matrix
from tests.reference import romax_column_source
from tests.test_reduce_load import DEFAULT_BINS, table_data

ROMAX_LOAD_ROWS = ["Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]"]


@pytest.fixture
async def exported(divided, dataset, romax_origin):
    """跑完一次缩减，返回 (数据集, GL 表, Romax 各 sheet)。"""
    await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)
    return dataset, read_gl(dataset.gl_excel()), read_romax(dataset.romax_excel())


# ─── 文件与 sheet ────────────────────────────────────────────

class TestFiles:
    async def test_文件名取自结果目录名(self, exported):
        dataset, _, _ = exported

        assert dataset.gl_excel().name == "Load_Reduction_GL-案例A.xlsx"
        assert dataset.romax_excel().name == "Load_Reduction_Romax-案例A.xlsx"
        assert dataset.gl_excel().exists() and dataset.romax_excel().exists()

    async def test_Romax_文件含四个_sheet(self, dataset, divided, romax_origin):
        import pandas as pd

        await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        with pd.ExcelFile(dataset.romax_excel()) as xl:
            assert xl.sheet_names == ["工况表格定义", "载荷", "未转置", "已转置"]


# ─── GL 表结构 ───────────────────────────────────────────────

class TestGLLayout:
    async def test_列顺序固定(self, exported):
        _, frame, _ = exported

        assert list(frame.columns) == [
            "fx_label", "fy_label", "fz_label", "mx_label", "mz_label",
            "time(h)", "speed[rpm]",
            "Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "Mz[KNm]",
            "时间占比", "格子转速", "工况",
        ]

    async def test_标签列写成合并单元格(self, divided, dataset, romax_origin):
        """merge_cells=True：首层标签连续相同时只写一次，读回来是 NaN。"""
        import pandas as pd

        # Fx 二分（c1 全落在 fx2），Fy 三分把 c1 再拆成两组 → fx2 连续出现两次
        bins = [[-100.0, 0.0, 100.0], [-100.0, 0.0, 5.15, 100.0]] + [[-100.0, 0.0, 100.0]] * 3
        await divided.simple_load2(table_data(*bins), romax_origin)

        raw = pd.read_excel(dataset.gl_excel())
        assert raw["fx_label"].tolist()[:2] == ["fx1", "fx2"]
        assert pd.isna(raw["fx_label"].iloc[2])          # 与上一行合并
        assert read_gl(dataset.gl_excel())["fx_label"].tolist() == ["fx1", "fx2", "fx2"]


# ─── Romax 表结构 ────────────────────────────────────────────

class TestRomaxLayout:
    async def test_工况表格定义只有四列(self, exported):
        _, gl, sheets = exported
        definition = sheets["工况表格定义"]

        assert list(definition.columns) == ["工况", "time(h)", "温度(C)", "speed[rpm]"]
        assert definition["工况"].tolist() == gl["工况"].tolist()
        assert definition["time(h)"].tolist() == pytest.approx(gl["time(h)"].tolist())

    async def test_温度取自配置(self, divided, dataset, romax_origin):
        divided.config.temperature = 55.0

        await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        assert (read_romax(dataset.romax_excel())["工况表格定义"]["温度(C)"] == 55.0).all()

    async def test_载荷_sheet_是分量乘工况的矩阵(self, exported):
        _, gl, sheets = exported
        matrix = romax_load_matrix(sheets)

        assert list(matrix.index) == ROMAX_LOAD_ROWS
        assert list(matrix.columns) == gl["工况"].tolist()

    async def test_已转置与载荷内容一致(self, exported):
        _, _, sheets = exported

        transposed = sheets["已转置"]
        assert transposed.iloc[1:, 0].tolist() == ["工况"] + ROMAX_LOAD_ROWS[:-1] + [ROMAX_LOAD_ROWS[-1]]


# ─── 坐标系映射 ──────────────────────────────────────────────

class TestRomaxAxisMapping:
    async def test_按_romax_origin_取列并处理正负号(self, exported, romax_origin):
        _, gl, sheets = exported
        matrix = romax_load_matrix(sheets)

        for column in ROMAX_LOAD_ROWS:
            source, negate = romax_column_source(column, romax_origin)
            if source not in gl.columns:
                continue
            expected = gl[source].to_numpy() * (-1.0 if negate else 1.0)
            assert matrix.loc[column].to_numpy(dtype=float) == pytest.approx(expected, rel=1e-9), column

    async def test_原始列缺失时补零(self, divided, dataset, romax_origin):
        """只给两行区间 → 结果里没有 Fz/Mx/My，Romax 对应列填 0。"""
        await divided.simple_load2(table_data(*DEFAULT_BINS[:2]), romax_origin)

        matrix = romax_load_matrix(read_romax(dataset.romax_excel()))

        assert (matrix.loc["Mx[KNm]"].to_numpy(dtype=float) == 0).all()
        assert (matrix.loc["My[KNm]"].to_numpy(dtype=float) == 0).all()

    async def test_恒等映射时不反号(self, divided, dataset):
        identity = [
            {"romax": "x", "origin": "x"},
            {"romax": "y", "origin": "y"},
            {"romax": "z", "origin": "z"},
        ]

        await divided.simple_load2(table_data(*DEFAULT_BINS), identity)

        gl = read_gl(dataset.gl_excel())
        matrix = romax_load_matrix(read_romax(dataset.romax_excel()))
        assert matrix.loc["Fx[KN]"].to_numpy(dtype=float) == pytest.approx(
            gl["Fx[KN]"].to_numpy(), rel=1e-9
        )
        assert matrix.loc["Fy[KN]"].to_numpy(dtype=float) == pytest.approx(
            gl["Fy[KN]"].to_numpy(), rel=1e-9
        )
