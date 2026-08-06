"""cal_simpleLoad 步骤三 —— 载荷缩减（simple_load2）

对应 README「步骤三：载荷缩减」：
    3.1 区间标签化   3.2 等效时间与转速   3.3 幂等效变换
    3.4 聚合与缩减   3.5 逆幂变换        3.6 等效时间

数值断言统一用 approx：生产链路是 float32 计算，参考实现是 float64，
两者本就有 ~1e-7 量级的相对差。
"""

import pytest

from app_simpleLoad.module.cal_simpleLoad import (
    ALL_COMPONENTS,
    CalSimpleLoad,
    parse_interval_table,
)
from tests import factories, reference
from tests.excel_io import assert_frames_close, read_gl

#: 默认 romax 映射（z←y）下参与打标签的分量，顺序即 table_data 的行顺序
SELECTED_COLUMNS = ["Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "Mz[KNm]"]

#: Fx 用三段区间把 c1 的四行拆成两组，其余分量只做正负二分
FX_BINS = [-100.0, 0.0, 4.15, 100.0]
BINARY_BINS = [-100.0, 0.0, 100.0]
DEFAULT_BINS = [FX_BINS, BINARY_BINS, BINARY_BINS, BINARY_BINS, BINARY_BINS]


def table_data(*bin_rows: list[float]) -> list[dict[str, str]]:
    """构造前端 tableData：每行是「列序号 → 边界值字符串」。"""
    return [{str(i): str(value) for i, value in enumerate(row)} for row in bin_rows]


@pytest.fixture
async def reduced(divided, dataset, romax_origin):
    """跑完默认参数的缩减，返回 (实例, 数据集, 返回计数, GL 表)。"""
    count = await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)
    return divided, dataset, count, read_gl(dataset.gl_excel())


# ─── 分量选择 ────────────────────────────────────────────────

class TestComponentSelection:
    @pytest.mark.parametrize(
        ("z_origin", "excluded", "kept"),
        [
            ("y", "my_label", ["fx_label", "fy_label", "fz_label", "mx_label", "mz_label"]),
            ("-y", "my_label", ["fx_label", "fy_label", "fz_label", "mx_label", "mz_label"]),
            ("z", "mz_label", ["fx_label", "fy_label", "fz_label", "mx_label", "my_label"]),
            ("x", "mx_label", ["fx_label", "fy_label", "fz_label", "my_label", "mz_label"]),
        ],
    )
    async def test_排除与_Romax_z_轴对应的力矩分量(
        self, divided, dataset, z_origin, excluded, kept
    ):
        romax_origin = factories.axis_mappings([
            {"romax": "x", "origin": "x"},
            {"romax": "y", "origin": "-z"},
            {"romax": "z", "origin": z_origin},
        ])

        await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        columns = read_gl(dataset.gl_excel()).columns
        assert excluded not in columns
        assert [c for c in columns if str(c).endswith("_label")] == kept

    async def test_区间行数与分量数不一致时报错(self, divided, dataset, romax_origin):
        """行与分量是按位置对齐的，行数不对会错位，所以直接拒绝。"""
        result = await divided.simple_load2(table_data(BINARY_BINS, BINARY_BINS), romax_origin)

        assert result["status"] == "error"
        assert "需要 5 行（fx/fy/fz/mx/mz）" in result["message"]
        assert "实际收到 2 行" in result["message"]
        assert not dataset.gl_excel().exists()

    async def test_区间里的空字符串被忽略(self, divided, dataset, romax_origin):
        rows = [{"0": "-100", "1": "0", "2": "100", "3": ""} for _ in SELECTED_COLUMNS]

        await divided.simple_load2(rows, romax_origin)

        frame = read_gl(dataset.gl_excel())
        assert frame["fx_label"].isin(["fx1", "fx2", "fx3"]).all()


# ─── 区间表与分量的对齐 ──────────────────────────────────────

DEFAULT_SELECTED = [comp for comp in ALL_COMPONENTS if comp[1] in SELECTED_COLUMNS]


def component_table(*pairs: tuple[str, list[float]]) -> list[dict]:
    """构造带分量名的 tableData（前端新版报文形态）。"""
    return [
        {"component": column, **{str(i): str(value) for i, value in enumerate(bins)}}
        for column, bins in pairs
    ]


DEFAULT_COMPONENT_ROWS = component_table(*zip(SELECTED_COLUMNS, DEFAULT_BINS))


