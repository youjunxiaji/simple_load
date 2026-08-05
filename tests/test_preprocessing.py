"""cal_simpleLoad 步骤一 —— setInit 校验 与 加载/预处理

对应 README「计算流程与算法 · 步骤一：加载与预处理」：
单位转换、频次表关联、工况占比、采样间隔。
"""
import polars as pl
import pytest

from app_simpleLoad.core.config import ConversionConfig, FileParseError, PathConfig
from app_simpleLoad.module.cal_simpleLoad import CalSimpleLoad
from app_simpleLoad.services import file_reader
from tests import factories
from tests.factories import CaseSpec, build_dataset, ramp_case
from tests.reference import case_weights, convert_units, sample_interval

REQUIRED_COLUMNS = ["Mx[KNm]", "My[KNm]", "Mz[KNm]", "Fx[KN]", "Fy[KN]", "Fz[KN]", "speed[rpm]"]


# ─── setInit ─────────────────────────────────────────────────

class TestSetInit:
    def test_缺少必需列时报错并列出缺失项(self, dataset, config):
        instance = CalSimpleLoad()
        header = ["Time[s]", "Mx[KNm]", "My[KNm]"]

        with pytest.raises(ValueError) as excinfo:
            instance.setInit(paths=dataset.path_config(), header=header, config=config)

        message = str(excinfo.value)
        assert message.startswith("标题配置错误：缺少必需的列")
        for missing in ("Fx[KN]", "Fy[KN]", "Fz[KN]", "Mz[KNm]", "speed[rpm]"):
            assert missing in message

    @pytest.mark.parametrize("missing", REQUIRED_COLUMNS)
    def test_任一必需列缺失都会被拦下(self, dataset, config, missing):
        instance = CalSimpleLoad()
        header = [c for c in factories.DEFAULT_HEADER if c != missing]

        with pytest.raises(ValueError, match=missing.replace("[", r"\[")):
            instance.setInit(paths=dataset.path_config(), header=header, config=config)

    def test_Time_列可选(self, dataset, config):
        instance = CalSimpleLoad()
        header = [c for c in factories.DEFAULT_HEADER if c != "Time[s]"]

        instance.setInit(paths=dataset.path_config(), header=header, config=config)

        assert instance.header == header

    def test_允许占位符列(self, dataset, config):
        instance = CalSimpleLoad()
        header = factories.DEFAULT_HEADER + ["占位符1"]

        instance.setInit(paths=dataset.path_config(), header=header, config=config)

        assert "占位符1" in instance.header

    def test_保存配置并把数据槽位清空(self, dataset, config):
        instance = CalSimpleLoad()
        instance.df_all = pl.DataFrame({"a": [1]})

        instance.setInit(paths=dataset.path_config(), header=factories.DEFAULT_HEADER, config=config)

        assert instance.paths.freq_table_path == str(dataset.freq_path)
        assert instance.config is config
        assert instance.df_all is None
        assert instance.df_ref is None
        assert instance.df_dest is None


# ─── 预处理（有 Time 列） ────────────────────────────────────

class TestPreProcessingWithTime:
    async def test_合并所有工况数据(self, loaded, dataset):
        assert loaded.df_all.height == sum(c.row_count for c in dataset.cases)
        assert sorted(loaded.df_all["文件名"].unique().to_list()) == dataset.names

    async def test_单位换算已生效(self, loaded, dataset, config):
        c1 = dataset.case("c1")
        expected = convert_units(c1.raw("Fx[KN]"), "Fx[KN]", config)

        actual = (
            loaded.df_all.filter(pl.col("文件名") == "c1")["Fx[KN]"].to_list()
        )

        assert sorted(actual) == pytest.approx(sorted(expected.tolist()), rel=1e-6)

    async def test_参考表列齐全(self, loaded):
        assert set(loaded.df_ref.columns) == {
            "文件名", "全寿命发生次数", "载荷行数", "仿真时间（s）", "采样间隔（s）", "工况占比",
        }

    async def test_仿真时间取自_Time_列(self, loaded, dataset):
        df = loaded.df_ref.sort("文件名")

        assert df["仿真时间（s）"].to_list() == pytest.approx(
            [c.resolved_sim_time() for c in dataset.cases], rel=1e-6
        )

    async def test_采样间隔等于时间跨度除以行数减一(self, loaded, dataset):
        df = loaded.df_ref.sort("文件名")
        expected = [
            sample_interval(c.resolved_sim_time(), c.row_count) for c in dataset.cases
        ]

        assert df["采样间隔（s）"].to_list() == pytest.approx(expected, rel=1e-6)

    async def test_载荷行数与文件行数一致(self, loaded, dataset):
        df = loaded.df_ref.sort("文件名")

        assert df["载荷行数"].to_list() == [c.row_count for c in dataset.cases]

    async def test_工况占比按时间加权且总和为一(self, loaded, dataset):
        df = loaded.df_ref.sort("文件名")
        expected = case_weights(
            [c.resolved_sim_time() for c in dataset.cases],
            [c.occurrences for c in dataset.cases],
        )

        assert df["工况占比"].to_list() == pytest.approx(expected.tolist(), rel=1e-6)
        assert sum(df["工况占比"].to_list()) == pytest.approx(1.0)

    async def test_工况占比与发生次数成正比(self, loaded):
        ratios = {
            row["文件名"]: row["工况占比"] for row in loaded.df_ref.iter_rows(named=True)
        }

        # c2 的发生次数是 c1 的两倍，仿真时间相同
        assert ratios["c2"] / ratios["c1"] == pytest.approx(2.0, rel=1e-6)


