"""app_simpleLoad/services/file_reader.py —— 文件读取服务

覆盖：文件名归一化 / 频次表与 txt 的一致性校验 / 单文件解析（单位换算、
占位符、时间信息、各类解析错误）/ 并发读取与进度推送 / 频次表读取。

注意：单位换算在 Polars 里是 float32 除法（内部按倒数乘法实现），
所以数值断言一律用 `pytest.approx`，不做精确相等比较。
"""
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app_simpleLoad.core.config import ConversionConfig, FileParseError
from app_simpleLoad.services import file_reader
from app_simpleLoad.services.file_reader import (
    _duplicate_names,
    _format_file_names,
    _parse_single_file,
    _validate_txt_file_mapping,
    normalize_load_file_name,
    read_all_txt_files,
    read_freq_table,
)
from tests import factories
from tests.factories import CaseSpec, ramp_case, write_freq_table, write_txt
from tests.fakes import RecordingProgress

HEADER = factories.DEFAULT_HEADER


# ─── 文件名归一化 ────────────────────────────────────────────

class TestNormalizeLoadFileName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("case1.txt", "case1"),
            ("  case1.TXT  ", "case1"),
            ("case1", "case1"),
            ("case1.csv", "case1.csv"),      # 非 txt 扩展名原样保留
            ("case1.txt.txt", "case1.txt"),  # 只脱一层
            (123, "123"),                    # Excel 里的数字文件名
            ("载荷 01.txt", "载荷 01"),
        ],
    )
    def test_归一化规则(self, raw, expected):
        assert normalize_load_file_name(raw) == expected


class TestFormatHelpers:
    def test_文件名列表排序后用顿号连接(self):
        assert _format_file_names(["c", "a", "b"]) == "a、b、c"

    def test_超过上限只列前若干个并给出总数(self):
        names = [f"f{i:02d}" for i in range(15)]

        text = _format_file_names(names)

        assert text.startswith("f00、f01")
        assert text.endswith("等 15 个")
        assert "f10" not in text

    def test_自定义上限(self):
        assert _format_file_names(["a", "b", "c"], limit=2) == "a、b 等 3 个"

    def test_找出重复项并排序(self):
        assert _duplicate_names(["b", "a", "b", "c", "a"]) == ["a", "b"]
        assert _duplicate_names(["a", "b"]) == []


# ─── 频次表 ↔ txt 一致性校验 ──────────────────────────────────

class TestValidateTxtFileMapping:
    def test_完全一致时通过(self):
        _validate_txt_file_mapping(["a", "b"], ["b", "a"])  # 顺序无关

    def test_频次表内部重复(self):
        with pytest.raises(ValueError, match="频次表第一列存在重复文件名：a"):
            _validate_txt_file_mapping(["a", "a"], ["a"])

    def test_载荷文件夹内部重复(self):
        with pytest.raises(ValueError, match="载荷文件夹中存在重复 txt 文件名"):
            _validate_txt_file_mapping(["a"], ["a", "a"])

    def test_缺失_txt(self):
        with pytest.raises(ValueError) as excinfo:
            _validate_txt_file_mapping(["a", "b"], ["a"])

        message = str(excinfo.value)
        assert "频次表包含 2 条记录，实际找到 1 个 txt 文件" in message
        assert "缺失 1 个 txt：b" in message
        assert "多余" not in message

    def test_多余_txt(self):
        with pytest.raises(ValueError) as excinfo:
            _validate_txt_file_mapping(["a"], ["a", "b"])

        message = str(excinfo.value)
        assert "多余 1 个 txt：b" in message
        assert "缺失" not in message

    def test_同时缺失与多余(self):
        with pytest.raises(ValueError) as excinfo:
            _validate_txt_file_mapping(["a", "b"], ["a", "c"])

        message = str(excinfo.value)
        assert "缺失 1 个 txt：b" in message
        assert "多余 1 个 txt：c" in message
        assert message.endswith("请检查频次表第一列文件名与载荷文件夹中的 txt 文件名是否一致")


# ─── 单文件解析 ──────────────────────────────────────────────

@pytest.fixture
def config() -> ConversionConfig:
    return ConversionConfig(title_row=0, unit_moment=1000.0, unit_force=1000.0, unit_speed=1.0)


