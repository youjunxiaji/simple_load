"""API 请求体模型 — 边界层用 Pydantic 校验，业务层只见 dataclass

约定：
1. 字段名保持前端现有写法（`file_path` / `draggableElements` / `conversion_factors` /
   `tableData` / `romax_origin`），这些是对外契约，不要改名；
2. 嵌套结构直接标注成 `core.config` 里的 dataclass —— Pydantic 会校验并构造出真正的
   dataclass 实例，所以 `cal_simpleLoad` 拿到的仍是 dataclass，不依赖 Pydantic；
3. 校验失败由 main.py 的 RequestValidationError 处理器转成
   `{"message": ..., "status": "error"}`，与其他业务错误保持同一种形状。
"""

from typing import Any

from pydantic import BaseModel, Field

from app_simpleLoad.core.config import AxisMapping, ConversionConfig, PathConfig


class HeaderColumn(BaseModel):
    """draggableElements 的一项：txt 中某一列的含义

    不需要的列用「占位符」开头的名字占位，解析时会跳过。
    """

    name: str


class LoadFileRequest(BaseModel):
    """POST /api/load_file"""

    file_path: PathConfig
    draggableElements: list[HeaderColumn] = Field(min_length=1)
    conversion_factors: ConversionConfig = Field(default_factory=ConversionConfig)

    @property
    def header(self) -> list[str]:
        """按 txt 列顺序展开的列名，直接喂给 `CalSimpleLoad.setInit`"""
        return [item.name for item in self.draggableElements]


class DivideIntervalRequest(BaseModel):
    """POST /api/divide_interval"""

    romax_origin: list[AxisMapping] = Field(default_factory=list)


class ReduceLoadRequest(BaseModel):
    """POST /api/reduce_load

    `tableData` 是前端表格的原始形态：每行一个 `{列序号: 边界值}`，
    值可能是字符串也可能是空串（空串表示该格没填），到计算层再转 float。
    `romax_origin` 必须给满三个轴 —— 缩减时要靠 `romax_origin[2]` 决定排除哪个力矩分量。
    """

    tableData: list[dict[str, Any]] = Field(min_length=1)
    romax_origin: list[AxisMapping] = Field(min_length=3)