# ─── 预处理（无 Time 列） ────────────────────────────────────

class TestPreProcessingWithoutTime:
    @pytest.fixture
    async def loaded_no_time(self, dataset_no_time, config):
        instance = CalSimpleLoad()
        instance.setInit(
            paths=dataset_no_time.path_config(),
            header=dataset_no_time.header,
            config=config,
        )
        await instance.simple_Pre_processing()
        return instance

    async def test_仿真时间取自频次表(self, loaded_no_time, dataset_no_time):
        df = loaded_no_time.df_ref.sort("文件名")

        assert df["仿真时间（s）"].to_list() == pytest.approx(
            [c.resolved_sim_time() for c in dataset_no_time.cases]
        )

    async def test_采样间隔由仿真时间与行数反推(self, loaded_no_time, dataset_no_time):
        df = loaded_no_time.df_ref.sort("文件名")
        expected = [
            c.resolved_sim_time() / (c.row_count - 1) for c in dataset_no_time.cases
        ]

        assert df["采样间隔（s）"].to_list() == pytest.approx(expected)

    async def test_数据里没有_Time_列(self, loaded_no_time):
        assert "Time[s]" not in loaded_no_time.df_all.columns


# ─── 错误与重复调用 ──────────────────────────────────────────

class TestPreProcessingErrors:
    async def test_频次表与载荷文件不匹配(self, tmp_path, config):
        dataset = build_dataset(
            tmp_path,
            [ramp_case("c1"), ramp_case("c2")],
            freq_names=["c1", "c3"],
        )
        instance = CalSimpleLoad()
        instance.setInit(paths=dataset.path_config(), header=dataset.header, config=config)

        with pytest.raises(ValueError, match="频次表与载荷文件不匹配"):
            await instance.simple_Pre_processing()

    async def test_频次表本身有问题时抛_FileParseError(self, tmp_path, config):
        dataset = build_dataset(tmp_path, [ramp_case("c1")], freq_names=["  "])
        instance = CalSimpleLoad()
        instance.setInit(paths=dataset.path_config(), header=dataset.header, config=config)

        with pytest.raises(FileParseError, match="频次表第一列存在空文件名"):
            await instance.simple_Pre_processing()

    async def test_载荷文件夹为空(self, tmp_path, config):
        dataset = build_dataset(tmp_path, [ramp_case("c1")])
        dataset.txt_path("c1").unlink()
        instance = CalSimpleLoad()
        instance.setInit(paths=dataset.path_config(), header=dataset.header, config=config)

        with pytest.raises(ValueError, match="缺失 1 个 txt：c1"):
            await instance.simple_Pre_processing()

    async def test_重复预处理会丢弃上一次的数据(self, loaded, monkeypatch):
        first_df = loaded.df_all
        seen = []

        original = file_reader.read_all_txt_files

        async def spy(*args, **kwargs):
            seen.append(1)
            return await original(*args, **kwargs)

        monkeypatch.setattr(
            "app_simpleLoad.module.cal_simpleLoad.read_all_txt_files", spy
        )

        await loaded.simple_Pre_processing()

        assert seen == [1]                       # 重新读了一次文件
        assert loaded.df_all is not first_df     # 换成了新对象
        assert loaded.df_all.height == first_df.height


# ─── 进度推送 ────────────────────────────────────────────────

class TestPreProcessingProgress:
    async def test_通过_WebSocket_推送读取进度(
        self, instance, connected_ws, instant_sleep
    ):
        from app_simpleLoad.core import progress as progress_module

        instant_sleep(progress_module)

        await instance.simple_Pre_processing()

        assert "开始处理 2 个文件..." in connected_ws.texts
        assert "已处理 2/2 个文件" in connected_ws.texts
        assert connected_ws.progresses[-1] == pytest.approx(100.0)
