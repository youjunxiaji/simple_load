"""统一日志模块 — 基于 Rich Console 自定义格式

用法：
    from app_simpleLoad.core.logger import setup_logging, get_logger

    # 应用启动时初始化一次
    setup_logging(debug=False)

    # 各模块获取 logger
    logger = get_logger(__name__)
    logger.info("消息")
    logger.warning("警告")
    logger.error("错误")
"""

import logging
import os
from datetime import datetime

from rich.console import Console
from rich.text import Text
from rich.traceback import Traceback

# 全局 Console 实例
console = Console()

# 日志级别对应的样式
_LEVEL_STYLES = {
    "DEBUG": "dim",
    "INFO": "bold cyan",
    "WARNING": "bold yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold white on red",
}


class RichConsoleHandler(logging.Handler):
    """基于 Rich Console 的自定义日志 Handler

    输出格式：
        2026-02-10 16:54:06 │ INFO     │ main.py:29 │ WebSocket 管理器已初始化
    """

    def __init__(self, show_path: bool = True):
        super().__init__()
        self.show_path = show_path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 时间
            dt = datetime.fromtimestamp(record.created)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")

            # 级别
            level_name = record.levelname
            level_style = _LEVEL_STYLES.get(level_name, "")

            # 文件:行号（取短文件名）
            filename = os.path.basename(record.pathname)
            location = f"{filename}:{record.lineno}"

            # 消息
            message = self.format(record)

            # 拼装 Rich Text
            line = Text()
            line.append(time_str, style="green")
            line.append(" │ ", style="dim")
            line.append(f"{level_name:<6}", style=level_style)
            line.append(" │ ", style="dim")
            if self.show_path:
                line.append(f"{location:<24}", style="cyan")
                line.append("│ ", style="dim")
            line.append(message)

            console.print(line)

            # 如果有异常信息，用 Rich Traceback 美化输出
            if record.exc_info and record.exc_info[0] is not None:
                tb = Traceback.from_exception(*record.exc_info)
                console.print(tb)

        except Exception:
            self.handleError(record)


def setup_logging(debug: bool = False) -> None:
    """初始化全局日志配置（应用启动时调用一次）"""
    level = logging.DEBUG if debug else logging.INFO

    # 清除已有 handler，避免重复
    root = logging.getLogger()
    root.handlers.clear()

    handler = RichConsoleHandler(show_path=True)
    handler.setLevel(level)

    root.setLevel(level)
    root.addHandler(handler)

    # 降低第三方库的日志级别，避免刷屏
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger 实例"""
    return logging.getLogger(name)
