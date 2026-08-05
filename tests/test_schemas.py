"""app_simpleLoad/schemas.py —— 请求体模型

重点验证两件事：
1. 前端报文的字段名/形态没变（对外契约）；
2. 校验后交给业务层的是 core.config 里的 dataclass，不是 Pydantic 对象。
"""

import dataclasses

import pytest
from pydantic import ValidationError

from app_simpleLoad.core.config import AxisMapping, ConversionConfig, PathConfig
from app_simpleLoad.schemas import (
    DivideIntervalRequest,
    LoadFileRequest,
    ReduceLoadRequest,
)
from tests import factories

PATHS = {
    "result_folder_save_path": "out",
    "load_file_folder_path": "load",
    "freq_table_path": "freq.xlsx",
}


def load_body(**overrides) -> dict:
    body = {
        "file_path": dict(PATHS),
        "draggableElements": [{"name": name} for name in factories.DEFAULT_HEADER],
        "conversion_factors": dataclasses.asdict(ConversionConfig()),
    }
    body.update(overrides)
    return body


class TestLoadFileRequest:
    def test_解析成内部_dataclass(self):
        request = LoadFileRequest.model_validate(load_body())

        assert isinstance(request.file_path, PathConfig)
        assert isinstance(request.conversion_factors, ConversionConfig)
        assert request.file_path.freq_table_path == "freq.xlsx"

    def test_header_按列顺序展开(self):
        request = LoadFileRequest.model_validate(load_body())

        assert request.header == factories.DEFAULT_HEADER

    def test_占位符列原样保留(self):
        body = load_body(draggableElements=[{"name": "占位符1"}, {"name": "speed[rpm]"}])

        assert LoadFileRequest.model_validate(body).header == ["占位符1", "speed[rpm]"]

    def test_换算参数可缺省(self):
        body = load_body()
        del body["conversion_factors"]

        assert LoadFileRequest.model_validate(body).conversion_factors == ConversionConfig()

    def test_换算参数可只传一部分(self):
        body = load_body(conversion_factors={"title_row": 1, "translate_factor": 6})

        config = LoadFileRequest.model_validate(body).conversion_factors

        assert config.title_row == 1
        assert config.translate_factor == 6.0
        assert config.unit_moment == 1000.0        # 其余走默认值

    def test_数字字符串会被转成数值(self):
        body = load_body(conversion_factors={"title_row": "1", "tol": "1e-4"})

        config = LoadFileRequest.model_validate(body).conversion_factors

        assert config.title_row == 1
        assert config.tol == pytest.approx(1e-4)

    def test_列映射不能为空(self):
        with pytest.raises(ValidationError, match="draggableElements"):
            LoadFileRequest.model_validate(load_body(draggableElements=[]))

    def test_路径缺一项就报错(self):
        body = load_body()
        del body["file_path"]["load_file_folder_path"]

        with pytest.raises(ValidationError, match="load_file_folder_path"):
            LoadFileRequest.model_validate(body)

    def test_多余字段被忽略(self):
        request = LoadFileRequest.model_validate(load_body(未知字段=1))

        assert not hasattr(request, "未知字段")


class TestDivideIntervalRequest:
    def test_坐标映射解析成_AxisMapping(self):
        request = DivideIntervalRequest.model_validate({"romax_origin": factories.ROMAX_ORIGIN})

        assert request.romax_origin == factories.axis_mappings()
        assert all(isinstance(item, AxisMapping) for item in request.romax_origin)

    def test_可以整个不传(self):
        assert DivideIntervalRequest.model_validate({}).romax_origin == []


class TestReduceLoadRequest:
    def test_区间表原样透传(self):
        rows = [{"0": "-100", "1": "0", "2": "100", "3": ""}]

        request = ReduceLoadRequest.model_validate(
            {"tableData": rows, "romax_origin": factories.ROMAX_ORIGIN}
        )

        assert request.tableData == rows        # 空串留给计算层过滤

    def test_坐标映射必须给满三个轴(self):
        with pytest.raises(ValidationError, match="romax_origin"):
            ReduceLoadRequest.model_validate(
                {"tableData": [{"0": "1"}], "romax_origin": factories.ROMAX_ORIGIN[:2]}
            )

    def test_区间表不能为空(self):
        with pytest.raises(ValidationError, match="tableData"):
            ReduceLoadRequest.model_validate(
                {"tableData": [], "romax_origin": factories.ROMAX_ORIGIN}
            )
