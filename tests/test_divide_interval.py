"""cal_simpleLoad 步骤二 —— 划分区间（simple_load1）与直方图（savePic）

对应 README「步骤二：划分区间与直方图」：
    2.1 区间划分（跨零 100+100 段 / 同号 200 段）
    2.2 加权直方图 H(b) = Σ_j w_j · c_j(b) / n_rows,j
"""

import json

import numpy as np
import polars as pl
import pytest

from app_simpleLoad.module.cal_simpleLoad import CalSimpleLoad
from tests import factories, reference
from tests.factories import build_dataset, ramp_case

ALL_LOAD_COLUMNS = ["Mx[KNm]", "My[KNm]", "Mz[KNm]", "Fx[KN]", "Fy[KN]", "Fz[KN]"]


def make_frame(values: dict[str, list[float]], names: list[str]) -> pl.DataFrame:
    """直接拼一个 df_all，跳过文件读取，专注测试区间划分。"""
    payload = {col: pl.Series(vals, dtype=pl.Float32) for col, vals in values.items()}
    payload["文件名"] = pl.Series(names, dtype=pl.String)
    return pl.DataFrame(payload)


# ─── simple_load1：最值与区间 ────────────────────────────────

class TestSimpleLoad1:
    async def test_返回各分量最值的_JSON(self, divided):
        payload = json.loads(await divided.simple_load1([]))

        assert set(payload) == {"min", "max"}
        assert set(payload["min"]) == set(ALL_LOAD_COLUMNS)
        assert set(payload["max"]) == set(ALL_LOAD_COLUMNS)

    async def test_最值向下向上取整(self, loaded, romax_origin):
        payload = json.loads(await loaded.simple_load1(romax_origin))

        for column in ALL_LOAD_COLUMNS:
            raw = loaded.df_all[column].to_numpy()
            assert payload["min"][column] == pytest.approx(np.floor(raw.min()))
            assert payload["max"][column] == pytest.approx(np.ceil(raw.max()))

    async def test_为六个分量都生成区间(self, divided):
        assert set(divided.max_min) == set(ALL_LOAD_COLUMNS)

    async def test_区间边界为整数且共_200_个(self, divided):
        for column, bins in divided.max_min.items():
            assert len(bins) == 200, column
            assert np.issubdtype(bins.dtype, np.integer), column
            assert np.all(np.diff(bins) >= 0), column

    async def test_跨零时负侧与正侧各占一半(self, loaded, romax_origin):
        """min<0<max：前 100 个边界落在负侧，后 100 个从 0 开始。"""
        await loaded.simple_load1(romax_origin)
        bins = loaded.max_min["Fx[KN]"]
        raw = loaded.df_all["Fx[KN]"].to_numpy()
        assert raw.min() < 0 < raw.max()

        assert bins[0] == np.floor(raw.min() / 100) * 100
        assert bins[99] == -1                       # 负侧不含 0
        assert bins[100] == 0                       # 正侧从 0 开始
        assert bins[-1] == np.ceil(raw.max() / 100) * 100

    async def test_同号时整体均分(self, instance, romax_origin):
        instance.df_all = make_frame(
            {col: [150.0, 250.0, 350.0] for col in ALL_LOAD_COLUMNS},
            ["c1", "c1", "c1"],
        )

        await instance.simple_load1(romax_origin)

        bins = instance.max_min["Fx[KN]"]
        assert bins[0] == 100        # floor(150/100)*100
        assert bins[-1] == 400       # ceil(350/100)*100
        assert 0 not in bins

    async def test_与参考实现的分箱规则一致(self, instance, romax_origin):
        instance.df_all = make_frame(
            {col: [-1234.0, 5678.0] for col in ALL_LOAD_COLUMNS}, ["c1", "c1"]
        )

        await instance.simple_load1(romax_origin)

        expected = reference.create_bins(-1234.0, 5678.0)
        np.testing.assert_array_equal(instance.max_min["Mz[KNm]"], expected)

    async def test_未加载数据时返回_None(self, instance):
        assert await instance.simple_load1([]) is None

    async def test_df_dest_记录每个分量的最值(self, divided):
        assert divided.df_dest.columns == ["column", "min", "max"]
        assert divided.df_dest["column"].to_list() == [
            "Mx[KNm]", "My[KNm]", "Mz[KNm]", "Fx[KN]", "Fy[KN]", "Fz[KN]",
        ]