class TestParseIntervalTable:
    """按分量名对齐（新前端）与按位置对齐（老前端）都要认。"""

    def test_按分量名对齐(self):
        bins, error = parse_interval_table(DEFAULT_COMPONENT_ROWS, DEFAULT_SELECTED)

        assert error == ""
        assert bins == DEFAULT_BINS

    def test_行序打乱也能对上(self):
        """这正是按名字对齐的意义：行序不再是隐含契约。"""
        shuffled = list(reversed(DEFAULT_COMPONENT_ROWS))

        bins, error = parse_interval_table(shuffled, DEFAULT_SELECTED)

        assert error == ""
        assert bins == DEFAULT_BINS

    def test_列号乱序按列号排序(self):
        rows = [{"component": col, "2": "100", "0": "-100", "1": "0"} for col in SELECTED_COLUMNS]

        bins, _ = parse_interval_table(rows, DEFAULT_SELECTED)

        assert bins[0] == [-100.0, 0.0, 100.0]

    def test_忽略_component_之外的附加字段(self):
        rows = [dict(row, index=3, foo="bar") for row in DEFAULT_COMPONENT_ROWS]

        bins, error = parse_interval_table(rows, DEFAULT_SELECTED)

        assert error == ""
        assert bins == DEFAULT_BINS

    def test_缺分量(self):
        bins, error = parse_interval_table(DEFAULT_COMPONENT_ROWS[:-1], DEFAULT_SELECTED)

        assert bins is None
        assert error == "区间表缺少分量：Mz[KNm]"

    def test_多了不参与分组的分量(self):
        rows = DEFAULT_COMPONENT_ROWS + component_table(("My[KNm]", BINARY_BINS))

        bins, error = parse_interval_table(rows, DEFAULT_SELECTED)

        assert bins is None
        assert error == "区间表包含未参与分组的分量：My[KNm]"

    def test_分量重复(self):
        rows = DEFAULT_COMPONENT_ROWS + component_table(("Fx[KN]", BINARY_BINS))

        bins, error = parse_interval_table(rows, DEFAULT_SELECTED)

        assert bins is None
        assert error == "区间表中分量 Fx[KN] 出现多次"

    def test_分量名无法识别(self):
        rows = component_table(("Fx[KN]", BINARY_BINS), ("扭矩", BINARY_BINS))

        bins, error = parse_interval_table(rows, DEFAULT_SELECTED)

        assert bins is None
        assert error == "区间表中的分量名无法识别：扭矩"

    def test_半带标签直接拒绝(self):
        rows = [dict(DEFAULT_COMPONENT_ROWS[0])] + [{"0": "-100", "1": "100"}]

        bins, error = parse_interval_table(rows, DEFAULT_SELECTED)

        assert bins is None
        assert error == "区间表里有的行带分量名、有的不带，请统一"

    def test_区间值不是数字(self):
        rows = component_table(*zip(SELECTED_COLUMNS, DEFAULT_BINS))
        rows[1]["1"] = "很大"

        bins, error = parse_interval_table(rows, DEFAULT_SELECTED)

        assert bins is None
        assert error == "Fy[KN]的区间值必须是数字"

    def test_老前端按位置对齐(self):
        bins, error = parse_interval_table(table_data(*DEFAULT_BINS), DEFAULT_SELECTED)

        assert error == ""
        assert bins == DEFAULT_BINS

    def test_老前端行数不符(self):
        bins, error = parse_interval_table(table_data(*DEFAULT_BINS[:2]), DEFAULT_SELECTED)

        assert bins is None
        assert "需要 5 行（fx/fy/fz/mx/mz）" in error
        assert "实际收到 2 行" in error

    def test_老前端区间值不是数字(self):
        rows = table_data(*DEFAULT_BINS)
        rows[0]["1"] = "很大"

        bins, error = parse_interval_table(rows, DEFAULT_SELECTED)

        assert bins is None
        assert error == "区间值必须是数字"

    def test_行不是对象(self):
        bins, error = parse_interval_table(["不是一行"], DEFAULT_SELECTED)

        assert bins is None
        assert error == "区间表里有格式不正确的行"


