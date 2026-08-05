"""app_simpleLoad/core/config.py —— 配置数据类与自定义异常"""
import dataclasses

import polars as pl
import pytest

from app_simpleLoad.core.config import (
    AxisMapping,
    ConversionConfig,
    FileParseError,
    FileResult,
    PathConfig,
)


class TestFileParseError:
    def test_携带文件名与原因(self):
        err = FileParseError(filename="a.txt", reason="列数不匹配")

        assert err.filename == "a.txt"
        assert err.reason == "列数不匹配"
        assert str(err) == "文件 a.txt 解析失败: 列数不匹配"

    def test_是_Exception_子类可被通用捕获(self):
        with pytest.raises(Exception) as excinfo:
            raise FileParseError("a.txt", "boom")

        assert isinstance(excinfo.value, FileParseError)


class TestPathConfig:
    def test_三个路径按位置参数传入(self):
        paths = PathConfig("out", "load", "freq.xlsx")

        assert paths.result_folder_save_path == "out"
        assert paths.load_file_folder_path == "load"
        assert paths.freq_table_path == "freq.xlsx"

    def test_是_dataclass_支持相等比较(self):
        assert PathConfig("a", "b", "c") == PathConfig("a", "b", "c")
        assert dataclasses.is_dataclass(PathConfig)


class TestConversionConfig:
    def test_默认值与前端约定一致(self):
        config = ConversionConfig()

        assert config.title_row == 0
        assert config.unit_moment == 1000.0
        assert config.unit_force == 1000.0
        assert config.unit_speed == 1.0
        assert config.translate_factor == 4.0
        assert config.temperature == 40.0
        assert config.tol == 1e-6

    def test_可由前端请求体直接展开构造(self):
        payload = {
            "title_row": 1,
            "unit_moment": 1.0,
            "unit_force": 1.0,
            "unit_speed": 2.0,
            "translate_factor": 6.0,
            "temperature": 25.0,
            "tol": 1e-4,
        }

        config = ConversionConfig(**payload)

        assert config.title_row == 1
        assert config.translate_factor == 6.0
        assert config.tol == 1e-4

    def test_title_row_允许为_None_表示无标题行(self):
        assert ConversionConfig(title_row=None).title_row is None


class TestAxisMapping:
    @pytest.mark.parametrize(
        ("origin", "axis", "inverted"),
        [("x", "x", False), ("-z", "z", True), ("y", "y", False), ("-x", "x", True)],
    )
    def test_解析原始轴与正负号(self, origin, axis, inverted):
        mapping = AxisMapping(romax="y", origin=origin)

        assert mapping.axis == axis
        assert mapping.inverted is inverted

    def test_相等比较按值(self):
        assert AxisMapping("z", "y") == AxisMapping("z", "y")


class TestFileResult:
    def test_无时间列时时间字段为_None(self):
        result = FileResult("c1", pl.DataFrame({"a": [1.0]}), 1)

        assert result.name == "c1"
        assert result.row_count == 1
        assert result.sim_time is None
        assert result.sample_interval is None

    def test_有时间列时按位置填入时间信息(self):
        result = FileResult("c1", pl.DataFrame({"a": [1.0, 2.0]}), 2, 0.5, 0.5)

        assert result.sim_time == 0.5
        assert result.sample_interval == 0.5
