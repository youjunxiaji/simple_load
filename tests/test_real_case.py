"""真实数据冒烟测试

`测试案例/` 不入库，本地没有就整体跳过。这里只做两件小事：
用真实 txt 验证「标题行 + END_OF_HEADER」的解析约定，
用真实频次表验证文件名归一化后能和 txt 一一对上。
完整流水线（300 个文件、900 万行）不在单元测试里跑。
"""

import os

import pytest

from app_simpleLoad.core.config import ConversionConfig
from app_simpleLoad.services.file_reader import (
    _validate_txt_file_mapping,
    _parse_single_file,
    normalize_load_file_name,
    read_freq_table,
)

HEADER = ["Time[s]", "speed[rpm]", "Mx[KNm]", "My[KNm]", "Mz[KNm]", "Fx[KN]", "Fy[KN]", "Fz[KN]"]
LOAD_FOLDER = "GW-V16-MB-适应性分析载荷-20260210"


@pytest.fixture
def txt_files(real_case_dir) -> list[str]:
    folder = real_case_dir / LOAD_FOLDER
    return sorted(
        os.path.join(root, name)
        for root, _, files in os.walk(folder)
        for name in files
        if name.lower().endswith(".txt")
    )


def test_真实_txt_用_title_row_1_跳过_END_OF_HEADER(txt_files):
    result = _parse_single_file(txt_files[0], HEADER, ConversionConfig(title_row=1), have_time=True)

    assert result.row_count > 1000
    assert result.sim_time == pytest.approx(600.0, rel=1e-3)
    assert result.sample_interval > 0
    assert result.df["speed[rpm]"].null_count() == 0


def test_真实频次表与_txt_文件一一对应(real_case_dir, txt_files):
    df_ref = read_freq_table(str(real_case_dir / f"{LOAD_FOLDER}.xlsx"), have_time=True)
    actual = [normalize_load_file_name(os.path.basename(path)) for path in txt_files]

    _validate_txt_file_mapping(df_ref["文件名"].to_list(), actual)   # 不抛异常即通过

    assert len(actual) == df_ref.height