class TestReduceWithComponentRows:
    """带分量名的报文跑完整缩减。"""

    async def test_与按位置的结果完全一致(self, divided, dataset, romax_origin, config):
        await divided.simple_load2(DEFAULT_COMPONENT_ROWS, romax_origin)
        by_component = read_gl(dataset.gl_excel())

        await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)
        by_position = read_gl(dataset.gl_excel())

        assert_frames_close(by_component, by_position, rel=1e-9)

    async def test_行序打乱结果不变(self, divided, dataset, romax_origin):
        await divided.simple_load2(DEFAULT_COMPONENT_ROWS, romax_origin)
        normal = read_gl(dataset.gl_excel())

        await divided.simple_load2(list(reversed(DEFAULT_COMPONENT_ROWS)), romax_origin)

        assert_frames_close(read_gl(dataset.gl_excel()), normal, rel=1e-9)

    async def test_缺分量时报错且不写文件(self, divided, dataset, romax_origin):
        result = await divided.simple_load2(DEFAULT_COMPONENT_ROWS[:-1], romax_origin)

        assert result == {"message": "区间表缺少分量：Mz[KNm]", "status": "error"}
        assert not dataset.gl_excel().exists()

    async def test_开关打开时必须给满六个分量(self, dataset, config, romax_origin):
        instance = CalSimpleLoad()
        instance.setInit(
            paths=dataset.path_config(keep_torque_component=True),
            header=dataset.header,
            config=config,
        )
        await instance.simple_Pre_processing()
        await instance.simple_load1(romax_origin)

        result = await instance.simple_load2(DEFAULT_COMPONENT_ROWS, romax_origin)

        assert result == {"message": "区间表缺少分量：My[KNm]", "status": "error"}


# ─── 保留绕转轴力矩分量的开关 ────────────────────────────────

#: 开关打开后参与打标签的六个分量，顺序即 tableData 的行顺序
ALL_COMPONENT_COLUMNS = ["Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]", "Mz[KNm]"]
SIX_ROW_BINS = [FX_BINS] + [BINARY_BINS] * 5


class TestKeepTorqueComponent:
    """PathConfig.keep_torque_component=True → 六个分量一视同仁。"""

    @pytest.fixture
    async def divided_keep(self, dataset, config, romax_origin):
        instance = CalSimpleLoad()
        instance.setInit(
            paths=dataset.path_config(keep_torque_component=True),
            header=dataset.header,
            config=config,
        )
        await instance.simple_Pre_processing()
        await instance.simple_load1(romax_origin)
        return instance

    async def test_六个分量都参与分组(self, divided_keep, dataset, romax_origin):
        await divided_keep.simple_load2(table_data(*SIX_ROW_BINS), romax_origin)

        frame = read_gl(dataset.gl_excel())

        assert [c for c in frame.columns if str(c).endswith("_label")] == [
            "fx_label", "fy_label", "fz_label", "mx_label", "my_label", "mz_label",
        ]
        assert "My[KNm]" in frame.columns          # 原本被丢掉的分量现在有输出列

    async def test_区间必须给满六行(self, divided_keep, romax_origin):
        result = await divided_keep.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        assert result["status"] == "error"
        assert "需要 6 行（fx/fy/fz/mx/my/mz）" in result["message"]
        assert "实际收到 5 行" in result["message"]

    async def test_算法与其余分量完全一致(self, divided_keep, dataset, config, romax_origin):
        """和参考实现对拍：多出来的分量走的是同一套幂等效流程。"""
        count = await divided_keep.simple_load2(table_data(*SIX_ROW_BINS), romax_origin)
        expected = reference.reduce_loads(
            reference.build_ref_cases(dataset, config),
            list(zip(ALL_COMPONENT_COLUMNS, SIX_ROW_BINS)),
            translate_factor=config.translate_factor,
            tol=config.tol,
        )

        assert count == expected.count_before_tol
        assert_frames_close(read_gl(dataset.gl_excel()), expected.frame)

    async def test_Romax_载荷表多出_Mz_一行(self, divided_keep, dataset, romax_origin):
        """保留的分量落在 Romax 的 z 轴力矩上，载荷表要给它一行。"""
        from tests.excel_io import read_romax, romax_load_matrix

        await divided_keep.simple_load2(table_data(*SIX_ROW_BINS), romax_origin)

        frame = read_gl(dataset.gl_excel())
        matrix = romax_load_matrix(read_romax(dataset.romax_excel()))

        assert list(matrix.index) == [
            "Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]", "Mz[KNm]",
        ]
        # romax z ← 原始 y，不带负号 → 直接取 GL 表里的 My
        assert matrix.loc["Mz[KNm]"].to_numpy(dtype=float) == pytest.approx(
            frame["My[KNm]"].to_numpy(), rel=1e-9
        )

    async def test_关闭时_Romax_仍是五行(self, divided, dataset, romax_origin):
        from tests.excel_io import read_romax, romax_load_matrix

        await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        matrix = romax_load_matrix(read_romax(dataset.romax_excel()))
        assert list(matrix.index) == ["Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]"]

    async def test_关闭时行为不变(self, divided, dataset, romax_origin):
        """默认值必须保持现状：仍然排除、仍然是五行。"""
        await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        frame = read_gl(dataset.gl_excel())
        assert "my_label" not in frame.columns
        assert "My[KNm]" not in frame.columns


