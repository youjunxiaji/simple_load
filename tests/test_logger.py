"""app_simpleLoad/core/logger.py —— 统一日志格式"""

import io
import logging

import pytest
from rich.console import Console

from app_simpleLoad.core import logger as logger_module
from app_simpleLoad.core.logger import RichConsoleHandler, get_logger, setup_logging


@pytest.fixture
def captured_console(monkeypatch) -> io.StringIO:
    """把 Rich 输出接到内存里，方便断言。"""
    buffer = io.StringIO()
    monkeypatch.setattr(
        logger_module, "console", Console(file=buffer, width=200, no_color=True, force_terminal=False)
    )
    return buffer


def emit(record_kwargs: dict, handler: RichConsoleHandler | None = None) -> None:
    handler = handler or RichConsoleHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.emit(logging.LogRecord(**record_kwargs))


def make_record(**overrides) -> dict:
    base = dict(
        name="app_simpleLoad.module.cal_simpleLoad",
        level=logging.INFO,
        pathname="/abs/path/cal_simpleLoad.py",
        lineno=42,
        msg="载荷缩减完成",
        args=(),
        exc_info=None,
    )
    base.update(overrides)
    return base


class TestGetLogger:
    def test_按名字返回_logger(self):
        assert get_logger("a.b").name == "a.b"
        assert isinstance(get_logger("a.b"), logging.Logger)


class TestSetupLogging:
    def test_只挂一个_handler(self):
        setup_logging()
        setup_logging()

        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], RichConsoleHandler)

    @pytest.mark.parametrize(
        ("debug", "expected"), [(False, logging.INFO), (True, logging.DEBUG)]
    )
    def test_按_debug_设置级别(self, debug, expected):
        setup_logging(debug=debug)

        assert logging.getLogger().level == expected
        assert logging.getLogger().handlers[0].level == expected

    def test_压低第三方库日志(self):
        setup_logging()

        for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "websockets"):
            assert logging.getLogger(name).level == logging.WARNING


class TestRichConsoleHandler:
    def test_输出时间级别位置与消息(self, captured_console):
        emit(make_record())

        output = captured_console.getvalue()
        assert "INFO" in output
        assert "cal_simpleLoad.py:42" in output      # 只显示文件名，不显示完整路径
        assert "载荷缩减完成" in output
        assert "/abs/path" not in output

    def test_可关闭位置显示(self, captured_console):
        emit(make_record(), RichConsoleHandler(show_path=False))

        assert "cal_simpleLoad.py:42" not in captured_console.getvalue()

    @pytest.mark.parametrize(
        "level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
    )
    def test_各级别都能输出(self, captured_console, level):
        emit(make_record(level=level, msg="消息"))

        assert logging.getLevelName(level) in captured_console.getvalue()

    def test_异常信息附带_traceback(self, captured_console):
        try:
            raise ValueError("炸了")
        except ValueError:
            import sys

            emit(make_record(level=logging.ERROR, msg="出错了", exc_info=sys.exc_info()))

        output = captured_console.getvalue()
        assert "出错了" in output
        assert "ValueError" in output

    def test_格式化失败不抛异常(self, captured_console, monkeypatch):
        """日志本身出问题时不能把业务流程带崩。"""
        handler = RichConsoleHandler()
        errors = []
        monkeypatch.setattr(handler, "handleError", errors.append)
        monkeypatch.setattr(
            logger_module, "console", type("Boom", (), {"print": lambda *a, **k: 1 / 0})()
        )

        handler.emit(logging.LogRecord(**make_record()))

        assert len(errors) == 1