class TestParseSingleFile:
    def test_单位换算与文件名列(self, tmp_path: Path, config):
        path = write_txt(
            tmp_path / "c1.txt",
            HEADER,
            {
                "Time[s]": [0.0, 0.1],
                "speed[rpm]": [10.0, 12.0],
                "Mx[KNm]": [1000.0, 2000.0],
                "My[KNm]": [3000.0, 4000.0],
                "Mz[KNm]": [5000.0, 6000.0],
                "Fx[KN]": [-7000.0, -8000.0],
                "Fy[KN]": [9000.0, 10000.0],
                "Fz[KN]": [11000.0, 12000.0],
            },
        )

        result = _parse_single_file(str(path), HEADER, config, have_time=True)

        assert result.name == "c1"
        assert result.row_count == 2
        # 力矩 / 力都除以 1000
        assert result.df["Mx[KNm]"].to_list() == pytest.approx([1.0, 2.0], rel=1e-6)
        assert result.df["Fx[KN]"].to_list() == pytest.approx([-7.0, -8.0], rel=1e-6)
        # 转速乘以系数（这里为 1）
        assert result.df["speed[rpm]"].to_list() == pytest.approx([10.0, 12.0], rel=1e-6)
        # 每行都带上文件名，供后续 join / 分组
        assert result.df["文件名"].to_list() == ["c1", "c1"]

    def test_转速换算系数生效(self, tmp_path: Path):
        path = write_txt(tmp_path / "c1.txt", HEADER, {"speed[rpm]": [10.0, 20.0], "Time[s]": [0.0, 1.0]})
        config = ConversionConfig(title_row=0, unit_speed=2.5)

        result = _parse_single_file(str(path), HEADER, config, have_time=True)

        assert result.df["speed[rpm]"].to_list() == pytest.approx([25.0, 50.0], rel=1e-6)

    def test_数值列一律为_float32(self, tmp_path: Path, config):
        path = write_txt(tmp_path / "c1.txt", HEADER, {"speed[rpm]": [1.0, 2.0], "Time[s]": [0.0, 1.0]})

        result = _parse_single_file(str(path), HEADER, config, have_time=True)

        numeric = [dtype for name, dtype in result.df.schema.items() if name != "文件名"]
        assert set(numeric) == {pl.Float32}

    def test_占位符列被跳过(self, tmp_path: Path, config):
        header = ["Time[s]", "占位符1", "speed[rpm]", "Mx[KNm]", "My[KNm]", "Mz[KNm]",
                  "Fx[KN]", "Fy[KN]", "Fz[KN]"]
        path = write_txt(
            tmp_path / "c1.txt",
            header,
            {"Time[s]": [0.0, 0.5], "占位符1": [999.0, 999.0], "speed[rpm]": [5.0, 6.0],
             "Mx[KNm]": [1000.0, 1100.0]},
        )

        result = _parse_single_file(str(path), header, config, have_time=True)

        assert "占位符1" not in result.df.columns
        assert result.df["speed[rpm]"].to_list() == pytest.approx([5.0, 6.0], rel=1e-6)

    def test_有时间列时计算仿真时间与采样间隔(self, tmp_path: Path, config):
        path = write_txt(tmp_path / "c1.txt", HEADER,
                         {"Time[s]": [10.0, 10.5, 11.0, 11.5, 12.0], "speed[rpm]": [1.0] * 5})

        result = _parse_single_file(str(path), HEADER, config, have_time=True)

        assert result.sim_time == pytest.approx(2.0)
        assert result.sample_interval == pytest.approx(0.5)

    def test_无时间列时不计算时间信息(self, tmp_path: Path, config):
        path = write_txt(tmp_path / "c1.txt", HEADER, {"Time[s]": [0.0, 1.0], "speed[rpm]": [1.0, 2.0]})

        result = _parse_single_file(str(path), HEADER, config, have_time=False)

        assert result.sim_time is None
        assert result.sample_interval is None

    def test_文件名去掉_txt_后缀(self, tmp_path: Path, config):
        path = write_txt(tmp_path / "DLC1.1_case01.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})

        result = _parse_single_file(str(path), HEADER, config, have_time=False)

        assert result.name == "DLC1.1_case01"

    def test_无标题行时_title_row_为_None(self, tmp_path: Path):
        path = write_txt(tmp_path / "c1.txt", HEADER,
                         {"speed[rpm]": [3.0, 4.0], "Mx[KNm]": [1000.0, 2000.0]},
                         title_line=False)
        config = ConversionConfig(title_row=None)

        result = _parse_single_file(str(path), HEADER, config, have_time=False)

        assert result.row_count == 2
        assert result.df["Mx[KNm]"].to_list() == pytest.approx([1.0, 2.0], rel=1e-6)

    def test_标题行之外还有说明行时用_title_row_跳过(self, tmp_path: Path):
        """真实数据里标题行后面还有一行 END_OF_HEADER，用 title_row=1 跳过。"""
        path = write_txt(tmp_path / "c1.txt", HEADER,
                         {"speed[rpm]": [3.0, 4.0], "Mx[KNm]": [1000.0, 2000.0]},
                         extra_lines=["END_OF_HEADER"])
        config = ConversionConfig(title_row=1)

        result = _parse_single_file(str(path), HEADER, config, have_time=False)

        assert result.row_count == 2
        assert result.df["Mx[KNm]"].to_list() == pytest.approx([1.0, 2.0], rel=1e-6)

    def test_列数不匹配时提示检查标题行(self, tmp_path: Path, config):
        path = tmp_path / "c1.txt"
        path.write_text("a b c\n1 2 3\n", encoding="utf-8")

        with pytest.raises(FileParseError) as excinfo:
            _parse_single_file(str(path), HEADER, config, have_time=False)

        assert excinfo.value.filename == "c1.txt"
        assert "请检查 标题行 配置是否与文件列数匹配" in str(excinfo.value)

    def test_出现非数值时报错(self, tmp_path: Path, config):
        path = tmp_path / "c1.txt"
        path.write_text("\t".join(HEADER) + "\n" + "\t".join(["abc"] * len(HEADER)) + "\n",
                        encoding="utf-8")

        with pytest.raises(FileParseError) as excinfo:
            _parse_single_file(str(path), HEADER, config, have_time=False)

        assert "could not convert string to float" in str(excinfo.value)

    def test_文件不存在时包装成_FileParseError(self, tmp_path: Path, config):
        with pytest.raises(FileParseError) as excinfo:
            _parse_single_file(str(tmp_path / "missing.txt"), HEADER, config, have_time=False)

        assert excinfo.value.filename == "missing.txt"


# ─── 并发读取整个文件夹 ──────────────────────────────────────

class TestReadAllTxtFiles:
    async def test_合并所有文件并返回逐文件结果(self, tmp_path: Path, config):
        for name in ("c1", "c2", "c3"):
            write_txt(tmp_path / f"{name}.txt", HEADER,
                      {"Time[s]": [0.0, 0.1], "speed[rpm]": [1.0, 2.0]})

        df_all, results = await read_all_txt_files(str(tmp_path), HEADER, config, have_time=True)

        assert df_all.height == 6
        assert sorted(df_all["文件名"].unique().to_list()) == ["c1", "c2", "c3"]
        # 完成顺序不确定，断言集合而非顺序
        assert sorted(r.name for r in results) == ["c1", "c2", "c3"]
        assert all(r.row_count == 2 for r in results)

    async def test_递归扫描子目录(self, tmp_path: Path, config):
        write_txt(tmp_path / "DLC12" / "a.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})
        write_txt(tmp_path / "DLC64" / "b.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})

        df_all, results = await read_all_txt_files(str(tmp_path), HEADER, config, have_time=False)

        assert sorted(r.name for r in results) == ["a", "b"]
        assert df_all.height == 4

    async def test_忽略非_txt_文件(self, tmp_path: Path, config):
        write_txt(tmp_path / "a.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})
        (tmp_path / "readme.md").write_text("不是载荷文件", encoding="utf-8")
        (tmp_path / "b.csv").write_text("1,2", encoding="utf-8")

        _, results = await read_all_txt_files(str(tmp_path), HEADER, config, have_time=False)

        assert [r.name for r in results] == ["a"]

    async def test_大写扩展名也识别(self, tmp_path: Path, config):
        write_txt(tmp_path / "a.TXT", HEADER, {"speed[rpm]": [1.0, 2.0]})

        _, results = await read_all_txt_files(str(tmp_path), HEADER, config, have_time=False)

        assert [r.name for r in results] == ["a"]

    async def test_空文件夹报错(self, tmp_path: Path, config):
        with pytest.raises(ValueError, match="载荷文件夹中没有找到 txt 文件"):
            await read_all_txt_files(str(tmp_path), HEADER, config, have_time=False)

    async def test_频次表文件名带_txt_后缀也能匹配(self, tmp_path: Path, config):
        write_txt(tmp_path / "a.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})

        _, results = await read_all_txt_files(
            str(tmp_path), HEADER, config, have_time=False, expected_file_names=["a.txt"]
        )

        assert [r.name for r in results] == ["a"]

    async def test_与频次表不匹配时在读取前就报错(self, tmp_path: Path, config):
        write_txt(tmp_path / "a.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})

        with pytest.raises(ValueError, match="频次表与载荷文件不匹配"):
            await read_all_txt_files(
                str(tmp_path), HEADER, config, have_time=False, expected_file_names=["a", "b"]
            )

    async def test_不同子目录下的同名_txt_视为重复(self, tmp_path: Path, config):
        write_txt(tmp_path / "d1" / "a.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})
        write_txt(tmp_path / "d2" / "a.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})

        with pytest.raises(ValueError, match="载荷文件夹中存在重复 txt 文件名"):
            await read_all_txt_files(
                str(tmp_path), HEADER, config, have_time=False, expected_file_names=["a"]
            )

    async def test_校验先于空文件夹检查(self, tmp_path: Path, config):
        """一个 txt 都没有时，优先给出「和频次表不匹配」这种更具体的提示。"""
        with pytest.raises(ValueError, match="缺失 1 个 txt：a"):
            await read_all_txt_files(
                str(tmp_path), HEADER, config, have_time=False, expected_file_names=["a"]
            )

    async def test_推送开始与逐文件进度(self, tmp_path: Path, config):
        for name in ("c1", "c2"):
            write_txt(tmp_path / f"{name}.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})
        progress = RecordingProgress()

        await read_all_txt_files(str(tmp_path), HEADER, config, have_time=False, progress=progress)

        assert progress.texts[0] == "开始处理 2 个文件..."
        assert progress.texts[-1] == "已处理 2/2 个文件"
        assert progress.progresses[0] == 0
        assert progress.progresses[-1] == pytest.approx(100.0)

    async def test_不传_progress_时不报错(self, tmp_path: Path, config):
        write_txt(tmp_path / "c1.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})

        df_all, _ = await read_all_txt_files(str(tmp_path), HEADER, config, have_time=False)

        assert df_all.height == 2

    async def test_单个文件解析失败会向上抛出(self, tmp_path: Path, config):
        write_txt(tmp_path / "good.txt", HEADER, {"speed[rpm]": [1.0, 2.0]})
        (tmp_path / "bad.txt").write_text("a b\n1 2\n", encoding="utf-8")

        with pytest.raises(FileParseError):
            await read_all_txt_files(str(tmp_path), HEADER, config, have_time=False)


# ─── 频次表读取 ──────────────────────────────────────────────

class TestReadFreqTable:
    def test_按列顺序重命名为标准列名(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", ["a", "b"], [10, 20], [100, 200],
                                columns=("Load Case", "次数", "时长"))

        df = read_freq_table(str(path), have_time=False)

        assert df.columns == ["文件名", "全寿命发生次数", "仿真时间（s）"]
        assert df["文件名"].to_list() == ["a", "b"]
        assert df["全寿命发生次数"].to_list() == [10, 20]

    def test_有时间列时丢弃频次表里的仿真时间(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", ["a"], [10], [100])

        df = read_freq_table(str(path), have_time=True)

        assert df.columns == ["文件名", "全寿命发生次数"]

    def test_文件名去掉_txt_后缀并去空格(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", [" a.txt ", "b"], [1, 2], [1, 2])

        df = read_freq_table(str(path), have_time=False)

        assert df["文件名"].to_list() == ["a", "b"]

    def test_数字文件名转成字符串(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", ["001", "002"], [1, 2], [1, 2])

        df = read_freq_table(str(path), have_time=False)

        assert df["文件名"].to_list() == ["001", "002"]

    def test_空文件名报错(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", ["a", "   "], [1, 2], [1, 2])

        with pytest.raises(FileParseError, match="频次表第一列存在空文件名"):
            read_freq_table(str(path), have_time=False)

    def test_缺失文件名报错(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", ["a", None], [1, 2], [1, 2])

        with pytest.raises(FileParseError, match="频次表第一列存在空文件名"):
            read_freq_table(str(path), have_time=False)

    def test_重复文件名报错并列出重复项(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", ["a", "a.txt"], [1, 2], [1, 2])

        with pytest.raises(FileParseError, match="频次表第一列存在重复文件名：a"):
            read_freq_table(str(path), have_time=False)

    def test_列数不足报错(self, tmp_path: Path):
        path = write_freq_table(tmp_path / "f.xlsx", ["a"], [1], None)

        with pytest.raises(FileParseError) as excinfo:
            read_freq_table(str(path), have_time=False)

        assert excinfo.value.filename == "f.xlsx"

    def test_文件不存在报错(self, tmp_path: Path):
        with pytest.raises(FileParseError) as excinfo:
            read_freq_table(str(tmp_path / "missing.xlsx"), have_time=False)

        assert excinfo.value.filename == "missing.xlsx"


# ─── 线程池 ──────────────────────────────────────────────────

def test_使用模块级线程池而非每次新建():
    """线程池是模块级单例（复用），并发度按 CPU 核数设置。"""
    import os
    from concurrent.futures import ThreadPoolExecutor

    assert isinstance(file_reader._thread_pool, ThreadPoolExecutor)
    assert file_reader._thread_pool._max_workers == (os.cpu_count() or 4)