# ─── 区间标签化（3.1） ───────────────────────────────────────

class TestLabels:
    async def test_标签由区间序号加一得到(self, reduced):
        _, _, _, frame = reduced

        # c1 的 Fx≈4.0~4.3 落在 (0, 4.15] 和 (4.15, 100] 两段 → fx2 / fx3
        # c2 的 Fx≈-4.5~-4.8 落在 (-100, 0] → fx1
        assert set(frame["fx_label"]) == {"fx1", "fx2", "fx3"}
        assert set(frame["mz_label"]) == {"mz1", "mz2"}

    async def test_超出区间的值被裁剪到端点(self, divided, dataset, romax_origin):
        narrow = [[-1.0, 0.0, 1.0]] * 5           # 全部数据都在 ±1 之外

        await divided.simple_load2(table_data(*narrow), romax_origin)

        frame = read_gl(dataset.gl_excel())
        assert set(frame["fx_label"]) == {"fx1", "fx3"}   # 下越界→第 1 段，上越界→最后一段

    async def test_区间非单调时返回错误(self, divided, romax_origin):
        bad = [[10.0, 0.0, 20.0]] + [BINARY_BINS] * 4

        result = await divided.simple_load2(table_data(*bad), romax_origin)

        assert result == {"message": "Fx[KN]的区间值必须是单调递增的", "status": "error"}

    async def test_区间非单调时不写出文件(self, divided, dataset, romax_origin):
        bad = [BINARY_BINS, [10.0, 0.0]] + [BINARY_BINS] * 3

        await divided.simple_load2(table_data(*bad), romax_origin)

        assert not dataset.gl_excel().exists()


# ─── 数值正确性（3.2~3.6） ──────────────────────────────────

class TestNumbers:
    async def test_与参考实现逐格一致(self, reduced, config, romax_origin):
        _, dataset, count, frame = reduced
        expected = reference.reduce_loads(
            reference.build_ref_cases(dataset, config),
            list(zip(SELECTED_COLUMNS, DEFAULT_BINS)),
            translate_factor=config.translate_factor,
            tol=config.tol,
        )

        assert count == expected.count_before_tol
        assert_frames_close(frame, expected.frame)

    async def test_返回值是_tol_过滤前的分组数(self, reduced):
        _, _, count, frame = reduced

        assert count == len(frame)          # 默认 tol 极小，未过滤掉任何分组

    async def test_时间占比之和为一(self, reduced):
        _, _, _, frame = reduced

        assert frame["时间占比"].sum() == pytest.approx(1.0, rel=1e-6)

    async def test_等效时间等于占比乘以全寿命总时长(self, reduced, dataset):
        _, _, _, frame = reduced
        total_hours = dataset.total_weighted_time() / 3600.0

        assert frame["time(h)"].tolist() == pytest.approx(
            (frame["时间占比"] * total_hours).tolist(), rel=1e-5
        )

    async def test_等效时间总和等于全寿命小时数(self, reduced, dataset):
        _, _, _, frame = reduced

        assert frame["time(h)"].sum() == pytest.approx(
            dataset.total_weighted_time() / 3600.0, rel=1e-5
        )

    async def test_转速为格子转速加权平均且恒为正(self, reduced, dataset, config):
        _, _, _, frame = reduced

        assert (frame["speed[rpm]"] > 0).all()
        # 全部分组合起来的加权平均，应等于各工况转速按格子转速加权的总平均
        cases = reference.build_ref_cases(dataset, config)
        numerator = sum(
            (abs(c.values["speed[rpm]"]) ** 2 * c.sample_interval * c.occurrences).sum()
            for c in cases
        )
        denominator = sum(
            (abs(c.values["speed[rpm]"]) * c.sample_interval * c.occurrences).sum()
            for c in cases
        )
        overall = (frame["speed[rpm]"] * frame["格子转速"]).sum() / frame["格子转速"].sum()
        assert overall == pytest.approx(numerator / denominator, rel=1e-5)

    async def test_幂等效变换往返(self, divided, dataset, romax_origin, config):
        """所有行归为一组时，结果就是 (Σ|F|^m·R / ΣR)^(1/m)。"""
        one_group = [[-100.0, 100.0]] * 5

        await divided.simple_load2(table_data(*one_group), romax_origin)

        frame = read_gl(dataset.gl_excel())
        assert len(frame) == 1

        cases = reference.build_ref_cases(dataset, config)
        m = config.translate_factor
        weighted, weights = 0.0, 0.0
        for case in cases:
            grid_speed = abs(case.values["speed[rpm]"]) * case.sample_interval * case.occurrences
            weighted += (reference.power_transform(case.values["Fx[KN]"], m) * grid_speed).sum()
            weights += grid_speed.sum()
        expected = reference.inverse_power_transform(
            (weighted / weights).reshape(1), m
        )[0]

        assert frame["Fx[KN]"].iloc[0] == pytest.approx(expected, rel=1e-5)