# ─── savePic：加权直方图 ─────────────────────────────────────

class TestSavePic:
    @pytest.fixture
    async def histogram_instance(self, tmp_path, config, romax_origin):
        """数据点刻意避开整数分箱边界，保证落点判定稳定。"""
        dataset = build_dataset(
            tmp_path,
            [
                ramp_case("c1", n_rows=4, occurrences=100.0, offset=50.0),
                ramp_case("c2", n_rows=4, occurrences=200.0, offset=50.0, sign=-1.0),
            ],
        )
        instance = CalSimpleLoad()
        instance.setInit(paths=dataset.path_config(), header=dataset.header, config=config)
        await instance.simple_Pre_processing()
        await instance.simple_load1(romax_origin)
        return instance, dataset

    async def test_六个分量都有数据(self, divided):
        result = await divided.savePic()

        assert set(result) == set(ALL_LOAD_COLUMNS)

    async def test_每个分量是区间到频次的_JSON(self, divided):
        result = await divided.savePic()

        payload = json.loads(result["Fx[KN]"])
        assert payload, "至少应保留一个区间"
        for key, value in payload.items():
            assert key.startswith("(") and key.endswith("]")   # 左开右闭区间标签
            assert 0 <= value <= 1

    async def test_频次为工况加权且总和为一(self, histogram_instance):
        instance, _ = histogram_instance

        result = await instance.savePic()

        for column in ALL_LOAD_COLUMNS:
            total = sum(json.loads(result[column]).values())
            assert total == pytest.approx(1.0, rel=1e-6), column

    async def test_与参考实现的加权直方图一致(self, histogram_instance, config):
        instance, dataset = histogram_instance
        column = "Fy[KN]"
        bins = instance.max_min[column]
        values_by_case = {
            case.name: reference.convert_units(case.raw(column), column, config)
            for case in dataset.cases
        }
        expected_hist = reference.weighted_histogram(values_by_case, bins, dataset.weights())

        payload = json.loads((await instance.savePic())[column])

        expected = {
            f"({bins[i]}, {bins[i + 1]}]": value
            for i, value in enumerate(expected_hist)
            if value >= 1e-4
        }
        assert payload.keys() == expected.keys()
        for key in expected:
            assert payload[key] == pytest.approx(expected[key], rel=1e-6)

    async def test_低于阈值的区间被丢弃(self, instance, romax_origin):
        """占比 < 1e-4 的区间不返回给前端（否则图表点太多）。"""
        many = 20000
        values = [1.5] * (many - 1) + [90.5]        # 后者占比 1/20000 = 5e-5
        instance.df_all = make_frame({col: list(values) for col in ALL_LOAD_COLUMNS}, ["c1"] * many)
        instance.df_ref = pl.DataFrame({"文件名": ["c1"], "工况占比": [1.0]})
        await instance.simple_load1(romax_origin)

        payload = json.loads((await instance.savePic())["Fx[KN]"])

        assert len(payload) == 1
        assert list(payload.values())[0] == pytest.approx((many - 1) / many)

    async def test_未加载数据时返回错误提示(self, instance):
        assert await instance.savePic() == {"message": "请先加载文件", "status": "error"}

    async def test_未划分区间就画图会抛_AttributeError(self, loaded):
        """routes 依赖这个异常返回「请先加载文件」，改动时要同步调整路由。"""
        with pytest.raises(AttributeError, match="max_min"):
            await loaded.savePic()

    async def test_推送图表生成进度(self, divided, connected_ws, instant_sleep):
        from app_simpleLoad.core import progress as progress_module

        instant_sleep(progress_module)

        await divided.savePic()

        assert "开始生成图表数据..." in connected_ws.texts
        assert "已处理 Fx[KN] 列数据" in connected_ws.texts
        assert connected_ws.texts[-1] == "图表数据生成完成"