# ─── 过滤（3.4） ─────────────────────────────────────────────

class TestTolFiltering:
    async def test_占比低于_tol_的分组被剔除(self, divided, dataset, romax_origin):
        divided.config.tol = 0.5              # 只保留占比 > 50% 的分组

        count = await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        frame = read_gl(dataset.gl_excel())
        assert count == 3                     # 返回值仍是过滤前的分组数
        assert len(frame) == 1                # 实际导出只剩占比最大的一组
        assert frame["时间占比"].iloc[0] > 0.5

    async def test_tol_为零时全部保留(self, divided, dataset, romax_origin):
        divided.config.tol = 0.0

        count = await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        assert len(read_gl(dataset.gl_excel())) == count


# ─── 排序与工况编号（导出约定） ──────────────────────────────

class TestOrderingAndNaming:
    async def test_工况号按标签排序后连续编号(self, reduced):
        _, _, _, frame = reduced

        assert frame["工况"].tolist() == [f"loc{i + 1:03d}" for i in range(len(frame))]

    async def test_按标签层级升序排列(self, reduced):
        _, _, _, frame = reduced
        label_columns = [c for c in frame.columns if str(c).endswith("_label")]

        ordered = frame[label_columns].values.tolist()
        assert ordered == sorted(ordered)

    async def test_工况号三位补零(self, divided, dataset, romax_origin):
        await divided.simple_load2(table_data(*[[-100.0, 100.0]] * 5), romax_origin)

        frame = read_gl(dataset.gl_excel())
        assert frame["工况"].iloc[0] == "loc001"


# ─── 错误分支 ────────────────────────────────────────────────

class TestErrors:
    async def test_未加载数据时返回错误提示(self, instance, romax_origin):
        result = await instance.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        assert result == {"message": "请先加载文件", "status": "error"}

    async def test_romax_映射缺失时抛_IndexError(self, divided):
        """routes 会兜住这个异常并返回「载荷缩减失败」。"""
        with pytest.raises(IndexError):
            await divided.simple_load2(table_data(*DEFAULT_BINS), [])

    async def test_结果目录不存在时抛_OSError(self, divided, romax_origin, tmp_path):
        divided.paths.result_folder_save_path = str(tmp_path / "不存在的目录")

        with pytest.raises(OSError):
            await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

    async def test_连续两次缩减结果一致(self, divided, dataset, romax_origin):
        """前端可能反复调整区间重算，重复调用不应互相污染。"""
        first_count = await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)
        first = read_gl(dataset.gl_excel())

        second_count = await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)
        second = read_gl(dataset.gl_excel())

        assert first_count == second_count
        assert_frames_close(first, second, rel=1e-9)


# ─── 进度推送 ────────────────────────────────────────────────

class TestProgress:
    async def test_推送缩减各阶段进度(self, divided, romax_origin, connected_ws, instant_sleep):
        from app_simpleLoad.core import progress as progress_module

        instant_sleep(progress_module)

        await divided.simple_load2(table_data(*DEFAULT_BINS), romax_origin)

        assert "开始载荷缩减处理..." in connected_ws.texts
        assert "正在保存Excel文件..." in connected_ws.texts
        assert connected_ws.texts[-1] == "载荷缩减处理全部完成！"
        assert connected_ws.progresses[-1] == pytest.approx(100.0)
